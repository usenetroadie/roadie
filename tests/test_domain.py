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

    def test_public_address_from_the_second_source(self):
        from roadie.checks.http import HttpError
        plain = mock.Mock(text=lambda: "203.0.113.10\n")
        with mock.patch.object(D, "http_get", side_effect=[HttpError("connect"), plain]):
            self.assertEqual(D.public_ipv4(), "203.0.113.10")

    def test_unknown_public_address_warns_instead_of_blocking(self):
        # behind NAT with no address service reachable: the names resolve, but to an address
        # the network card does not have
        table = {f"{h}.example.com": {"203.0.113.10"} for h in D.HOSTS}
        with mock.patch.object(D, "public_ipv4", return_value=None), \
                mock.patch.object(D, "local_ipv4", return_value={"10.0.0.5"}):
            r = D.check_domain("example.com", lookup=self.lookup(table))
            self.assertTrue(r.ok and r.warn)
            self.assertEqual(r.key, "check.domain.unverified")
            missing = D.check_domain("example.com", lookup=self.lookup({}))
        self.assertEqual((missing.ok, missing.key), (False, "check.domain.missing"))  # still an error


def dns_answer(query: bytes, records) -> bytes:
    """A DNS answer to `query`; records are (type, rdata) with the name as a pointer."""
    import struct
    header = query[:2] + struct.pack(">HHHHH", 0x8400, 1, len(records), 0, 0)
    body = b"".join(b"\xc0\x0c" + struct.pack(">HHIH", rtype, 1, 60, len(data)) + data for rtype, data in records)
    return header + query[12:] + body


class DnsTest(unittest.TestCase):
    def test_query_and_answer(self):
        q = D.dns_query("auth.example.com")
        self.assertEqual(q[2:4], b"\0\0")  # no recursion: the zone's own server is asked
        self.assertIn(b"\x04auth\x07example\x03com\0", q)
        found, alias = D.parse_answer(dns_answer(q, [(D.A, bytes([203, 0, 113, 10])), (D.A, bytes([203, 0, 113, 11]))]))
        self.assertEqual((found, alias), ({"203.0.113.10", "203.0.113.11"}, False))
        self.assertEqual(D.parse_answer(dns_answer(q, [])), (set(), False))  # no such name
        self.assertEqual(D.parse_answer(dns_answer(q, [(D.CNAME, b"\x03cdn\xc0\x11")])), (set(), True))
        with self.assertRaises(ValueError):
            D.parse_answer(b"\0\1\x84\0\0\1\0\5")

    def test_zone_servers_walk_up_to_the_zone(self):
        def doh(name, rtype):
            if (name, rtype) == ("example.com", "NS"):
                return {"Answer": [{"type": 2, "data": "ns1.example.net."}, {"type": 2, "data": "ns2.example.net."}]}
            if rtype == "A" and name.startswith("ns"):
                return {"Answer": [{"type": 1, "data": "192.0.2." + name[2]}]}
            return {"Status": 3}
        with mock.patch.object(D, "_doh", side_effect=doh):
            self.assertEqual(D.zone_servers("auth.media.example.com"), ["192.0.2.1", "192.0.2.2"])

    def test_resolve_prefers_the_zones_own_server(self):
        """A resolver that still remembers "no such name" must not matter."""
        with mock.patch.object(D, "zone_servers", return_value=["192.0.2.1", "192.0.2.2"]), \
                mock.patch.object(D, "ask_server", side_effect=[OSError("down"), ({"203.0.113.10"}, False)]), \
                mock.patch.object(D, "_doh") as doh, mock.patch.object(D.socket, "getaddrinfo") as system:
            self.assertEqual(D.resolve("auth.example.com"), {"203.0.113.10"})
        doh.assert_not_called()
        system.assert_not_called()

    def test_resolve_trusts_a_clear_no_from_the_zone(self):
        with mock.patch.object(D, "zone_servers", return_value=["192.0.2.1"]), \
                mock.patch.object(D, "ask_server", return_value=(set(), False)), \
                mock.patch.object(D, "_doh") as doh:
            self.assertEqual(D.resolve("auth.example.com"), set())
        doh.assert_not_called()

    def test_resolve_falls_back_for_aliases_and_without_any_server(self):
        from roadie.checks.http import HttpError
        answer = {"Answer": [{"type": 5, "data": "cdn.example.net."}, {"type": 1, "data": "203.0.113.10"}]}
        with mock.patch.object(D, "zone_servers", return_value=["192.0.2.1"]), \
                mock.patch.object(D, "ask_server", return_value=(set(), True)), \
                mock.patch.object(D, "_doh", return_value=answer):
            self.assertEqual(D.resolve("auth.example.com"), {"203.0.113.10"})
        info = [(2, 1, 6, "", ("203.0.113.12", 443))]
        with mock.patch.object(D, "_doh", side_effect=HttpError("connect")), \
                mock.patch.object(D.socket, "getaddrinfo", return_value=info):
            self.assertEqual(D.resolve("auth.example.com"), {"203.0.113.12"})


class DomainStepTest(unittest.TestCase):
    def ctx(self, data, metadata=False):
        from types import SimpleNamespace
        return mock.Mock(answers=mock.Mock(data=data), extra={"render": SimpleNamespace(metadata=metadata)})

    def test_own_domain_checked_and_duckdns_timer_removed(self):
        from roadie.checks import CheckResult
        from roadie.phases import stack
        with mock.patch.object(stack.domain_check, "check_domain", return_value=CheckResult(True, "check.domain.ok")) as check, \
                mock.patch.object(stack, "_remove_duckdns_timer") as remove, \
                mock.patch.object(stack.duckdns_check, "check_duckdns") as duck:
            self.assertIsNone(stack.duckdns(self.ctx(own())))
        self.assertEqual(check.call_args_list[0], mock.call("example.com"))  # the names every setup needs
        remove.assert_called_once()
        duck.assert_not_called()

    def test_missing_optional_names_are_an_open_point_not_a_failure(self):
        from roadie.checks import CheckResult
        from roadie.phases import stack
        asked = []

        def check(domain, hosts=D.HOSTS):
            asked.append(hosts)
            ok = hosts == D.HOSTS
            return CheckResult(ok, "check.domain.ok" if ok else "check.domain.missing", {"domain": domain, "names": "", "ip": ""})
        with mock.patch.object(stack.domain_check, "check_domain", side_effect=check), \
                mock.patch.object(stack, "_remove_duckdns_timer"):
            plain = stack.duckdns(self.ctx(own()))
            self.assertNotIn(("metadata",), asked)  # no AIOMetadata: that name is not needed
            full = stack.duckdns(self.ctx(own(), metadata=True))
        self.assertEqual((plain.status, plain.open_key), ("warn", "open.optional_dns"))
        self.assertIn("status.example.com", plain.note)
        self.assertNotIn("metadata.example.com", plain.note)
        self.assertIn("metadata.example.com", full.note)

    def test_unverified_address_is_a_warning(self):
        from roadie.checks import CheckResult
        from roadie.phases import stack
        unsure = CheckResult(True, "check.domain.unverified", {"domain": "example.com", "found": "1.2.3.4"}, warn=True)
        with mock.patch.object(stack.domain_check, "check_domain", return_value=unsure), \
                mock.patch.object(stack, "_remove_duckdns_timer"):
            outcome = stack.duckdns(self.ctx(own()))
        self.assertEqual((outcome.status, outcome.open_key), ("warn", "open.domain_unverified"))

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
