"""roadie doctor and the weekly report: numbers from AIOStreams' statistics, single checks."""

import io
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from roadie import i18n
from roadie.checks import CheckResult
from roadie.ops import doctor
from roadie.ops.common import Env
from roadie.paths import Paths
from roadie.secretstore import SecretStore
from roadie.state import State
from roadie.ui import Prompter

NOW = 1_800_000_000.0
PROVIDERS = [{"host": "news.example.com", "port": 563, "tls": True, "username": "u", "password": "p",
              "connections": 20, "role": "primary"},
             {"host": "backup.example.net", "port": 563, "tls": True, "username": "u", "password": "p",
              "connections": 10, "role": "backup"}]
INDEXERS = [{"name": "Indexer A", "url": "https://indexer.example.com", "api_key": "KEY"}]
# the columns doctor reads, as AIOStreams 2.38 creates them
SCHEMA = """
CREATE TABLE stream_bandwidth (hour_ms INTEGER, username TEXT, transport TEXT, bytes INTEGER);
CREATE TABLE usenet_library (nzb_hash TEXT, status TEXT, added_at TIMESTAMP);
CREATE TABLE usenet_provider_metrics (hour_ms INTEGER, provider_id TEXT, articles INTEGER, missing INTEGER, errors INTEGER);
CREATE TABLE usenet_indexer_metrics (hour_ms INTEGER, indexer TEXT, ok INTEGER, degraded INTEGER, failed INTEGER);
"""


def make_db(path: Path, backup_articles=0) -> None:
    recent, old = int((NOW - 3600) * 1000), int((NOW - 30 * 86400) * 1000)
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    con.executemany("INSERT INTO stream_bandwidth VALUES (?,?,?,?)",
                    [(recent, "a", "usenet", 12_500_000_000), (old, "a", "usenet", 99_000_000_000)])
    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(NOW - 86400))
    rows = [(f"h{i}", "failed" if i < 8 else "available", stamp) for i in range(40)]
    con.executemany("INSERT INTO usenet_library VALUES (?,?,?)", rows + [("old", "failed", "2020-01-01 00:00:00")])
    con.executemany("INSERT INTO usenet_provider_metrics VALUES (?,?,?,?,?)",
                    [(recent, "provider1", 99_900, 100, 0), (old, "provider1", 5, 5000, 0)]
                    + ([(recent, "provider2", backup_articles, 0, 0)] if backup_articles else []))
    con.executemany("INSERT INTO usenet_indexer_metrics VALUES (?,?,?,?,?)",
                    [(recent, "Indexer A", 30, 2, 3), (recent, "Indexer B", 1, 0, 9)])
    con.commit()
    con.close()


class UsageTest(unittest.TestCase):
    def setUp(self):
        i18n.set_language("en")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "db.sqlite"

    def test_only_the_last_seven_days_count(self):
        make_db(self.db)
        u = doctor.usage(self.db, NOW)
        self.assertEqual(u.stream_bytes, 12_500_000_000)
        self.assertEqual((u.releases, u.failed), (40, 8))
        self.assertEqual(u.providers, {"provider1": {"articles": 99_900, "missing": 100, "errors": 0}})
        self.assertEqual(u.indexers["Indexer A"], {"ok": 32, "failed": 3})

    def test_unreadable_statistics_are_no_error(self):
        self.assertIsNone(doctor.usage(self.db, NOW))  # no database yet
        self.db.write_text("not a database")
        self.assertIsNone(doctor.usage(self.db, NOW))
        self.assertEqual(doctor.usage_lines(None, PROVIDERS), [("info", i18n.t("doctor.week.unknown"))])

    def test_plain_words(self):
        make_db(self.db)
        lines = doctor.usage_lines(doctor.usage(self.db, NOW), PROVIDERS)
        text = "\n".join(t for _, t in lines)
        self.assertIn("Streamed: 12.5 GB", text)
        self.assertIn("New releases: 40, not playable: 8 (20 %)", text)
        self.assertIn("news.example.com: delivered 99.90 %", text)  # the id became the provider's name
        # the backup never delivered although 100 articles were missing: it does not help
        self.assertIn(("warn", i18n.t("doctor.week.backup_useless", name="backup.example.net", missing=100)), lines)
        self.assertIn(("warn", i18n.t("doctor.week.indexer_bad", name="Indexer B", failed=9, total=10)), lines)
        self.assertNotIn("Indexer A", text)

    def test_backup_that_stepped_in_is_praised(self):
        make_db(self.db, backup_articles=45)
        lines = doctor.usage_lines(doctor.usage(self.db, NOW), PROVIDERS)
        self.assertIn(("ok", i18n.t("doctor.week.backup_helped", name="backup.example.net", articles=45)), lines)
        self.assertFalse(any("does not help" in t for _, t in lines))

    def test_quiet_week(self):
        self.assertEqual(doctor.usage_lines(doctor.Usage(), PROVIDERS), [("info", i18n.t("doctor.week.nothing"))])


class DoctorRunTest(unittest.TestCase):
    def setUp(self):
        i18n.set_language("en")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        paths = Paths(Path(self.tmp.name))
        state = State(paths.state_file)
        state.answers.update({"duckdns": {"subdomain": "example"}, "usenet": {"providers": PROVIDERS},
                              "indexers": INDEXERS})
        self.env = Env(paths, state, SecretStore(paths.secrets_file))
        self.env.secrets.set("ntfy_topic", "topic")

    def run_doctor(self, nntp_ok=True):
        out = io.StringIO()
        ui = Prompter(inp=io.StringIO(""), out=out, secret_reader=lambda p: "")
        bad = CheckResult(False, "check.nntp.bad_login")
        with mock.patch.object(doctor.watch, "collect_problems", return_value=[]), \
                mock.patch.object(doctor.nntp, "check_nntp") as nntp, \
                mock.patch.object(doctor.newznab, "check_newznab",
                                  return_value=CheckResult(True, "check.newznab.ok", {"categories": 5})):
            nntp.side_effect = [CheckResult(True, "check.nntp.ok"), CheckResult(True, "check.nntp.ok") if nntp_ok else bad]
            code = doctor.run(self.env, ui)
        return code, out.getvalue()

    def test_every_provider_and_indexer_is_tested_on_its_own(self):
        code, text = self.run_doctor()
        self.assertEqual(code, 0, text)
        for part in ("news.example.com:", "backup.example.net:", "Indexer A:", i18n.t("doctor.all_ok")):
            self.assertIn(part, text)

    def test_one_bad_login_is_named_and_fails_the_run(self):
        code, text = self.run_doctor(nntp_ok=False)
        self.assertEqual(code, 1)
        self.assertIn("backup.example.net: " + i18n.t("check.nntp.bad_login")[:40], text.replace("\n", " "))
        self.assertIn(i18n.t("doctor.problems"), text)

    def test_weekly_report_is_one_push(self):
        with mock.patch.object(Env, "push", return_value=True) as push:
            self.assertEqual(doctor.report(self.env), 0)
        title, text, priority = push.call_args.args
        self.assertEqual(title, i18n.t("report.title"))
        self.assertIn(i18n.t("report.no_problems"), text)
        self.env.save_json("watch.json", {"problems": {"disk": {"key": "watch.disk_full", "notified_at": 1}}})
        with mock.patch.object(Env, "push", return_value=True) as push:
            doctor.report(self.env)
        self.assertIn(i18n.t("report.problems", count=1), push.call_args.args[1])


if __name__ == "__main__":
    unittest.main()
