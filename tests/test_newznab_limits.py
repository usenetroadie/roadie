import unittest

from roadie.checks.newznab import parse_limits

NNTMUX = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:newznab="http://www.newznab.com/DTD/2010/feeds/attributes/"><channel>
<newznab:apilimits apicurrent="3" apimax="100" grabcurrent="0" grabmax="25" apioldesttime="x"/>
<newznab:response offset="0" total="1"/></channel></rss>"""

NO_LIMITS = b"""<rss version="2.0" xmlns:newznab="http://www.newznab.com/DTD/2010/feeds/attributes/">
<channel><newznab:response offset="0" total="14642"/></channel></rss>"""

ONLY_API = b"""<rss><channel><apilimits apimax="500"/></channel></rss>"""


class ParseLimitsTest(unittest.TestCase):
    def test_reads_api_and_grab_limits(self):
        self.assertEqual(parse_limits(NNTMUX), (100, 25))

    def test_none_when_indexer_does_not_tell(self):
        self.assertEqual(parse_limits(NO_LIMITS), (None, None))

    def test_partial_and_namespace_free(self):
        self.assertEqual(parse_limits(ONLY_API), (500, None))

    def test_garbage_is_none(self):
        self.assertEqual(parse_limits(b"<html>nope"), (None, None))


CAPS = b'<?xml version="1.0"?><caps><categories><category id="2000" name="Movies"/></categories></caps>'
BAD_KEY = b'<?xml version="1.0"?><error code="100" description="Incorrect user credentials"/>'


class ProbeTest(unittest.TestCase):
    def probe(self, *answers):
        from unittest import mock
        from roadie.checks import newznab
        from roadie.checks.http import HttpError, HttpResponse
        replies = [a if isinstance(a, Exception) else HttpResponse(*a) for a in answers]

        def fake_get(url, **kw):
            reply = replies.pop(0)
            if isinstance(reply, Exception):
                raise reply
            return reply
        with mock.patch.object(newznab, "http_get", side_effect=fake_get):
            return newznab.probe("https://indexer.example.com", "KEY")

    def test_probe_bad_key_from_search(self):
        result, _, _ = self.probe((200, CAPS), (200, BAD_KEY))  # caps answers without checking the key
        self.assertEqual(result.key, "check.newznab.bad_key")

    def test_probe_ok_with_limits(self):
        result, api, grab = self.probe((200, CAPS), (200, NNTMUX))
        self.assertTrue(result.ok)
        self.assertEqual((api, grab), (100, 25))

    def test_probe_down_on_503(self):
        result, _, _ = self.probe((503, b"service unavailable"))
        self.assertEqual(result.key, "check.newznab.down")

    def test_probe_down_on_timeout(self):
        from roadie.checks.http import HttpError
        result, _, _ = self.probe(HttpError("timeout"))
        self.assertEqual(result.key, "check.newznab.down")

    def test_down_text_is_not_address_hint(self):
        from roadie import i18n
        self.assertNotIn("address", i18n.t("check.newznab.down"))
