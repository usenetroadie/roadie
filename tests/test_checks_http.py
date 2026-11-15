import unittest

from roadie.checks import duckdns, newznab, tmdb
from roadie.checks.http import HttpError, http_get
from tests.helpers import MockHttp, closed_port

CAPS = ('<?xml version="1.0"?><caps><server title="x"/><limits max="100" default="50"/>'
        '<categories><category id="2000" name="Movies"/><category id="5000" name="TV"/></categories></caps>')


def caps_route(query, headers):
    if query.get("t") != "caps":
        return 400, "bad"
    if query.get("apikey") == "good":
        return 200, CAPS, "application/xml"
    return 200, '<?xml version="1.0"?><error code="100" description="Incorrect user credentials"/>', "application/xml"


class NewznabTest(unittest.TestCase):
    def setUp(self):
        self.srv = MockHttp({"/api": caps_route, "/html/api": lambda q, h: (200, "<html>login</html>", "text/html"),
                             "/other/api": lambda q, h: (200, '<error code="910" description="down"/>')})

    def tearDown(self):
        self.srv.close()

    def test_newznab_ok(self):
        r = newznab.check_newznab(self.srv.url, "good")
        self.assertTrue(r.ok, r)
        self.assertEqual(r.params["categories"], 2)

    def test_newznab_bad_key(self):
        r = newznab.check_newznab(self.srv.url, "wrong")
        self.assertFalse(r.ok)
        self.assertEqual(r.key, "check.newznab.bad_key")

    def test_newznab_other_error(self):
        r = newznab.check_newznab(self.srv.url + "/other", "good")
        self.assertEqual((r.key, r.params.get("code")), ("check.newznab.error", "910"))

    def test_newznab_html_is_not_newznab(self):
        r = newznab.check_newznab(self.srv.url + "/html", "good")
        self.assertEqual(r.key, "check.newznab.not_newznab")

    def test_url_variants_hit_same_endpoint(self):
        for variant in (self.srv.url, self.srv.url + "/", self.srv.url + "/api", self.srv.url + "/api/"):
            self.assertTrue(newznab.check_newznab(variant, "good").ok, variant)
        self.assertEqual({p for p, _, _ in self.srv.requests}, {"/api"})

    def test_params_contain_no_secret(self):
        r = newznab.check_newznab(self.srv.url, "wrong")
        self.assertNotIn("wrong", repr(r.params))


class HttpTest(unittest.TestCase):
    def test_error_message_hides_url(self):
        url = f"http://127.0.0.1:{closed_port()}/api?t=caps&apikey=supersecret"
        with self.assertRaises(HttpError) as cm:
            http_get(url, timeout=2)
        self.assertEqual(cm.exception.kind, "connect")
        self.assertNotIn("supersecret", str(cm.exception))
        self.assertNotIn("127.0.0.1", str(cm.exception))
        self.assertIsNone(cm.exception.__cause__)

    def test_network_failure_becomes_result(self):
        r = newznab.check_newznab(f"http://127.0.0.1:{closed_port()}", "k")
        self.assertEqual((r.ok, r.key, r.params["kind"]), (False, "check.network", "connect"))

    def test_dns_failure(self):
        with self.assertRaises(HttpError) as cm:
            http_get("http://nonexistent.invalid/", timeout=5)
        self.assertEqual(cm.exception.kind, "dns")

    def test_invalid_url_is_http_error_without_url(self):
        with self.assertRaises(HttpError) as cm:
            http_get("http://x.example/api?apikey=supersecret\r\nX: y", timeout=5)
        self.assertEqual(cm.exception.kind, "other")
        self.assertNotIn("supersecret", str(cm.exception))
        self.assertIsNone(cm.exception.__cause__)

    def test_http_status_returned_not_raised(self):
        srv = MockHttp({"/x": lambda q, h: (401, "no")})
        try:
            self.assertEqual(http_get(srv.url + "/x").status, 401)
        finally:
            srv.close()


class DuckdnsTest(unittest.TestCase):
    def setUp(self):
        token = "11111111-2222-3333-4444-555555555555"
        self.srv = MockHttp({"/update": lambda q, h: (200, "OK" if q.get("token") == token else "KO")})
        self.token = token

    def tearDown(self):
        self.srv.close()

    def test_duckdns_ok_ko(self):
        self.assertTrue(duckdns.check_duckdns("example", self.token, base=self.srv.url).ok)
        r = duckdns.check_duckdns("example", "00000000-0000-0000-0000-000000000000", base=self.srv.url)
        self.assertEqual((r.ok, r.key), (False, "check.duckdns.rejected"))
        self.assertEqual(self.srv.requests[0][1]["domains"], "example")


class TmdbTest(unittest.TestCase):
    def setUp(self):
        def conf(q, h):
            ok = q.get("api_key") == "a" * 32 or h.get("Authorization") == "Bearer " + "e" * 60
            return (200, '{"images": {}}', "application/json") if ok else (401, '{"status_code": 7}')
        self.srv = MockHttp({"/3/configuration": conf})

    def tearDown(self):
        self.srv.close()

    def test_tmdb_v3_and_v4(self):
        self.assertTrue(tmdb.check_tmdb("a" * 32, base=self.srv.url).ok)
        self.assertTrue(tmdb.check_tmdb("e" * 60, base=self.srv.url).ok)
        self.assertNotIn("api_key", self.srv.requests[1][1])
        r = tmdb.check_tmdb("b" * 32, base=self.srv.url)
        self.assertEqual(r.key, "check.tmdb.bad_key")
