"""Encrypted weekly backups with age. Only the public key lives on the server; the private
key stays with the user, so a stolen backup or server cannot be decrypted."""

import contextlib
import datetime
import hashlib
import os
import posixpath
import subprocess
import tarfile
import time
from contextlib import contextmanager
from pathlib import Path

from roadie import system
from roadie.i18n import t
from roadie.ops.common import APP_SERVICES, BACKUP_MARKER, Env
from roadie.paths import atomic_write
from roadie.redact import Redactor

KEEP = 2
HAS_TAR_FILTER = hasattr(tarfile, "data_filter")  # Python 3.12, and 3.11 from 3.11.4
# the program itself is not backed up: a restore must never replace a newer version
EXCLUDES = ["opt/roadie/app", "opt/roadie/app.prev", "opt/roadie/app.new",
            "opt/roadie/stack/data/aiostreams/cache", "opt/roadie/stack/data/aiometadata-redis",
            "opt/roadie/stack/data/traefik/access.log",
            "opt/roadie/stack/data/status/backup"]  # the copy for the status page (never a backup in a backup)
PUBLISHED = "data/status/backup"  # below the stack folder: served by the status page, behind the login
INCLUDES = ["opt/roadie", "etc/roadie"]


def backup_dir(env: Env) -> Path:
    """The admin user's home, so the encrypted file can be downloaded via SFTP."""
    user = env.answers.get("admin_user")
    try:
        home = system.run(["getent", "passwd", user]).split(":")[5]
        return Path(home) / "backups"
    except (system.CommandFailed, IndexError, TypeError):
        return env.paths.backup_dir


def recipient(env: Env) -> str | None:
    return (env.answers.get("backup") or {}).get("age_recipient") or env.answers.get("_age_recipient")


def file_name(now: datetime.datetime, existing: set[str]) -> str:
    name = f"roadie-backup-{now:%Y-%m-%d}.tar.age"
    return name if name not in existing else f"roadie-backup-{now:%Y-%m-%d-%H%M}.tar.age"


def prune(names: list[str], keep: int = KEEP) -> list[str]:
    """Names to delete: everything but the newest `keep`. Sorted without the suffix, so a second
    backup of a day (...-2026-11-15-1030) counts as newer than the first (...-2026-11-15)."""
    archives = sorted((n for n in names if n.startswith("roadie-backup-") and n.endswith(".tar.age")),
                      key=lambda n: n.removesuffix(".tar.age"))
    return archives[:-keep] if keep else archives


def encrypt_tar(target: Path, key: str, root: Path = Path("/"), includes=INCLUDES, excludes=EXCLUDES) -> None:
    """tar | age -> target. Nothing unencrypted is written to disk."""
    tar_cmd = ["tar", "-czf", "-", "-C", str(root), *[f"--exclude={e}" for e in excludes], *includes]
    tmp = target.with_name(target.name + ".part")
    tmp.unlink(missing_ok=True)  # a leftover (or a planted link) is removed, never followed
    with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), "wb") as out:
        tar = subprocess.Popen(tar_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        age = subprocess.Popen(["age", "-r", key], stdin=tar.stdout, stdout=out, stderr=subprocess.PIPE)
        tar.stdout.close()
        age_err = age.communicate()[1]
        tar_err = tar.stderr.read()
        tar.wait()
    if age.returncode != 0 or tar.returncode not in (0, 1):  # tar 1 = a file changed while reading
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"backup failed: {(age_err or tar_err).decode('utf-8', 'replace')[-300:]}")
    tmp.replace(target)


def write_checksum(target: Path) -> None:
    atomic_write(target.with_name(target.name + ".sha256"), f"{sha256(target)}  {target.name}\n", 0o644)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run(env: Env, quiet: bool = False) -> int:
    key = recipient(env)
    if not key:
        print(t("backup.no_key"))
        return 2
    target_dir = backup_dir(env)
    if target_dir.is_symlink():  # root writes here: only into a real folder
        print(t("backup.failed"), str(target_dir))
        return 1
    target_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    close_folder(target_dir)  # an existing 0755 folder too: the published hard link is 0644
    existing = {p.name for p in target_dir.iterdir()}
    target = target_dir / file_name(datetime.datetime.now(), existing)
    running = [s["Service"] for s in env.services() if s.get("State") == "running" and s["Service"] in APP_SERVICES]
    env.save_json(BACKUP_MARKER, {"since": time.time()})  # the watcher does not report the stopped apps
    from roadie.ui import Prompter
    progress = contextlib.nullcontext() if quiet else Prompter().working(t("backup.working"))
    try:
        with progress:
            if running:
                env.compose("stop", *running)
            encrypt_tar(target, key, env.paths.root)
    except Exception as exc:
        env.push(t("backup.title"), t("backup.failed"), 4)
        print(t("backup.failed"), Redactor(env.secrets.values())(f"{type(exc).__name__}: {exc}")[:400])
        return 1
    finally:
        if running:
            env.compose("start", *running, check=False)
        (env.var_dir / BACKUP_MARKER).unlink(missing_ok=True)
    write_checksum(target)
    publish_latest(env, target)
    for old in prune([p.name for p in target_dir.iterdir()]):
        (target_dir / old).unlink(missing_ok=True)
        (target_dir / (old + ".sha256")).unlink(missing_ok=True)
    user = env.answers.get("admin_user")
    system.run(["chown", "-R", f"{user}:{user}", str(target_dir)], check=False)
    size_mb = max(1, target.stat().st_size // 1_000_000)
    env.save_json("backup.json", {"file": str(target), "time": datetime.datetime.now().isoformat(timespec="seconds"),
                                  "size_mb": size_mb})
    message = t("backup.done", file=str(target), mb=size_mb)
    env.push(t("backup.title"), message, 3)
    if not quiet:
        print(message)
    return 0


def close_folder(folder: Path) -> None:
    """Only root and the admin user see the backups (the files are 0644 for the status page)."""
    try:
        folder.chmod(0o700)
    except OSError:
        pass


def publish_latest(env: Env, archive: Path) -> None:
    """The newest backup for the status page (download in the browser, behind the login).
    The file is useless without the private backup key. A hard link costs no space; across
    file systems a copy. Never fails the backup."""
    folder = env.paths.stack_dir / PUBLISHED
    if archive.is_symlink() or not archive.is_file():  # only roadie's own regular file
        return
    try:
        folder.mkdir(mode=0o755, parents=True, exist_ok=True)
        folder.chmod(0o755)
        target = folder / archive.name
        if not target.exists():
            tmp = folder / (archive.name + ".tmp")
            tmp.unlink(missing_ok=True)
            try:
                os.link(archive, tmp)
            except OSError:
                import shutil
                shutil.copyfile(archive, tmp)
            tmp.chmod(0o644)  # the web server runs as nobody; the home folder stays closed (0700)
            tmp.replace(target)
        for other in folder.iterdir():
            if other.name != archive.name:
                other.unlink(missing_ok=True)
    except OSError as exc:
        print(t("backup.publish_failed"), type(exc).__name__)


def published(env: Env) -> Path | None:
    """The backup the status page offers, if any."""
    folder = env.paths.stack_dir / PUBLISHED
    try:
        files = [p for p in folder.iterdir() if p.name.endswith(".tar.age") and p.is_file()]
    except OSError:
        return None
    return max(files, key=lambda p: p.name) if files else None


class UnsafeBackup(Exception):
    def __init__(self, name: str):
        super().__init__(name)
        self.name = "".join(c if c.isprintable() else "?" for c in name)[:120]


PROWLARR_DATA = "opt/roadie/stack/data/prowlarr"  # the only files not owned by root (PUID 1000)


def _below(name: str, top: str) -> bool:
    return name == top or name.startswith(top + "/")


def _member_kind(member: tarfile.TarInfo) -> str:
    """'ok' (restore it), 'skip' (the program: never restored) or 'bad' (refuse the backup).
    Only files and folders below the two wizard folders are restored, owned by root (Prowlarr's
    data: uid 1000): an age key only encrypts, it does not prove who made the archive."""
    name = member.name
    if not name or name.startswith("/") or posixpath.normpath(name) != name:  # "..", "./", "//"
        return "bad"
    if not any(_below(name, top) for top in INCLUDES):
        return "bad"
    if not (member.isreg() or member.isdir()):  # no links (could point anywhere), no devices
        return "bad"
    # restore runs as root, so this is uid 0; the unit tests run as a normal user
    if member.uid not in (0, os.geteuid()) and not (member.uid == 1000 and _below(name, PROWLARR_DATA)):
        return "bad"
    if any(_below(name, ex) for ex in EXCLUDES[:3]):
        return "skip"  # older backups still contain the program
    return "ok"


def _no_links_on_the_way(root: Path, name: str) -> None:
    """Every part of the path that already exists must be a real file or folder: tar would
    write through a link that was put there before (e.g. opt/roadie -> /etc)."""
    path = root
    for part in name.split("/"):
        path = path / part
        if path.is_symlink():
            raise UnsafeBackup(name)


@contextmanager
def _decrypted(identity: str, archive: str):
    age = subprocess.Popen(["age", "-d", "-i", identity, archive], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    complete = False
    try:
        with tarfile.open(fileobj=age.stdout, mode="r|gz") as tar:
            yield tar
        while age.stdout.read(1 << 16):  # tar may stop before the end: let age finish normally
            pass
        complete = True
    finally:
        if not complete and age.poll() is None:
            age.kill()
        age.stdout.close()
        age.wait()
    if age.returncode != 0:
        raise RuntimeError("age failed")


def _check_members(identity: str, archive: str, root: Path) -> None:
    with _decrypted(identity, archive) as tar:
        for member in tar:
            if _member_kind(member) == "bad":
                raise UnsafeBackup(member.name)
            _no_links_on_the_way(root, member.name)


def _extract(identity: str, archive: str, root: Path) -> None:
    """Second pass: the same check again (the file could have changed), then unpack. The 'tar'
    filter keeps owners (Prowlarr runs as uid 1000) and folder modes (0700), drops setuid bits."""
    with _decrypted(identity, archive) as tar:
        for member in tar:
            kind = _member_kind(member)
            if kind == "bad":
                raise UnsafeBackup(member.name)
            if kind == "ok":
                _no_links_on_the_way(root, member.name)
                if HAS_TAR_FILTER:
                    tar.extract(member, path=str(root), filter="tar", numeric_owner=True)
                else:  # Python < 3.11.4 (Debian 12): the checks above did the filter's work
                    member.mode &= 0o777  # no setuid, setgid or sticky bits
                    tar.extract(member, path=str(root), numeric_owner=True)


def find_uploaded(paths) -> Path | None:
    """The newest roadie backup a user uploaded to this server (home folders and /root)."""
    places = [paths.root / "root", *sorted((paths.root / "home").glob("*"))]
    found = []
    for place in places:
        try:
            found += [p for p in place.glob("roadie-backup-*.tar.age") if p.is_file()]
        except OSError:
            continue
    return max(found, key=lambda p: p.stat().st_mtime) if found else None


def restore_with_key(paths, archive: str, key_text: str, overwrite: bool = False,
                     then_continue: bool = False) -> int:
    """restore() with the private key pasted as text: it lives in a 0600 file only while
    the backup is unpacked and is removed in every case."""
    import tempfile
    run_dir = "/run" if os.access("/run", os.W_OK) else None  # tmpfs: gone at the latest with a reboot
    fd, name = tempfile.mkstemp(prefix="roadie-key-", dir=run_dir)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(key_text.strip() + "\n")
        os.chmod(name, 0o600)
        return restore(paths, archive, name, overwrite=overwrite, then_continue=then_continue)
    finally:
        try:
            os.unlink(name)
        except OSError:
            pass


def restore(paths, archive: str, identity: str, overwrite: bool = False, then_continue: bool = False) -> int:
    """Decrypt and unpack a backup onto this (fresh) server, then let the wizard rebuild
    everything from the restored answers and secrets."""
    import shutil
    from roadie.state import State
    if not Path(archive).is_file() or not Path(identity).is_file():
        print(t("backup.restore_missing"))
        return 2
    if paths.state_file.exists() and not overwrite:  # never replace a running installation by accident
        print(t("backup.restore_exists"))
        return 2
    if not shutil.which("age"):  # a fresh server does not have it yet
        from roadie.ui import Prompter
        try:
            with Prompter().working(t("backup.installing_age")):
                system.run(["apt-get", "update", "-q"])
                system.run(["apt-get", "install", "-y", "-q", "age"])
        except (system.CommandFailed, OSError):
            print(t("backup.restore_failed"))
            return 1
    try:
        _check_members(identity, archive, paths.root)  # first pass: refuse before anything is written
        _extract(identity, archive, paths.root)
    except UnsafeBackup as e:
        print(t("backup.restore_unsafe", name=e.name))
        return 1
    except (tarfile.TarError, OSError, EOFError, RuntimeError):
        print(t("backup.restore_failed"))
        return 1
    state = State(paths.state_file)
    state.done = []  # this machine has to be set up again from the restored answers
    state.answers.pop("_ssh_hardened", None)
    state.answers["_restored"] = {"file": Path(archive).name, "at": time.strftime("%Y-%m-%d %H:%M")}
    state.save()
    # the wizard offered the restore itself and goes on at once: no "now run sudo roadie"
    print(t("backup.restore_continuing" if then_continue else "backup.restore_done"))
    return 0
