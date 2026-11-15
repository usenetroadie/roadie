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
