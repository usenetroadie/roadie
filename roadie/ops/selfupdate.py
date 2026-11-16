"""Self-update: fetch the newest roadie release from GitHub, check checksum AND signature,
install it next to the running version, test it, swap it in (old one stays as app.prev) and
re-run the command with the new version.

Off until REPO and ALLOWED_SIGNERS are filled in (the public project repo does not exist yet)."""

import hashlib
import http.client
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from roadie import __version__
from roadie.i18n import t
from roadie.notify import _ipv4_only
from roadie.ops.watchlogic import Message

REPO = "usenetroadie/roadie"
ALLOWED_SIGNERS = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIEF/ChmrVlDq7L5y3eDq/y5gOvKXCwadGrK/Nh0FmXZ8"  # public release key; the private one is the RELEASE_SIGNING_KEY secret
API_URL = "https://api.github.com"
DOWNLOAD_URL = "https://github.com"
NAMESPACE, PRINCIPAL = "roadie-release", "roadie"
SKIP_FLAG = "--skip-self-update"  # internal: the re-executed new version must not update itself again
WRAPPER = "usr/local/bin/roadie"
MAX_DOWNLOAD = 50 * 1024 * 1024
CHECK_EVERY = 86400  # watcher: ask GitHub at most once a day
_NET_ERRORS = (OSError, urllib.error.URLError, http.client.HTTPException, ValueError)
_TAG = re.compile(r"v(\d+)\.(\d+)\.(\d+)")
_VERSION = re.compile(r"(\d+)\.(\d+)\.(\d+)(.*)")


def enabled() -> bool:
    return bool(REPO and ALLOWED_SIGNERS)


def _key(version: str):
    m = _VERSION.fullmatch(version.strip())
    if not m:
        return None
    return (int(m[1]), int(m[2]), int(m[3]), 0 if m[4] else 1)  # 0.1.0.dev0 < 0.1.0


def is_newer(tag: str, current: str) -> bool:
    """Only release tags vX.Y.Z count."""
    if not _TAG.fullmatch(tag):
        return False
    cur = _key(current)
    return cur is None or _key(tag[1:]) > cur


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "roadie"})
    with _ipv4_only(), urllib.request.urlopen(req, timeout=30) as resp:
        data = resp.read(MAX_DOWNLOAD + 1)
    if len(data) > MAX_DOWNLOAD:
        raise ValueError("download too large")
    return data


def latest(repo: str) -> str | None:
    """Tag of the newest release, None if unknown."""
    try:
        tag = json.loads(_get(f"{API_URL}/repos/{repo}/releases/latest")).get("tag_name")
    except (*_NET_ERRORS, AttributeError):
        tag = None
    if not isinstance(tag, str):  # the API allows only few anonymous requests per address
        try:  # .../releases/latest redirects to .../releases/tag/<tag>, without such a limit
            m = re.search(r"/releases/tag/(v[0-9.]+)$", _final_url(f"{DOWNLOAD_URL}/{repo}/releases/latest"))
        except _NET_ERRORS:
            return None
        tag = m.group(1) if m else None
    return tag


def _final_url(url: str) -> str:
    """Where the address leads after its redirects."""
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "roadie"})
    with _ipv4_only(), urllib.request.urlopen(req, timeout=30) as resp:
        return resp.geturl()


def verify(archive: Path, sha256_text: str, sig: bytes, allowed_signers: str) -> bool:
    """Checksum must match and the OpenSSH signature must come from the release key."""
    expected = (sha256_text.split() or [""])[0].lower()
    if hashlib.sha256(Path(archive).read_bytes()).hexdigest() != expected:
        return False
    with tempfile.TemporaryDirectory(prefix="roadie-verify-") as tmp:  # created with mode 0700
        signers, sig_file = Path(tmp) / "allowed_signers", Path(tmp) / "archive.sig"
        signers.write_text(f'{PRINCIPAL} namespaces="{NAMESPACE}" {allowed_signers.strip()}\n')
        sig_file.write_bytes(sig)
        try:
            with open(archive, "rb") as data:
                result = subprocess.run(["ssh-keygen", "-Y", "verify", "-f", str(signers), "-I", PRINCIPAL,
                                         "-n", NAMESPACE, "-s", str(sig_file)],
                                        stdin=data, capture_output=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired):
            return False
    return result.returncode == 0


def _package_members(tar: tarfile.TarFile, prefix: str) -> list[tarfile.TarInfo] | None:
    """Members of <prefix>/roadie/ (only these are installed; everything else in the
    archive is ignored). None if one of them is unsafe: absolute, '..', or not a plain file/dir."""
    base = f"{prefix}/roadie"
    out = []
    for m in tar.getmembers():
        if m.name != base and not m.name.startswith(base + "/"):
            continue
        if m.name.startswith("/") or ".." in m.name.split("/") or not (m.isfile() or m.isdir()):
            return None
        out.append(m)
    return out


def _remove(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


def install(archive: Path, app_dir: Path, tag: str) -> str:
    """Unpack the package to app.new, test it, then swap: app -> app.prev, app.new -> app.
    Returns "ok", "unsafe" (archive rejected) or "failed" (new version did not start / disk error).
    On any failure the running version stays untouched."""
    app_dir = Path(app_dir)
    new, prev = app_dir.with_name("app.new"), app_dir.with_name("app.prev")
    prefix = f"roadie-{tag}"
    _remove(new)
    try:
        with tempfile.TemporaryDirectory(prefix="roadie-unpack-") as tmp:
            with tarfile.open(archive, "r:gz") as tar:
                members = _package_members(tar, prefix)
                if not members:
                    return "unsafe"
                if hasattr(tarfile, "data_filter"):
                    tar.extractall(tmp, members=members, filter="data")
                else:  # Python < 3.11.4 (Debian 12): _package_members already refused unsafe ones
                    for m in members:
                        m.mode &= 0o755  # like filter="data": no setuid/setgid/sticky, no group/other write
                    tar.extractall(tmp, members=members)
            package = Path(tmp) / prefix / "roadie"
            if not package.is_dir():
                return "unsafe"
            new.mkdir(mode=0o755)
            shutil.copytree(package, new / "roadie")
        env = dict(os.environ, PYTHONPATH=str(new))
        check = subprocess.run([sys.executable, "-m", "roadie", "--version"], cwd=new, env=env,
                               capture_output=True, text=True, timeout=60)
        if check.returncode != 0 or check.stdout.strip() != f"roadie {tag.removeprefix('v')}":
            _remove(new)
            return "failed"
        _remove(prev)
        os.rename(app_dir, prev)
        try:
            os.rename(new, app_dir)
        except OSError:
            try:
                os.rename(prev, app_dir)
            except OSError as exc:  # the wrapper falls back to app.prev, but say so loudly
                print(f"self-update: could not restore {app_dir} from {prev}: {exc}", file=sys.stderr)
            raise
        return "ok"
    except (OSError, tarfile.TarError, subprocess.TimeoutExpired, ValueError):
        _remove(new)
        return "failed"


def _lang_args(argv: list[str]) -> list[str]:
    for i, arg in enumerate(argv):
        if arg == "--lang" and i + 1 < len(argv):
            return ["--lang", argv[i + 1]]
        if arg.startswith("--lang="):
            return [arg]
    return []


def run(paths, ui, argv: list[str]) -> int | None:
    """None: nothing was installed, the caller goes on with the service updates.
    After a successful install the process is replaced by the new version; an int is returned
    only if that restart failed (or execv is mocked in tests)."""
    if not enabled():
        ui.info(t("selfupdate.disabled"))
        return None
    app_dir = paths.root / "opt/roadie/app"
    if not (app_dir / "roadie").is_dir():
        ui.info(t("selfupdate.not_installed"))
        return None
    tag = latest(REPO)
    if tag is None:
        ui.warn(t("selfupdate.check_failed"))
        return None
    if not is_newer(tag, __version__):
        ui.ok(t("selfupdate.current", version=__version__))
        return None
    ui.info(t("selfupdate.found", version=tag.removeprefix("v")))
    name = f"roadie-{tag}.tar.gz"
    base = f"{DOWNLOAD_URL}/{REPO}/releases/download/{tag}/{name}"
    with tempfile.TemporaryDirectory(prefix="roadie-update-") as tmp:
        archive = Path(tmp) / name
        try:
            archive.write_bytes(_get(base))
            sha = _get(base + ".sha256").decode("utf-8", "replace")
        except _NET_ERRORS:
            ui.warn(t("selfupdate.check_failed"))
            return None
        try:
            sig = _get(base + ".sig")
        except urllib.error.HTTPError as e:
            if e.code != 404:
                ui.warn(t("selfupdate.check_failed"))
                return None
            sig = b""  # missing: an unsigned release is never installed
        except _NET_ERRORS:
            ui.warn(t("selfupdate.check_failed"))
            return None
        if not sig or not verify(archive, sha, sig, ALLOWED_SIGNERS):
            ui.fail(t("selfupdate.untrusted"))
            return None
        result = install(archive, app_dir, tag)
        if result != "ok":
            ui.fail(t("selfupdate.unsafe" if result == "unsafe" else "selfupdate.failed"))
            return None
    ui.ok(t("selfupdate.done", version=tag.removeprefix("v")))
    wrapper = str(paths.root / WRAPPER)
    try:
        os.execv(wrapper, [wrapper, SKIP_FLAG, *_lang_args(argv), "update"])
    except OSError:
        ui.fail(t("selfupdate.restart_failed"))
        return 1
    return 0  # only reached when execv is mocked in tests


def notice(saved: dict, now: float | None = None) -> Message | None:
    """Watcher: one push per new version, GitHub asked at most once a day. Updates `saved`."""
    if not enabled():
        return None
    now = time.time() if now is None else now
    state = saved.setdefault("selfupdate", {})
    if "checked" in state and now - state["checked"] < CHECK_EVERY:
        return None
    state["checked"] = now
    tag = latest(REPO)
    if not tag or not is_newer(tag, __version__) or state.get("notified") == tag:
        return None
    state["notified"] = tag
    return Message("info", "watch.selfupdate_available", {"version": tag.removeprefix("v")})
