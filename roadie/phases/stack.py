"""Stack steps: domain, Docker, config files, containers, indexers, AIOStreams config."""

import hashlib
import importlib
import json
import re
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from roadie import guard, system
from roadie.aiostreams import metadata, people
from roadie.ops.common import aiostreams_missed_prowlarr
from roadie.aiostreams.api import AioClient
from roadie.aiostreams.builder import FALLBACK_TAG, MAIN_TAG, build_user_config, has_fallback, record_presets
from roadie.checks import domain as domain_check, duckdns as duckdns_check
from roadie.engine import Context, Outcome, Step, StepFailed
from roadie.i18n import t
from roadie.ops import statuspage
from roadie.ops.common import Env
from roadie.paths import atomic_write
from roadie.render import authelia, compose, envfiles, traefik
from roadie.render.envfiles import PLAYBACK

DOCKER_PACKAGES = ["docker-ce", "docker-ce-cli", "containerd.io", "docker-compose-plugin"]
# Docker's apt signing key (docs.docker.com/engine/install/ubuntu): TLS alone is not trusted
DOCKER_KEY_FINGERPRINT = "9DC858229FC7DD38854AE2D88D81803C0EBFCD88"
DUCKDNS_UNITS = {
    "/etc/systemd/system/roadie-duckdns.service":
        "[Unit]\nDescription=roadie: keep the DuckDNS address up to date\nAfter=network-online.target\n\n"
        "[Service]\nType=oneshot\nExecStart=/usr/local/bin/roadie duckdns-update\n",
    "/etc/systemd/system/roadie-duckdns.timer":
        "[Unit]\nDescription=roadie: DuckDNS update every 5 minutes\n\n"
        "[Timer]\nOnBootSec=1min\nOnUnitActiveSec=5min\n\n[Install]\nWantedBy=timers.target\n",
}

SMALL_SERVER_MB = 3500  # below this the swap file carries part of the load
PROOF_TITLE = ("movie", "tt0111161")
PROOF_TIMEOUT = 90  # a first search asks the indexers and can be slow


def _test_mode(ctx: Context) -> bool:
    return bool(ctx.answers.data.get("test_mode"))


def _remove_duckdns_timer() -> None:
    """After a switch to an own domain the DuckDNS updater must stop."""
    if not any(Path(p).exists() for p in DUCKDNS_UNITS):
        return
    system.run(["systemctl", "disable", "--now", "roadie-duckdns.timer"], check=False)
    for path in DUCKDNS_UNITS:
        Path(path).unlink(missing_ok=True)
    system.run(["systemctl", "daemon-reload"], check=False)


def duckdns(ctx: Context) -> Outcome | None:
    """The web address: DuckDNS is updated (and kept up to date by a timer); an own domain
    only has to point here already."""
    if _test_mode(ctx):
        return
    own = ctx.answers.data.get("domain")
    if own:
        _remove_duckdns_timer()
        result = domain_check.check_domain(own)
        if not result.ok:
            raise StepFailed("duckdns", t(result.key, **result.params))
        if result.warn:
            return Outcome("warn", note=t(result.key, **result.params), open_key="open.domain_unverified")
        # pages only the owner opens: the setup works without them (AIOStreams reaches
        # AIOMetadata inside the server)
        extra = ("status",) + (("metadata",) if ctx.extra["render"].metadata else ())
        missing = [n for n in domain_check.names(own, extra) if not domain_check.check_domain(own, hosts=(n.split(".")[0],)).ok]
        if missing:
            return Outcome("warn", note=t("domain.optional_missing", names=", ".join(missing)),
                           open_key="open.optional_dns")
        return None
    d = ctx.answers.data["duckdns"]
    result = duckdns_check.check_duckdns(d["subdomain"], d["token"])
    if not result.ok:
        raise StepFailed("duckdns", t(result.key, **result.params))
    for path, content in DUCKDNS_UNITS.items():
        atomic_write(Path(path), content, 0o644)
    system.run(["systemctl", "daemon-reload"])
    system.run(["systemctl", "enable", "--now", "roadie-duckdns.timer"])


def pinned_keyring(download: Path) -> str | None:
    """The pinned Docker key, exported on its own (armored), or None. The download must hold
    exactly that one key: apt's signed-by would trust every key in the file."""
    with tempfile.TemporaryDirectory(prefix="roadie-gpg-") as home:  # leaves root's keyrings alone
        gpg = ["gpg", "--homedir", home, "--batch", "--quiet"]
        system.run([*gpg, "--import", str(download)], check=False)
        lines = system.run([*gpg, "--with-colons", "--list-keys"], check=False).splitlines()
        pubs = [i for i, line in enumerate(lines) if line.startswith("pub:")]
        if len(pubs) != 1:
            return None
        primary = next((line.split(":")[9] for line in lines[pubs[0] + 1:] if line.startswith("fpr:")), None)
        if primary != DOCKER_KEY_FINGERPRINT:
            return None
        out = Path(home) / "docker.asc"
        system.run([*gpg, "--armor", "--output", str(out), "--export", DOCKER_KEY_FINGERPRINT], check=False)
        armored = out.read_text() if out.is_file() else ""
    return armored if "BEGIN PGP PUBLIC KEY BLOCK" in armored else None


def docker_source(os_release: str, arch: str) -> tuple[str, str]:
    """(key URL, apt line) for Docker's repository of this system: Ubuntu or Debian, any
    architecture Docker builds for (amd64, arm64). Docker signs both with the same key."""
    values = dict(re.findall(r'^(\w+)="?([^"\n]*)"?$', os_release, re.M))
    distro, codename = values.get("ID", ""), values.get("VERSION_CODENAME", "")
    if distro not in ("ubuntu", "debian") or not codename or not re.fullmatch(r"[a-z0-9]+", arch or ""):
        raise ValueError(f"no Docker repository for {distro} {codename} {arch}")
    base = f"https://download.docker.com/linux/{distro}"
    return f"{base}/gpg", f"deb [arch={arch} signed-by={{keyring}}] {base} {codename} stable\n"


def docker(ctx: Context) -> None:
    if not system.ok(["docker", "compose", "version"]):
        key_url, apt_line = docker_source(Path("/etc/os-release").read_text(),
                                          system.run(["dpkg", "--print-architecture"]).strip())
        with tempfile.TemporaryDirectory(prefix="roadie-docker-") as tmp:
            download = Path(tmp) / "docker.asc"
            system.run(["curl", "-fsSL", key_url, "-o", str(download)])
            armored = pinned_keyring(download)
        if not armored:  # the downloaded file itself is never used
            raise StepFailed("docker", t("docker.key_mismatch"))
        keyring = Path("/etc/apt/keyrings/docker.asc")
        keyring.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        atomic_write(keyring, armored, 0o644)
        atomic_write(Path("/etc/apt/sources.list.d/docker.list"), apt_line.format(keyring=keyring), 0o644)
        system.run(["apt-get", "update", "-q"])
        system.run(["apt-get", "install", "-y", "-q", *DOCKER_PACKAGES])
    system.run(["systemctl", "enable", "--now", "docker"])


def _secrets(ctx: Context) -> dict:
    s = ctx.secrets
    return {
        "aiostreams_secret_key": s.generate("aiostreams_secret_key", 32, "hex"),
        "aiostreams_admin_password": s.generate("aiostreams_admin_password", 24),
        "aiostreams_playback_password": s.generate("aiostreams_playback_password", 24),
        "prowlarr_api_key": s.generate("prowlarr_api_key", 16, "hex"),
        "authelia_jwt_secret": s.generate("authelia_jwt_secret", 48),
        "authelia_session_secret": s.generate("authelia_session_secret", 48),
        "authelia_storage_key": s.generate("authelia_storage_key", 48),
        "aiometadata_addon_password": s.generate("aiometadata_addon_password", 24),
    }


# Files a running container reads only at start; `compose up -d` does not notice when a
# mounted one changes. Traefik's dynamic/ folder is left out: Traefik watches it live.
RESTART_ON_CHANGE = (("config/authelia/", "authelia"), ("secrets/authelia/", "authelia"),
                     ("env/authelia.env", "authelia"), ("config/traefik/traefik.yml", "traefik"),
                     ("env/aiostreams.env", "aiostreams"), ("env/prowlarr.env", "prowlarr"),
                     ("env/aiometadata.env", "aiometadata"))


def config_hashes(files) -> dict[str, str]:
    """One hash per service over the files it reads only at start."""
    digests: dict[str, "hashlib._Hash"] = {}
    for f in sorted(files, key=lambda f: f.path):
        service = next((s for prefix, s in RESTART_ON_CHANGE if f.path.startswith(prefix)), None)
        if service:
            digests.setdefault(service, hashlib.sha256()).update(f"{f.path}\0{f.content}\0".encode())
    return {service: h.hexdigest() for service, h in digests.items()}


def _fingerprint(value: str) -> str:
    return hashlib.sha256(("roadie:" + value).encode()).hexdigest()[:16]


OWN_RULES = "config/authelia/own-rules.yml"
REJECTED = "_own_rules_rejected"  # sha256 of an own-rules.yml Authelia did not start with


def _own_rules(ctx: Context) -> list[dict]:
    """The user's own Authelia rules; python3-yaml is fetched once if an older install lacks it."""
    path = ctx.paths.stack_dir / OWN_RULES
    if path.is_file():
        try:
            import yaml  # noqa: F401
        except ImportError:  # the packages step only runs on a first setup
            try:
                system.run(["apt-get", "install", "-y", "-q", "python3-yaml"])
            except (system.CommandFailed, OSError, subprocess.SubprocessError):
                pass  # read_own_rules then reports python3-yaml as missing
            importlib.invalidate_caches()
    rejected = ctx.state.answers.get(REJECTED)
    if rejected and path.is_file() and guard._sha(path.read_bytes()) == rejected:
        ctx.ui.warn(t("authelia.own_rules_still_left_out"))  # Authelia did not start with this file
        ctx.extra["own_rules_used"] = False
        return []
    rules, reason = authelia.read_own_rules(path)
    if reason:
        ctx.ui.warn(t("authelia.own_rules_bad", reason=reason))
    ctx.extra["own_rules_used"] = bool(rules)
    return rules


def _retire_old_routes(ctx: Context, files) -> None:
    """Older roadie wrote config/traefik/routes.yml; Traefik now reads the dynamic/ folder, so the
    old file would be ignored. Unchanged: removed. Edited: renamed to a .bak copy (kept for the user)."""
    old = ctx.paths.stack_dir / traefik.OLD_DYNAMIC_FILE
    if not old.is_file():
        return
    new = next((f.content for f in files if f.path == traefik.DYNAMIC_FILE), None)
    if new is None:
        return

    def body(text: str) -> list[str]:
        return [line for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    written = ctx.state.answers.setdefault("_written", {})
    text = old.read_bytes()
    if body(text.decode("utf-8", "replace")) == body(new) or guard._sha(text) == written.get(traefik.OLD_DYNAMIC_FILE):
        old.unlink()
    else:
        copy = guard._copy_path(old)
        old.rename(copy)
        ctx.extra.setdefault("mine_copies", []).append((traefik.OLD_DYNAMIC_FILE, str(copy)))
    written.pop(traefik.OLD_DYNAMIC_FILE, None)


def stack_files(ctx: Context) -> None:
    cfg, secrets, data = ctx.extra["render"], _secrets(ctx), ctx.answers.data
    for value in secrets.values():
        ctx.redact.add(value)
    password = data["auth"]["password"]
    if ctx.secrets.get("authelia_hash_for") != _fingerprint(password):
        pull_image(cfg.images["authelia"])
        ctx.secrets.set("authelia_hash", system.authelia_hash(cfg.images["authelia"], password))
        ctx.secrets.set("authelia_hash_for", _fingerprint(password))
    files = (compose.render_compose(cfg) + traefik.render_traefik(cfg)
             + authelia.render_authelia(cfg, secrets, ctx.secrets.get("authelia_hash"), _own_rules(ctx))
             + envfiles.render_env(cfg, secrets, data["usenet"]["providers"], data.get("tmdb_api_key")))
    stack = ctx.paths.stack_dir
    for sub in ("data/traefik", "data/authelia", "data/aiostreams", "data/prowlarr"):
        (stack / sub).mkdir(parents=True, exist_ok=True)
    statuspage.write(Env(ctx.paths, ctx.state, ctx.secrets))  # its web server wants a file to serve
    stack.chmod(0o700)  # containers write files 0644 (one-time codes, databases): root only
    system.run(["chown", "-R", "1000:1000", str(stack / "data/prowlarr")])
    # users.yml holds the user's password reset and extra users: rewritten only when the login changed
    # (the hash stays in config_hashes: it only changes with the password, so no needless restart)
    keep_users = (stack / "config/authelia/users.yml").is_file() and not {"*", "auth"} & ctx.extra.get("changed", {"*"})
    guard.write_guarded(ctx, [f for f in files if not (keep_users and f.path == "config/authelia/users.yml")], stack)
    _retire_old_routes(ctx, files)  # only now: if writing failed, the old routes keep working
    if not cfg.metadata:  # taken down again: its env file holds the TMDB key. The data folder
        (stack / "env/aiometadata.env").unlink(missing_ok=True)  # stays, in case it comes back
    ctx.extra["config_hashes"] = config_hashes(files)


def _compose(ctx: Context, *args: str, check: bool = True) -> str:
    return system.run(["docker", "compose", *args], cwd=str(ctx.paths.stack_dir), check=check, timeout=1800)


def _override_problem(e: system.CommandFailed) -> None:
    """Compose names the file when the user's own compose.override.yaml cannot be read."""
    if "compose.override.yaml" in e.output:
        raise StepFailed("stack_up", t("stack.override_broken")) from None


def stack_up(ctx: Context) -> None:
    new = ctx.extra.get("config_hashes")
    running = set(_compose(ctx, "ps", "--services", "--status", "running", check=False).split())
    try:
        _compose(ctx, "pull", "-q")
    except system.CommandFailed as e:
        problem = pull_problem(e.output)
        if problem:
            raise StepFailed("stack_up", problem) from None
        _override_problem(e)
        raise
    try:
        _compose(ctx, "up", "-d", "--remove-orphans")
    except system.CommandFailed as e:
        _override_problem(e)
        raise
    if new is None:
        return
    old = ctx.state.answers.get("_config_hashes") or {}
    changed = sorted(s for s in new if s in running and new[s] != old.get(s))
    if changed:  # a fresh start already read the new files
        _compose(ctx, "up", "-d", "--no-deps", "--force-recreate", *changed)
    ctx.state.answers["_config_hashes"] = new
    ctx.state.save()


def _own_services(ctx: Context) -> set[str]:
    """roadie's services: all of them have a health check. Ones the user added in
    compose.override.yaml are left out, they may have none."""
    if "render" in ctx.extra:
        return set(compose.compose_dict(ctx.extra["render"])["services"])
    return set(Env(ctx.paths, ctx.state, ctx.secrets).stack_services())


def _unhealthy(ctx: Context) -> list[str]:
    own, bad = _own_services(ctx), []
    for line in _compose(ctx, "ps", "-a", "--format", "json").splitlines():
        if line.strip().startswith("{"):
            c = json.loads(line)
            name = c.get("Service", "?")
            if own and name not in own:
                continue
            # right after a (re)create Health is "" for a moment, also for one about to crash
            if c.get("State") != "running" or c.get("Health") not in (("healthy",) if own else ("healthy", "")):
                bad.append(name)
    return bad


def _wait_healthy(ctx: Context) -> list[str]:
    """The services still not healthy after up to 7 minutes ([] = all fine)."""
    deadline = time.time() + 420
    while time.time() < deadline:
        if not _unhealthy(ctx):
            return []
        time.sleep(5)
    return _unhealthy(ctx)


def _without_own_rules(ctx: Context) -> bool:
    """Start Authelia again with roadie's rules only. The user's own-rules.yml stays as it is;
    the next run tries it again. False when this run has nothing to render from."""
    if "render" not in ctx.extra or not ctx.secrets.get("authelia_hash"):
        return False
    files = authelia.render_authelia(ctx.extra["render"], _secrets(ctx), ctx.secrets.get("authelia_hash"))
    guard.write_guarded(ctx, [f for f in files if f.path == "config/authelia/configuration.yml"],
                        ctx.paths.stack_dir)
    _compose(ctx, "up", "-d", "--no-deps", "--force-recreate", "authelia")
    ctx.extra["own_rules_used"] = False
    hashes = ctx.state.answers.get("_config_hashes")
    if hashes is not None:  # what runs now: the next run recreates Authelia with the rules again
        hashes["authelia"] = config_hashes(files)["authelia"]
        ctx.state.save()
    return True


def stack_healthy(ctx: Context) -> bool:
    bad = _wait_healthy(ctx)
    if not bad:
        if ctx.extra.get("own_rules_used") and ctx.state.answers.pop(REJECTED, None):
            ctx.state.save()  # a changed file that works: nothing rejected any more
        return True
    if "authelia" in bad and ctx.extra.get("own_rules_used") and _without_own_rules(ctx):
        bad = _wait_healthy(ctx)
        if not bad:
            ctx.ui.warn(t("authelia.own_rules_left_out"))
            rules = ctx.paths.stack_dir / OWN_RULES
            if rules.is_file():  # left out until the file changes: no broken Authelia every run
                ctx.state.answers[REJECTED] = guard._sha(rules.read_bytes())
                ctx.state.save()
            return True
    ctx.ui.fail(t("stack.unhealthy", services=", ".join(bad)))
    if "authelia" in bad and ctx.extra.get("own_rules_used"):
        ctx.ui.hint(t("authelia.own_rules_suspect"))
    return False


def _container_url(ctx: Context, service: str, port: int) -> str:
    return f"http://{system.container_ip(_compose(ctx, 'ps', '-q', service).strip())}:{port}"


def _prowlarr(base: str, key: str, method: str, path: str, body=None):
    req = urllib.request.Request(base + path, method=method, data=json.dumps(body).encode() if body else None,
                                 headers={"X-Api-Key": key, "Content-Type": "application/json"})
    error = None
    try:
        with urllib.request.urlopen(req, timeout=150) as resp:  # Prowlarr tests the indexer first
            return json.loads(resp.read() or b"null")
    except urllib.error.HTTPError as e:
        error = f"Prowlarr answered {e.code}: {e.read(4000).decode('utf-8', 'replace')}"
    except (urllib.error.URLError, OSError):
        error = "Prowlarr did not answer"
    raise RuntimeError(error)


PULL_TROUBLE = ("dial tcp", "connection refused", "tls handshake timeout", "toomanyrequests",
                "no such host", "i/o timeout", "temporary failure in name resolution")


def pull_problem(output: str) -> str | None:
    """A plain explanation when Docker could not download the program images."""
    low = output.lower()
    return t("stack.pull_failed") if any(word in low for word in PULL_TROUBLE) else None


def pull_image(image: str) -> None:
    """docker pull, with a plain explanation when the download servers do not answer."""
    try:
        system.run(["docker", "pull", "-q", image])
    except system.CommandFailed as e:
        problem = pull_problem(e.output)
        if problem:
            raise StepFailed("stack_files", problem) from None
        raise


def prowlarr_problem(reason: str) -> str:
    """Prowlarr's own words for a refused indexer, told plainly."""
    low = reason.lower()
    if "no results" in low:
        return t("prowlarr.no_results")
    if any(w in low for w in ("unable to connect", "timeout", "timed out", "connection refused")):
        return t("prowlarr.no_answer")
    if any(w in low for w in ("unauthorized", "api key", "apikey", "forbidden")):
        return t("prowlarr.bad_key")
    return reason[:200]


def _prowlarr_reason(text: str) -> str:
    """Pull the human-readable messages out of Prowlarr's validation answer."""
    start = text.find("[")
    try:
        items = json.loads(text[start:]) if start >= 0 else []
        return "; ".join(i.get("errorMessage", "") for i in items) or text
    except ValueError:
        return text


def prowlarr_indexers(ctx: Context) -> Outcome | None:
    """Keep Prowlarr in line with the answers: add new indexers, update changed ones, remove
    the ones the wizard added earlier that are gone. Indexers the user added in Prowlarr stay."""
    indexers = ctx.answers.data["indexers"]
    base, key = _container_url(ctx, "prowlarr", 9696), ctx.secrets.get("prowlarr_api_key")
    current = {i["name"]: i for i in _prowlarr(base, key, "GET", "/api/v1/indexer")}
    # Prowlarr masks API keys in its answers: a fingerprint of what we applied tells us what changed
    applied = ctx.state.answers.get("_prowlarr_indexers") or {}
    wanted, changed, failed = {}, False, []
    split = has_fallback(indexers)
    tags = {t["label"]: t["id"] for t in _prowlarr(base, key, "GET", "/api/v1/tag")}
    for label in (MAIN_TAG, FALLBACK_TAG) if split else ():
        if label not in tags:
            tags[label] = _prowlarr(base, key, "POST", "/api/v1/tag", {"label": label})["id"]
    ours = {tags[label] for label in (MAIN_TAG, FALLBACK_TAG) if label in tags}
    rewrite = bool({"*", "indexers"} & ctx.extra.get("changed", {"*"}))
    claimed = {current[ix["name"]]["id"] for ix in indexers if ix["name"] in current}  # matched by name
    for ix in indexers:
        values = {"baseUrl": ix["url"], "apiPath": "/api", "apiKey": ix["api_key"],
                  "baseSettings.queryLimit": ix.get("daily_api_limit"),
                  "baseSettings.grabLimit": ix.get("daily_grab_limit")}
        tag = (FALLBACK_TAG if ix.get("fallback") else MAIN_TAG) if split else None
        # the tag only joins the fingerprint when set: installs without a split see no change
        wanted[ix["name"]] = hashlib.sha256(json.dumps({**values, **({"tag": tag} if tag else {})},
                                                       sort_keys=True).encode()).hexdigest()
        old = current.get(ix["name"])
        if not old:  # the user may have renamed it in Prowlarr: same address, other name
            # (not one the wizard applied under a name that is gone now: that is a rename by the answers)
            old = next((c for c in current.values() if c["id"] not in claimed and c["name"] not in applied and any(
                f["name"] == "baseUrl" and f.get("value") == ix["url"] for f in c.get("fields") or [])), None)
            if old:
                claimed.add(old["id"])
        if not old and ix["name"] in applied and not rewrite:
            wanted[ix["name"]] = applied[ix["name"]]  # the user deleted it: stays deleted
            continue
        if old and applied.get(ix["name"]) == wanted[ix["name"]]:
            continue
        if old:
            entry, method, path = old, "PUT", f"/api/v1/indexer/{old['id']}"
        else:
            entry = next(s for s in _prowlarr(base, key, "GET", "/api/v1/indexer/schema")
                         if s.get("implementation") == "Newznab" and "generic" in s.get("name", "").lower())
            entry.update({"name": ix["name"], "enable": True, "priority": 25,
                          "appProfileId": _prowlarr(base, key, "GET", "/api/v1/appprofile")[0]["id"]})
            method, path = "POST", "/api/v1/indexer"
        for field in entry["fields"]:
            if field["name"] in values:
                field["value"] = values[field["name"]]
        entry["tags"] = [i for i in entry.get("tags") or [] if i not in ours] + ([tags[tag]] if tag else [])
        try:
            _prowlarr(base, key, method, path, entry)
        except RuntimeError as e:  # this one is tried again at the next run; the others go on
            failed.append((ix["name"], prowlarr_problem(_prowlarr_reason(str(e)))))
            if old and ix["name"] in applied:
                wanted[ix["name"]] = applied[ix["name"]]  # keep the working old one
            else:  # not in Prowlarr: counts as never applied, so the next run tries again
                del wanted[ix["name"]]
            continue
        changed = True
    if failed and len(failed) == len(indexers) and not any(n in current for n, _ in failed):
        name, reason = failed[0]  # before removing anything: never leave Prowlarr without an indexer
        raise StepFailed("prowlarr_indexers", t("prowlarr.indexer_failed", name=name, reason=reason))
    for name in set(applied) - set(wanted):
        if name in current and current[name]["id"] not in claimed:
            _prowlarr(base, key, "DELETE", f"/api/v1/indexer/{current[name]['id']}")
            changed = True
    ctx.state.answers["_prowlarr_indexers"] = wanted
    if failed:
        ctx.state.answers["_indexers_missing"] = [n for n, _ in failed]
    else:
        ctx.state.answers.pop("_indexers_missing", None)
    # AIOStreams reads Prowlarr's indexer list only once at startup: after new indexers, or when
    # it started before Prowlarr was ready (then the addon's Indexers field stays empty)
    if changed or aiostreams_missed_prowlarr(_compose(ctx, "ps", "-q", "aiostreams", check=False).strip()):
        _compose(ctx, "restart", "aiostreams")
        if not stack_healthy(ctx):
            raise StepFailed("prowlarr_indexers", t("stack.unhealthy", services="aiostreams"))
    if failed:
        return Outcome("warn", note=t("prowlarr.some_missing", names=", ".join(n for n, _ in failed)),
                       open_key="open.indexers_missing", hints=tuple(f"{n}: {r}" for n, r in failed))
    return None


def metadata_config(ctx: Context) -> None:
    """Create the own AIOMetadata's configuration once, and one for every person with own
    catalogs; later runs leave them to the user."""
    if not ctx.extra["render"].metadata:
        return
    if ctx.secrets.get("aiometadata_uuid") and not metadata.people_without_config(ctx.secrets, ctx.answers.data):
        return
    url = _container_url(ctx, "aiometadata", 3232)
    if not ctx.secrets.get("aiometadata_uuid"):
        password = ctx.secrets.generate("aiometadata_config_password", 24)
        ctx.redact.add(password)
        uuid = metadata.create(url, metadata.metadata_config(ctx.answers.data["content"]), password,
                               ctx.secrets.get("aiometadata_addon_password"))
        ctx.secrets.set("aiometadata_uuid", uuid)
    for password in metadata.ensure_people(url, ctx.secrets, ctx.answers.data,
                                           ctx.secrets.get("aiometadata_addon_password")):
        ctx.redact.add(password)


def _metadata_preset(ctx: Context) -> dict | None:
    uuid = ctx.secrets.get("aiometadata_uuid")
    return metadata.preset(uuid) if uuid and ctx.extra["render"].metadata else None


PRESET_RECORDS = "_preset_options"


def aiostreams_config(ctx: Context) -> None:
    api = AioClient(_container_url(ctx, "aiostreams", 3000))
    playback = f"{PLAYBACK}:{ctx.secrets.get('aiostreams_playback_password')}"
    config = build_user_config(ctx.answers, api.starter_template(), playback, _metadata_preset(ctx))
    password = ctx.secrets.generate("aiostreams_config_password", 24)
    uuid = ctx.secrets.get("aiostreams_config_uuid")
    # an update leaves the config alone; "change settings" writes only what the changed answers feed
    changed = ctx.extra.get("changed", {"*"})  # other entry points: everything, as before
    sections, flags = people.config_parts(changed)
    # what roadie last wrote inside its own addons, so settings changed there by hand stay
    records = ctx.state.answers.setdefault(PRESET_RECORDS, {})
    if uuid:  # with nothing changed only a TMDB/AIOMetadata swap is written; the user's own changes stay
        people.update_keeping_own(api, uuid, password, config, sections, flags["readd"], flags["write"],
                                  record=records.setdefault("main", {}))
    else:
        uuid, encrypted = api.create_user(config, password)
        records["main"] = {}
        record_presets(config.get("presets") or [], config.get("presets") or [], records["main"])
        ctx.secrets.set("aiostreams_config_uuid", uuid)
        ctx.secrets.set("aiostreams_config_encrypted", encrypted)
    for person, result in people.remove_dropped(api, ctx.secrets, ctx.state, ctx.answers.data):
        ctx.ui.warn(t(people.REMOVE_FAILED_TEXT[result], name=person["name"]))
    try:
        people.sync(api, ctx.secrets, ctx.answers.data, config, changed, records)
    finally:  # also the secrets of people created before a failure
        for person in people.people_of(ctx.answers.data):
            for name in people.secret_names(person["id"]):
                if ctx.secrets.get(name):
                    ctx.redact.add(ctx.secrets.get(name))
    ctx.extra["aio_api"] = api


def aiostreams_ok(ctx: Context) -> bool:
    return ctx.extra["aio_api"].manifest_ok(ctx.secrets.get("aiostreams_config_uuid"),
                                            ctx.secrets.get("aiostreams_config_encrypted"))


def proof(ctx: Context) -> Outcome:
    """Ask the addon for the streams of a well-known film and count its catalogs. Never fails
    the setup and never shows the addon address; the result goes into the step line."""
    uuid, encrypted = ctx.secrets.get("aiostreams_config_uuid"), ctx.secrets.get("aiostreams_config_encrypted")
    full = ctx.answers.data if ctx.answers else ctx.state.answers  # state.answers has no secrets
    if not full.get("tmdb_api_key"):  # one step returns one Outcome: this open point is added directly
        ctx.extra.setdefault("open_points", []).append((t("step.proof"), "open.tmdb"))
    try:
        api = AioClient(_container_url(ctx, "aiostreams", 3000), timeout=PROOF_TIMEOUT)
        count = api.stream_count(uuid, encrypted, *PROOF_TITLE)
        catalogs = api.catalog_count(uuid, encrypted) if count > 0 else 0
    except Exception:  # proof is a bonus: no problem here may stop the setup
        count = catalogs = 0
    if count > 0 and catalogs > 0:
        return Outcome("ok", note=t("proof.note_ok_one" if count == 1 else "proof.note_ok", count=count))
    if count > 0:  # streams work, but the app's start page stays empty
        return Outcome("warn", note=t("proof.note_no_catalogs", count=count), open_key="open.catalogs")
    return Outcome("warn", note=t("proof.note_none"), open_key="open.proof", hints=(t("proof.causes"),))


def summary(ctx: Context) -> None:
    """Passwords only on screen when someone asked for them: in an interactive setup or
    "change settings", never in updates or answers-file runs (they may be logged)."""
    from roadie import access, help as helppages
    env = Env(ctx.paths, ctx.state, ctx.secrets)
    access.write_url_files(env)
    statuspage.write(env)
    if not ctx.extra.get("interactive") or ctx.extra.get("update"):
        ctx.ui.info(t("summary.access_later"))
        return
    ctx.ui.heading(t("summary.heading_open" if ctx.extra.get("open_points") else "summary.heading"))
    restored = ctx.state.answers.get("_restored")
    if isinstance(restored, dict) and not restored.get("shown"):  # once, right after the move
        ctx.ui.box(t("summary.restored_title"), [t("summary.restored")])
        restored["shown"] = True
    access.show(env, ctx.ui, mask_login_password=True, essentials_only=True)
    facts = ctx.extra.get("facts")
    if facts is not None and facts.mem_mb < SMALL_SERVER_MB:
        ctx.ui.info(t("summary.small_server"))
    helppages.next_steps(ctx.ui, env)
    ctx.ui.info(t("summary.again"))
    if not ctx.state.answers.get("_feedback_hint_shown"):  # once, not after every change
        from roadie.ops.diagnose import ISSUES_URL
        ctx.ui.info(t("summary.feedback"))
        ctx.ui.copyable(ISSUES_URL)
        ctx.state.answers["_feedback_hint_shown"] = True


def steps() -> list[Step]:
    return [
        Step("duckdns", "step.duckdns", duckdns, always=True),
        Step("docker", "step.docker", docker, verify=lambda c: system.ok(["docker", "info"])),
        Step("stack_files", "step.stack_files", stack_files, always=True),
        Step("stack_up", "step.stack_up", stack_up, verify=stack_healthy, always=True),
        Step("prowlarr_indexers", "step.prowlarr_indexers", prowlarr_indexers, always=True),
        Step("metadata_config", "step.metadata_config", metadata_config, always=True),
        Step("aiostreams_config", "step.aiostreams_config", aiostreams_config, verify=aiostreams_ok, always=True),
        Step("proof", "step.proof", proof, always=True),
    ]


def final_steps() -> list[Step]:
    return [Step("summary", "step.summary", summary, always=True)]
