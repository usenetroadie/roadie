import unittest

from roadie.redact import Redactor


class RedactorTest(unittest.TestCase):
    def test_redacts_registered_secret(self):
        r = Redactor(["supersecretvalue"])
        self.assertEqual(r("x supersecretvalue y"), "x *** y")

    def test_add_later(self):
        r = Redactor()
        r.add("anothersecret")
        self.assertNotIn("anothersecret", r("anothersecret"))

    def test_redacts_apikey_query(self):
        out = Redactor()("GET https://x.example/api?t=caps&apikey=abc123&o=json")
        self.assertNotIn("abc123", out)
        self.assertIn("t=caps", out)

    def test_redacts_token_and_password_params(self):
        out = Redactor()("update?domains=x&token=tok999 pass=pw1 password=pw2 api_key=k1")
        for s in ("tok999", "pw1", "pw2", "k1"):
            self.assertNotIn(s, out)

    def test_redacts_authorization_header(self):
        out = Redactor()("Authorization: Bearer eyJabc.def")
        self.assertNotIn("eyJabc", out)

    def test_redacts_ipv4_ipv6(self):
        out = Redactor()("from 203.0.113.10 and 2001:db8::1 port 22")
        self.assertNotIn("203.0.113.10", out)
        self.assertNotIn("2001:db8::1", out)
        self.assertIn("port 22", out)

    def test_time_is_not_ipv6(self):
        self.assertEqual(Redactor()("at 05:00:12"), "at 05:00:12")

    def test_redacts_domain_optional(self):
        text = "https://aio.example.duckdns.org/x"
        self.assertEqual(Redactor()(text), text)
        self.assertNotIn("example.duckdns.org", Redactor(hide_domain="example.duckdns.org")(text))

    def test_short_values_not_redacted(self):
        self.assertEqual(Redactor(["a"])("a cat"), "a cat")

    def test_longest_secret_first(self):
        out = Redactor(["abcd", "abcdefgh"])("abcdefgh")
        self.assertEqual(out, "***")
