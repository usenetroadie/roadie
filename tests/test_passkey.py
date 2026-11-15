import sqlite3
import tempfile
import unittest
from pathlib import Path

from roadie.ops.passkey import fix_discoverable

# Authelia 4.39 (checked in the test container): only the columns used here matter
SCHEMA = "CREATE TABLE webauthn_credentials (id INTEGER PRIMARY KEY AUTOINCREMENT, kid VARCHAR(512) NOT NULL, discoverable BOOLEAN NOT NULL, public_key BLOB NOT NULL)"


class PasskeyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "db.sqlite3"
        con = sqlite3.connect(self.db)
        con.execute(SCHEMA)
        con.executemany("INSERT INTO webauthn_credentials (kid, discoverable, public_key) VALUES (?, ?, x'00')",
                        [("a", 0), ("b", 1), ("c", 0)])
        con.commit()
        con.close()

    def values(self):
        con = sqlite3.connect(self.db)
        rows = con.execute("SELECT discoverable FROM webauthn_credentials ORDER BY id").fetchall()
        con.close()
        return [r[0] for r in rows]

    def test_fix_sets_discoverable(self):
        self.assertEqual(fix_discoverable(self.db), 2)
        self.assertEqual(self.values(), [1, 1, 1])
        self.assertEqual(fix_discoverable(self.db), 0)

    def test_fix_missing_db_is_zero(self):
        self.assertEqual(fix_discoverable(Path(self.tmp.name) / "none.sqlite3"), 0)

    def test_fix_missing_table_is_zero(self):
        other = Path(self.tmp.name) / "other.sqlite3"
        sqlite3.connect(other).close()
        self.assertEqual(fix_discoverable(other), 0)

    def test_fix_locked_db_no_crash(self):
        import roadie.ops.passkey as mod
        real = sqlite3.connect
        mod.sqlite3.connect = lambda p, timeout=5: real(p, timeout=0.1)
        self.addCleanup(setattr, mod.sqlite3, "connect", real)
        lock = real(self.db)
        lock.execute("BEGIN EXCLUSIVE")
        self.addCleanup(lock.close)
        import contextlib
        import io
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(fix_discoverable(self.db), 0)
        self.assertEqual(len(err.getvalue().splitlines()), 1)
        self.assertIn("passkey", err.getvalue())
