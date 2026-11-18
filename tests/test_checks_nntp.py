import unittest

from roadie.checks import nntp
from tests.helpers import MockNntp, closed_port


class NntpTest(unittest.TestCase):
    def test_nntp_ok(self):
        srv = MockNntp(users={"alice": "pw1"})
        try:
            r = nntp.check_nntp("127.0.0.1", srv.port, False, "alice", "pw1", timeout=3)
            self.assertTrue(r.ok, r)
        finally:
            srv.close()

    def test_nntp_bad_login(self):
        srv = MockNntp(users={"alice": "pw1"})
        try:
            r = nntp.check_nntp("127.0.0.1", srv.port, False, "alice", "nope", timeout=3)
            self.assertEqual((r.ok, r.key), (False, "check.nntp.bad_login"))
            self.assertNotIn("nope", repr(r.params))
        finally:
            srv.close()

    def test_nntp_refused_greeting(self):
        srv = MockNntp(greeting="502 too many connections")
        try:
            r = nntp.check_nntp("127.0.0.1", srv.port, False, "a", "b", timeout=3)
            self.assertEqual((r.ok, r.key), (False, "check.nntp.refused"))
        finally:
            srv.close()

    def test_nntp_timeout(self):
        srv = MockNntp(silent=True)
        try:
            r = nntp.check_nntp("127.0.0.1", srv.port, False, "a", "b", timeout=1)
            self.assertEqual((r.key, r.params.get("kind")), ("check.network", "timeout"))
        finally:
            srv.close()

    def test_nntp_connect_refused(self):
        r = nntp.check_nntp("127.0.0.1", closed_port(), False, "a", "b", timeout=2)
        self.assertEqual((r.key, r.params.get("kind")), ("check.network", "connect"))

    def test_tls_against_plain_server_is_tls_error(self):
        srv = MockNntp(users={"a": "b"})
        try:
            r = nntp.check_nntp("127.0.0.1", srv.port, True, "a", "b", timeout=3)
            self.assertEqual((r.key, r.params.get("kind")), ("check.network", "tls"))
        finally:
            srv.close()


class BackboneTest(unittest.TestCase):
    def test_lookup_by_id_or_host(self):
        self.assertEqual(nntp.networks_of({"provider": "easynews"}), ["omicron"])
        self.assertEqual(nntp.networks_of({"host": "NEWS.EWEKA.NL"}), ["ewekais"])
        self.assertEqual(nntp.networks_of({"host": "news.unknown.example"}), [])

    def test_same_network_warning(self):
        providers = [{"provider": "easynews", "host": "news.easynews.com", "role": "primary"},
                     {"provider": "newsgroupninja", "host": "news.newsgroup.ninja", "role": "backup"},
                     {"provider": "giganews", "host": "news.giganews.com", "role": "backup"}]
        self.assertEqual(nntp.same_backbone_warnings(providers),
                         [("Newsgroup Ninja", "EasyNews", "network", "Omicron")])

    def test_same_owner_is_a_softer_hint(self):
        providers = [{"provider": "eweka", "host": "news.eweka.nl", "role": "primary"},
                     {"provider": "easynews", "host": "news.easynews.com", "role": "backup"}]
        self.assertEqual(nntp.same_backbone_warnings(providers), [("EasyNews", "Eweka", "owner", "Omicron")])

    def test_multi_network_backup_that_adds_a_network_is_fine(self):
        providers = [{"provider": "giganews", "role": "primary", "host": "news.giganews.com"},
                     {"provider": "newsgroupdirect", "role": "backup", "host": "x"}]
        self.assertEqual(nntp.same_backbone_warnings(providers), [])

    def test_backbone_overview_names_backbones_not_one_provider(self):
        have, other, same = nntp.backbone_overview([{"provider": "eweka", "role": "primary"}])
        self.assertEqual(have, ["Eweka IS"])
        names = [name for name, _ in other]
        self.assertEqual(names, sorted(names, key=str.lower))
        self.assertIn("Abavia", names)
        self.assertNotIn("Eweka IS", names + [name for name, _ in same])
        self.assertIn("Omicron", [name for name, _ in same])  # same company as Eweka IS
        brands = dict(other)["UsenetExpress"]
        self.assertEqual(brands, sorted(brands, key=str.lower))
        self.assertIn("UsenetExpress", brands)

    def test_backbone_overview_unknown_provider_lists_all(self):
        have, other, same = nntp.backbone_overview([{"host": "news.unknown.example", "role": "primary"}])
        self.assertEqual((have, same), ([], []))
        self.assertIn("Omicron", [name for name, _ in other])

    def test_unknown_backbone_no_warning(self):
        providers = [{"host": "news.eweka.nl", "role": "primary"},
                     {"host": "news.unknown.example", "role": "backup"}]
        self.assertEqual(nntp.same_backbone_warnings(providers), [])

    def test_provider_list_well_formed(self):
        data = nntp.provider_data()
        ids = set()
        for p in data["providers"]:
            self.assertTrue({"id", "name", "networks"} <= set(p), p)
            self.assertNotIn(p["id"], ids)
            ids.add(p["id"])
            for net in p["networks"]:
                self.assertIn(net, data["networks"])
            if "host" in p:
                self.assertTrue({"port", "tls"} <= set(p))
        self.assertEqual(len(data["providers"]), 70)
