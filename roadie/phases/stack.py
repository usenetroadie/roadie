"""Stack steps: domain, Docker, config files, containers, indexers, AIOStreams config."""

import hashlib
import json
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from roadie import system
from roadie.aiostreams import people
from roadie.aiostreams.api import AioClient
from roadie.aiostreams.builder import build_user_config
from roadie.checks import duckdns as duckdns_check
from roadie.engine import Context, Outcome, Step, StepFailed
from roadie.i18n import t
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

PROOF_TITLE = ("movie", "tt0111161")
PROOF_TIMEOUT = 90  # a first search asks the indexers and can be slow


def _test_mode(ctx: Context) -> bool:
    return bool(ctx.answers.data.get("test_mode"))


def duckdns(ctx: Context) -> None:
    if _test_mode(ctx):
        return
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


def docker(ctx: Context) -> None:
    if not system.ok(["docker", "compose", "version"]):
        with tempfile.TemporaryDirectory(prefix="roadie-docker-") as tmp:
            download = Path(tmp) / "docker.asc"
            system.run(["curl", "-fsSL", "https://download.docker.com/linux/ubuntu/gpg", "-o", str(download)])
            armored = pinned_keyring(download)
        if not armored:  # the downloaded file itself is never used
            raise StepFailed("docker", t("docker.key_mismatch"))
        keyring = Path("/etc/apt/keyrings/docker.asc")
        keyring.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        atomic_write(keyring, armored, 0o644)
        atomic_write(Path("/etc/apt/sources.list.d/docker.list"),
                     f"deb [arch=amd64 signed-by={keyring}] https://download.docker.com/linux/ubuntu noble stable\n",
                     0o644)
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
    }


# Files a running container reads only at start; `compose up -d` does not notice when a
# mounted one changes. Traefik's routes.yml is left out: Traefik watches it live.
RESTART_ON_CHANGE = (("config/authelia/", "authelia"), ("secrets/authelia/", "authelia"),
                     ("env/authelia.env", "authelia"), ("config/traefik/traefik.yml", "traefik"),
                     ("env/aiostreams.env", "aiostreams"), ("env/prowlarr.env", "prowlarr"))


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


def stack_files(ctx: Context) -> None:
    cfg, secrets, data = ctx.extra["render"], _secrets(ctx), ctx.answers.data
    for value in secrets.values():
        ctx.redact.add(value)
    password = data["auth"]["password"]
    if ctx.secrets.get("authelia_hash_for") != _fingerprint(password):
        system.run(["docker", "pull", "-q", cfg.images["authelia"]])
        ctx.secrets.set("authelia_hash", system.authelia_hash(cfg.images["authelia"], password))
        ctx.secrets.set("authelia_hash_for", _fingerprint(password))
    files = (compose.render_compose(cfg) + traefik.render_traefik(cfg)
             + authelia.render_authelia(cfg, secrets, ctx.secrets.get("authelia_hash"))
             + envfiles.render_env(cfg, secrets, data["usenet"]["providers"]))
    stack = ctx.paths.stack_dir
    for sub in ("data/traefik", "data/authelia", "data/aiostreams", "data/prowlarr"):
        (stack / sub).mkdir(parents=True, exist_ok=True)
    stack.chmod(0o700)  # containers write files 0644 (one-time codes, databases): root only
    system.run(["chown", "-R", "1000:1000", str(stack / "data/prowlarr")])
    system.write_files(files, stack)
    ctx.extra["config_hashes"] = config_hashes(files)


def _compose(ctx: Context, *args: str, check: bool = True) -> str:
    return system.run(["docker", "compose", *args], cwd=str(ctx.paths.stack_dir), check=check, timeout=1800)


def stack_up(ctx: Context) -> None:
    new = ctx.extra.get("config_hashes")
    running = set(_compose(ctx, "ps", "--services", "--status", "running", check=False).split())
    _compose(ctx, "pull", "-q")
    _compose(ctx, "up", "-d", "--remove-orphans")
    if new is None:
        return
    old = ctx.state.answers.get("_config_hashes") or {}
    changed = sorted(s for s in new if s in running and new[s] != old.get(s))
    if changed:  # a fresh start already read the new files
        _compose(ctx, "up", "-d", "--no-deps", "--force-recreate", *changed)
    ctx.state.answers["_config_hashes"] = new
    ctx.state.save()


def _unhealthy(ctx: Context) -> list[str]:
    bad = []
    for line in _compose(ctx, "ps", "-a", "--format", "json").splitlines():
        if line.strip().startswith("{"):
            c = json.loads(line)
            if c.get("State") != "running" or c.get("Health") not in ("healthy", ""):
                bad.append(c.get("Service", "?"))
    return bad


def stack_healthy(ctx: Context) -> bool:
    deadline = time.time() + 420
    while time.time() < deadline:
        if not _unhealthy(ctx):
            return True
        time.sleep(5)
    ctx.ui.fail(t("stack.unhealthy", services=", ".join(_unhealthy(ctx))))
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


def _prowlarr_reason(text: str) -> str:
    """Pull the human-readable messages out of Prowlarr's validation answer."""
    start = text.find("[")
    try:
        items = json.loads(text[start:]) if start >= 0 else []
        return "; ".join(i.get("errorMessage", "") for i in items) or text
    except ValueError:
        return text


def prowlarr_indexers(ctx: Context) -> None:
    """Keep Prowlarr in line with the answers: add new indexers, update changed ones, remove
    the ones the wizard added earlier that are gone. Indexers the user added in Prowlarr stay."""
    indexers = ctx.answers.data["indexers"]
    base, key = _container_url(ctx, "prowlarr", 9696), ctx.secrets.get("prowlarr_api_key")
    current = {i["name"]: i for i in _prowlarr(base, key, "GET", "/api/v1/indexer")}
    # Prowlarr masks API keys in its answers: a fingerprint of what we applied tells us what changed
    applied = ctx.state.answers.get("_prowlarr_indexers") or {}
    wanted, changed = {}, False
    for ix in indexers:
        values = {"baseUrl": ix["url"], "apiPath": "/api", "apiKey": ix["api_key"],
                  "baseSettings.queryLimit": ix.get("daily_api_limit"),
                  "baseSettings.grabLimit": ix.get("daily_grab_limit")}
        wanted[ix["name"]] = hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()
        old = current.get(ix["name"])
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
        try:
            _prowlarr(base, key, method, path, entry)
        except RuntimeError as e:
            raise StepFailed("prowlarr_indexers", t("prowlarr.indexer_failed", name=ix["name"],
                                                    reason=_prowlarr_reason(str(e)))) from None
        changed = True
    for name in set(applied) - set(wanted):
        if name in current:
            _prowlarr(base, key, "DELETE", f"/api/v1/indexer/{current[name]['id']}")
            changed = True
    ctx.state.answers["_prowlarr_indexers"] = wanted
    if changed:  # AIOStreams reads Prowlarr's indexer list only once at startup
        _compose(ctx, "restart", "aiostreams")
        if not stack_healthy(ctx):
            raise StepFailed("prowlarr_indexers", t("stack.unhealthy", services="aiostreams"))


def aiostreams_config(ctx: Context) -> None:
    api = AioClient(_container_url(ctx, "aiostreams", 3000))
    playback = f"{PLAYBACK}:{ctx.secrets.get('aiostreams_playback_password')}"
    config = build_user_config(ctx.answers, api.starter_template(), playback)
    password = ctx.secrets.generate("aiostreams_config_password", 24)
    uuid = ctx.secrets.get("aiostreams_config_uuid")
    if uuid:  # keep what the user changed on the website outside the wizard's sections
        people.update_keeping_own(api, uuid, password, config)
    else:
        uuid, encrypted = api.create_user(config, password)
        ctx.secrets.set("aiostreams_config_uuid", uuid)
        ctx.secrets.set("aiostreams_config_encrypted", encrypted)
    for person, result in people.remove_dropped(api, ctx.secrets, ctx.state, ctx.answers.data):
        ctx.ui.warn(t(people.REMOVE_FAILED_TEXT[result], name=person["name"]))
    try:
        people.sync(api, ctx.secrets, ctx.answers.data, config)
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
        return Outcome("ok", note=t("proof.note_ok", count=count))
    if count > 0:  # streams work, but the app's start page stays empty
        return Outcome("warn", note=t("proof.note_no_catalogs", count=count), open_key="open.catalogs")
    return Outcome("warn", note=t("proof.note_none"), open_key="open.proof", hints=(t("proof.causes"),))


def summary(ctx: Context) -> None:
    """Passwords only on screen when someone asked for them: in an interactive setup or
    "change settings", never in updates or answers-file runs (they may be logged)."""
    from roadie import access, help as helppages
    from roadie.ops.common import Env
    env = Env(ctx.paths, ctx.state, ctx.secrets)
    access.write_url_files(env)
    if not ctx.extra.get("interactive") or ctx.extra.get("update"):
        ctx.ui.info(t("summary.access_later"))
        return
    ctx.ui.heading(t("summary.heading_open" if ctx.extra.get("open_points") else "summary.heading"))
    access.show(env, ctx.ui, mask_login_password=True)
    helppages.next_steps(ctx.ui, env)
    ctx.ui.info(t("summary.again"))


def steps() -> list[Step]:
    return [
        Step("duckdns", "step.duckdns", duckdns, always=True),
        Step("docker", "step.docker", docker, verify=lambda c: system.ok(["docker", "info"])),
        Step("stack_files", "step.stack_files", stack_files, always=True),
        Step("stack_up", "step.stack_up", stack_up, verify=stack_healthy, always=True),
        Step("prowlarr_indexers", "step.prowlarr_indexers", prowlarr_indexers, always=True),
        Step("aiostreams_config", "step.aiostreams_config", aiostreams_config, verify=aiostreams_ok, always=True),
        Step("proof", "step.proof", proof, always=True),
    ]


def final_steps() -> list[Step]:
    return [Step("summary", "step.summary", summary, always=True)]
