import os
import stat
import tempfile
import unittest
from pathlib import Path

from roadie.paths import Paths, atomic_write
from roadie.secretstore import SecretStore


def mode(p):
    return stat.S_IMODE(os.stat(p).st_mode)


class SecretStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.paths = Paths(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_paths_live_under_root(self):
        self.assertEqual(self.paths.secrets_file, Path(self.tmp.name) / "etc/roadie/secrets.json")

    def test_file_mode_is_0600(self):
        s = SecretStore(self.paths.secrets_file)
        s.set("a", "value")
        self.assertEqual(mode(self.paths.secrets_file), 0o600)

    def test_dir_mode_is_0700(self):
        SecretStore(self.paths.secrets_file).set("a", "value")
        self.assertEqual(mode(self.paths.etc_dir), 0o700)

    def test_generate_is_stable(self):
        s = SecretStore(self.paths.secrets_file)
        first = s.generate("key")
        self.assertGreaterEqual(len(first), 32)
        self.assertEqual(s.generate("key"), first)
        self.assertEqual(SecretStore(self.paths.secrets_file).generate("key"), first)

    def test_generate_hex(self):
        value = SecretStore(self.paths.secrets_file).generate("hex", nbytes=16, alphabet="hex")
        self.assertRegex(value, r"^[0-9a-f]{32}$")

    def test_get_missing_is_none(self):
        self.assertIsNone(SecretStore(self.paths.secrets_file).get("nope"))

    def test_atomic_write_leaves_no_tmp(self):
        target = self.paths.etc_dir / "x.txt"
        atomic_write(target, "hello")
        atomic_write(target, "hello2")
        self.assertEqual(target.read_text(), "hello2")
        self.assertEqual(os.listdir(self.paths.etc_dir), ["x.txt"])

    def test_atomic_write_custom_mode(self):
        target = self.paths.root / "pub.txt"
        atomic_write(target, "x", mode=0o644)
        self.assertEqual(mode(target), 0o644)
