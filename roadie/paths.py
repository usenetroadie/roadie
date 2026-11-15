"""System paths (relocatable under a root for tests) and atomic file writes."""

import fcntl
import os
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Paths:
    root: Path = Path("/")

    def _p(self, rel: str) -> Path:
        return self.root / rel

    @property
    def etc_dir(self) -> Path:
        return self._p("etc/roadie")

    @property
    def state_file(self) -> Path:
        return self.etc_dir / "state.json"

    @property
    def secrets_file(self) -> Path:
        return self.etc_dir / "secrets.json"

    @property
    def stack_dir(self) -> Path:
        return self._p("opt/roadie/stack")

    @property
    def lock_file(self) -> Path:
        """/run on the real system (gone after a reboot). Another root (tests) is locked as a
        folder, so no file is left behind."""
        return Path("/run/roadie.lock") if self.root == Path("/") else self.root

    @property
    def backup_dir(self) -> Path:
        return self._p("var/backups/roadie")


def atomic_write(path: Path, data: str, mode: int = 0o600) -> None:
    """Write via temp file + rename; permissions are set before content is written."""
    path = Path(path)
    if not path.parent.exists():
        path.parent.mkdir(mode=0o700, parents=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


class Busy(Exception):
    """Another roadie run that changes the server is still going."""


@contextmanager
def exclusive(lock: Path, wait: float = 0, poll: float = 5):
    """One changing run at a time (setup, update, backup, restore, uninstall). The lock goes away
    with the process, also after a crash. `wait`: seconds to wait for the other run."""
    lock = Path(lock)
    flags = os.O_RDONLY if lock.is_dir() else os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW
    fd = os.open(lock, flags, 0o600)
    try:
        deadline = time.monotonic() + wait
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise Busy() from None
                time.sleep(poll)
        yield
    finally:
        os.close(fd)
