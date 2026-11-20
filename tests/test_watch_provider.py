import tempfile
import unittest
from unittest import mock

from roadie import i18n
from roadie.checks import CheckResult
from roadie.ops import selfupdate, watch
from tests.test_watch_run import make_env

PROVIDER = {"host": "news.example.com", "port": 563, "tls": True, "username": "u", "password": "p",
            "connections": 18, "role": "primary"}


class ProviderCheckTest(unittest.TestCase):
    def setUp(self):
        i18n.set_language("en")
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.env = make_env(tmp.name)
        self.env.state.answers["usenet"] = {"providers": [PROVIDER]}
        self.calls = 0

    def check(self, saved, now, result):
        def fake(*args, **kwargs):
            self.calls += 1
            return result
        with mock.patch.object(watch.nntp, "check_nntp", side_effect=fake):
            return watch.provider_problems(self.env, saved, now)

    def test_provider_down_reported(self):
        saved = {}
        self.check(saved, 1000, CheckResult(False, "check.network", {"kind": "timeout"}))
        problems = self.check(saved, 1000 + 3600, CheckResult(False, "check.network", {"kind": "timeout"}))
        self.assertEqual([p.key for p in problems], ["watch.provider_down"])
        saved = {}
        self.check(saved, 1000, CheckResult(False, "check.nntp.bad_login"))
        problems = self.check(saved, 1000 + 3600, CheckResult(False, "check.nntp.bad_login"))
        self.assertEqual([p.key for p in problems], ["watch.provider_login"])

    def test_provider_check_hourly(self):
        saved = {}
        down = CheckResult(False, "check.network", {"kind": "timeout"})
        self.check(saved, 1000, down)
        self.check(saved, 1000 + 3600, down)
        problems = self.check(saved, 1000 + 3900, CheckResult(True, "check.nntp.ok"))  # 5 minutes later
        self.assertEqual(self.calls, 2)  # not asked again: connections are for streaming
        self.assertEqual([p.key for p in problems], ["watch.provider_down"])  # the problem stays
        self.check(saved, 1000 + 7200, CheckResult(True, "check.nntp.ok"))
        self.assertEqual(self.calls, 3)

    def test_provider_502_is_ok(self):
        problems = self.check({}, 1000, CheckResult(False, "check.nntp.refused", {"code": "502"}))
        self.assertEqual(problems, [])  # all connections in use by streaming: not a fault

    def test_every_problem_has_a_resolved_text(self):
        keys = ["watch.container_down", "watch.container_unhealthy", "watch.unreachable", "watch.tls_expiring",
                "watch.indexer_disabled", "watch.disk_full", "watch.ram_low", "watch.provider_down",
                "watch.provider_login"]
        for key in keys:
            self.assertNotEqual(i18n.t(key + ".resolved"), key + ".resolved", key)
            self.assertNotEqual(i18n.t(key + ".fix"), key + ".fix", key)

    def test_resolved_text_reads_as_good_news(self):
        from roadie.ops.watchlogic import Message
        title, text, _ = watch.format_messages([Message("resolved", "watch.container_down",
                                                        {"name": "aiostreams"})])[0]
        self.assertNotIn("not running", text)
        self.assertIn("aiostreams", text)


class NewsTest(unittest.TestCase):
    def test_news_cut_at_word(self):
        with mock.patch.object(selfupdate, "NEWS_CHARS", 20):
            line = selfupdate._plain("A new roadie version shows up in red at every login")
        self.assertTrue(line.endswith("…"))
        self.assertIn(line[:-1].rstrip(), "A new roadie version shows up in red at every login")
        self.assertTrue("A new roadie version shows up in red at every login".startswith(line[:-1].rstrip() + " "))


if __name__ == "__main__":
    unittest.main()
