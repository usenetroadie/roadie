"""Fixes from the second simulation (failure scenarios)."""
import io
import unittest
from unittest import mock

from roadie import i18n, system
from roadie.checks import CheckResult
from roadie.engine import StepFailed
from roadie.ops import diagnose, doctor
from roadie.phases import stack
from tests.test_questions import FakeChecks, USENET_LINES, USENET_SECRETS, run


class PullInStackFilesTest(unittest.TestCase):
    def test_first_image_download_failure_is_plain(self):
        output = ('Error response from daemon: Head "https://ghcr.io/v2/x": dial tcp [ip]:443: '
                  'connect: connection refused')
        with mock.patch.object(stack.system, "run",
                               side_effect=system.CommandFailed(["docker", "pull", "-q"], 1, output)):
            with self.assertRaises(StepFailed) as e:
                stack.pull_image("ghcr.io/authelia/authelia:4.39.28")
        self.assertEqual(e.exception.message, i18n.t("stack.pull_failed"))


class TextsTest(unittest.TestCase):
    def test_url_error_allows_http(self):
        self.assertIn("http://", i18n.t("answers.err.url"))

    def test_preflight_unreachable_says_what_to_do(self):
        self.assertIn("sudo roadie", i18n.t("preflight.unreachable", hosts="ghcr.io"))

    def test_interrupt_text_is_accurate(self):
        self.assertIn("finished parts", i18n.t("cli.interrupted").lower())

    def test_diagnose_without_services(self):
        self.assertIn(i18n.t("diagnose.no_services"), diagnose.summary_line([], {}, None))


class DoctorProviderTest(unittest.TestCase):
    def test_provider_down_has_one_clear_advice(self):
        with mock.patch.object(doctor.nntp, "check_nntp",
                               return_value=CheckResult(False, "check.network", {"kind": "connect"})):
            lines = doctor.provider_lines([{"host": "news.example.com", "port": 563, "tls": True,
                                            "username": "u", "password": "p"}])
        text = " ".join(t for _, t in lines)
        self.assertNotIn("Check the address", text)
        self.assertIn(i18n.t("doctor.provider_down"), text)


class IndexerRetryTest(unittest.TestCase):
    def test_retry_failing_again_shows_the_choice_again(self):
        down = CheckResult(False, "check.newznab.down")
        lines = list(USENET_LINES)
        lines[12:12] = ["1", "2"]  # try again (still down), then keep it
        lines[16:17] = ["Second", "https://second.example.com", "", "", "n", "y", "y"]
        secrets = list(USENET_SECRETS)
        secrets[3:3] = ["SECONDKEY"]
        data, out, checks = run(lines, secrets, FakeChecks(newznab=[down, down, OK_RESULT]))
        self.assertTrue(data["indexers"][0].get("unchecked"))
        self.assertEqual(out.count(i18n.t("q.indexers.down_q")), 2)

    def test_review_marks_unchecked_indexer(self):
        from roadie.questions import _summary
        data, _, _ = run(USENET_LINES, USENET_SECRETS)
        data["indexers"][0]["unchecked"] = True
        self.assertIn(i18n.t("review.indexer_unchecked"), " ".join(_summary("usenet", data)))


OK_RESULT = CheckResult(True, "check.ok")

if __name__ == "__main__":
    unittest.main()
