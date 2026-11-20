import tempfile
import unittest
from pathlib import Path

from roadie.ops import backup
from roadie.ops.common import Env
from roadie.paths import Paths
from roadie.secretstore import SecretStore
from roadie.state import State


class PublishLatestTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        paths = Paths(Path(tmp.name))
        self.env = Env(paths, State(paths.state_file), SecretStore(paths.secrets_file))
        self.home = Path(tmp.name) / "home/admin/backups"
        self.home.mkdir(parents=True, mode=0o700)
        self.folder = paths.stack_dir / "data/status/backup"

    def make(self, name):
        f = self.home / name
        f.write_bytes(b"secret-but-encrypted")
        f.chmod(0o600)
        return f

    def test_publish_latest_copies_and_prunes(self):
        backup.publish_latest(self.env, self.make("roadie-backup-2026-11-12.tar.age"))
        backup.publish_latest(self.env, self.make("roadie-backup-2026-11-19.tar.age"))
        files = sorted(p.name for p in self.folder.iterdir())
        self.assertEqual(files, ["roadie-backup-2026-11-19.tar.age"])
        self.assertEqual((self.folder / files[0]).stat().st_mode & 0o777, 0o644)  # the web server reads it
        self.assertEqual(self.folder.stat().st_mode & 0o777, 0o755)

    def test_publish_never_raises(self):
        backup.publish_latest(self.env, self.home / "missing.tar.age")  # only a hint, never a failed backup

    def test_backup_excludes_published_copy(self):
        self.assertIn("opt/roadie/stack/data/status/backup", backup.EXCLUDES)


if __name__ == "__main__":
    unittest.main()
