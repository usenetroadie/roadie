"""Own domain instead of DuckDNS: answers, the DNS check and the setup step."""

import copy
import json
import unittest
from pathlib import Path
from unittest import mock

from roadie import answers as A
from roadie.checks import domain as D

EXAMPLE = json.loads((Path(__file__).parent.parent / "examples/answers.example.json").read_text())


def own(name="example.com"):
    d = copy.deepcopy(EXAMPLE)
    d.pop("duckdns")
    d["domain"] = name
    return d


class DomainAnswersTest(unittest.TestCase):
    def test_own_domain_is_valid_and_normalised(self):
        a = A.Answers.from_dict(own("https://Media.Example.COM/"))
        self.assertEqual(a.data["domain"], "media.example.com")
        self.assertEqual(A.base_domain(a.data), "media.example.com")
        self.assertEqual(A.base_domain(EXAMPLE), "example.duckdns.org")

    def test_bad_domains_refused(self):
        for bad in ("localhost", "exa mple.com", "-bad.com", "a..b", "x" * 64 + ".com", ""):
            self.assertTrue(any(e.startswith("domain") for e in A.validate(own(bad))), bad)

    def test_not_both(self):
        d = own()
        d["duckdns"] = EXAMPLE["duckdns"]
        self.assertTrue(any(e.startswith("duckdns") for e in A.validate(d)))

    def test_render_uses_own_domain(self):
        from roadie.render import build_render_config, load_versions
        cfg = build_render_config(A.Answers.from_dict(own()), load_versions(), 4000, 50)
        self.assertEqual(cfg.host("aiostreams"), "aiostreams.example.com")


class DomainCheckTest(unittest.TestCase):
    def lookup(self, table):
        return lambda name: set(table.get(name, ()))

    def test_all_names_point_here(self):
        table = {f"{h}.example.com": {"203.0.113.10"} for h in D.HOSTS}
        r = D.check_domain("example.com", {"203.0.113.10"}, self.lookup(table))
        self.assertTrue(r.ok)

    def test_behind_nat_the_public_address_counts(self):
        # Oracle Cloud: the network card only has 10.0.0.5; Cloudflare sees 203.0.113.10
        table = {f"{h}.example.com": {"203.0.113.10"} for h in D.HOSTS}
        r = D.check_domain("example.com", {"10.0.0.5", "203.0.113.10"}, self.lookup(table))
        self.assertTrue(r.ok)

    def test_missing_names_listed_with_the_address(self):
        table = {"aiostreams.example.com": {"203.0.113.10"}}
        r = D.check_domain("example.com", {"10.0.0.5", "203.0.113.10"}, self.lookup(table), public="203.0.113.10")
        self.assertFalse(r.ok)
        self.assertEqual(r.key, "check.domain.missing")
        self.assertEqual(r.params["names"], "auth.example.com, prowlarr.example.com")
        self.assertEqual(r.params["ip"], "203.0.113.10")  # the public one, not 10.0.0.5

    def test_pointing_elsewhere(self):
        table = {f"{h}.example.com": {"198.51.100.7"} for h in D.HOSTS}
        r = D.check_domain("example.com", {"203.0.113.10"}, self.lookup(table))
        self.assertFalse(r.ok)
        self.assertEqual(r.key, "check.domain.wrong")
        self.assertEqual(r.params["found"], "198.51.100.7")

    def test_public_address_from_cloudflare_trace(self):
        from roadie.checks.http import HttpError
        resp = mock.Mock(text=lambda: "fl=1\nip=203.0.113.10\nts=1\n")
        with mock.patch.object(D, "http_get", return_value=resp):
            self.assertEqual(D.public_ipv4(), "203.0.113.10")
        with mock.patch.object(D, "http_get", side_effect=HttpError("timeout")):
            self.assertIsNone(D.public_ipv4())


class DomainStepTest(unittest.TestCase):
    def ctx(self, data):
        return mock.Mock(answers=mock.Mock(data=data))

    def test_own_domain_checked_and_duckdns_timer_removed(self):
        from roadie.checks import CheckResult
        from roadie.phases import stack
        with mock.patch.object(stack.domain_check, "check_domain", return_value=CheckResult(True, "check.domain.ok")) as check, \
                mock.patch.object(stack, "_remove_duckdns_timer") as remove, \
                mock.patch.object(stack.duckdns_check, "check_duckdns") as duck:
            stack.duckdns(self.ctx(own()))
        check.assert_called_once_with("example.com")
        remove.assert_called_once()
        duck.assert_not_called()

    def test_wrong_dns_stops_the_step(self):
        from roadie.checks import CheckResult
        from roadie.engine import StepFailed
        from roadie.phases import stack
        bad = CheckResult(False, "check.domain.wrong", {"names": "auth.example.com", "found": "1.2.3.4", "ip": "5.6.7.8"})
        with mock.patch.object(stack.domain_check, "check_domain", return_value=bad), \
                mock.patch.object(stack, "_remove_duckdns_timer"):
            with self.assertRaises(StepFailed):
                stack.duckdns(self.ctx(own()))


if __name__ == "__main__":
    unittest.main()
