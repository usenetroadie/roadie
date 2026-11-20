"""Fixes from the final review of the beginner fixes."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from roadie import i18n
from roadie.checks import CheckResult
from roadie.ops import backup, doctor, watch
from tests.test_watch_provider import PROVIDER
from tests.test_watch_run import make_env


class BusyTest(unittest.TestCase):
    def test_502_after_auth_with_too_many_connections_is_busy(self):
        from tests.helpers import MockNntp
        from roadie.checks.nntp import check_nntp
        srv = MockNntp(auth_reply="502 Too many connections for your user")
        try:
            result = check_nntp("127.0.0.1", srv.port, False, "u", "p", timeout=5)
        finally:
            srv.close()
        self.assertEqual(result.key, "check.nntp.busy")

    def test_watcher_needs_two_failed_checks(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = make_env(tmp.name)
        env.state.answers["usenet"] = {"providers": [PROVIDER]}
        saved = {}
        down = CheckResult(False, "check.network", {"kind": "timeout"})
        with mock.patch.object(watch.nntp, "check_nntp", return_value=down):
            self.assertEqual(watch.provider_problems(env, saved, 1000), [])  # once: maybe a blip
            self.assertEqual(watch.provider_problems(env, saved, 1300), [])
            self.assertEqual([p.key for p in watch.provider_problems(env, saved, 1000 + 3600)],
                             ["watch.provider_down"])
        with mock.patch.object(watch.nntp, "check_nntp", return_value=CheckResult(False, "check.nntp.busy")):
            self.assertEqual(watch.provider_problems(env, saved, 1000 + 7200), [])

    def test_doctor_busy_is_not_down(self):
        with mock.patch.object(doctor.nntp, "check_nntp", return_value=CheckResult(False, "check.nntp.busy")):
            lines = doctor.provider_lines([dict(PROVIDER)])
        self.assertNotIn(doctor.WARN, [level for level, _ in lines])


class BackupRightsTest(unittest.TestCase):
    def test_backup_folder_closed_on_every_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "backups"
            folder.mkdir(mode=0o755)
            backup.close_folder(folder)
            self.assertEqual(folder.stat().st_mode & 0o777, 0o700)

    def test_publish_skips_symlinks(self):
        from tests.test_backup_publish import PublishLatestTest
        t = PublishLatestTest("test_publish_never_raises")
        t.setUp()
        try:
            real = t.make("roadie-backup-2026-11-19.tar.age")
            link = t.home / "roadie-backup-2026-11-20.tar.age"
            link.symlink_to(real)
            backup.publish_latest(t.env, link)
            self.assertFalse(t.folder.exists() and any(t.folder.iterdir()))
        finally:
            t.doCleanups()


class PageLogTest(unittest.TestCase):
    def test_reads_only_new_lines_and_counts_206(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "access.log"
            log.write_text('x "GET /other HTTP/2.0" 200 1\n')
            seen = {}
            self.assertFalse(watch.downloaded_via_page(log, "b.tar.age", seen))
            pos = seen["log_pos"]
            with open(log, "a") as f:
                f.write('x "GET /backup/b.tar.age HTTP/2.0" 206 1000\n')
            self.assertTrue(watch.downloaded_via_page(log, "b.tar.age", seen))
            self.assertGreater(seen["log_pos"], pos)

    def test_without_sftp_log_the_text_says_so(self):
        self.assertIn("SFTP", i18n.t("watch.backup_not_downloaded_page", file="x", mb=1))


class ProwlarrOrderTest(unittest.TestCase):
    def test_rename_failing_keeps_the_old_indexer(self):
        from tests.test_prowlarr_indexers import A, ProwlarrIndexersTest
        t = ProwlarrIndexersTest("test_add_then_unchanged_does_nothing")
        t.setUp()
        try:
            t.run_step(A)
            t.fake.failing = {"B"}
            from roadie.engine import StepFailed
            with self.assertRaises(StepFailed):
                t.run_step({**A, "name": "B"})
            self.assertIn("A", [i["name"] for i in t.fake.indexers.values()])  # nothing working deleted
        finally:
            t.doCleanups()


if __name__ == "__main__":
    unittest.main()
