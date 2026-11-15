#!/usr/bin/env python3
"""Tiny fake Newznab indexer for end-to-end tests: valid caps, one dummy search result."""
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

CAPS = """<?xml version="1.0" encoding="UTF-8"?>
<caps><server version="1.0" title="mock"/><limits max="100" default="100"/>
<searching><search available="yes" supportedParams="q"/><tv-search available="yes" supportedParams="q,season,ep,imdbid,tvdbid"/>
<movie-search available="yes" supportedParams="q,imdbid"/></searching>
<categories><category id="2000" name="Movies"><subcat id="2040" name="HD"/></category>
<category id="5000" name="TV"><subcat id="5040" name="HD"/></category></categories></caps>"""
EMPTY = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:newznab="http://www.newznab.com/DTD/2010/feeds/attributes/"><channel><title>mock</title>
<newznab:response offset="0" total="1"/>
<item><title>The.Shawshank.Redemption.1994.1080p.BluRay.x264-SWTEST</title><guid isPermaLink="false">mock-0001</guid>
<link>http://127.0.0.1/mock-0001.nzb</link><pubDate>Sat, 14 Nov 2026 08:00:00 +0000</pubDate>
<category>Movies &gt; HD</category><enclosure url="http://127.0.0.1/mock-0001.nzb" length="1048576" type="application/x-nzb"/>
<newznab:attr name="category" value="2040"/><newznab:attr name="size" value="1048576"/></item></channel></rss>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        query = parse_qs(urlparse(self.path).query)
        body = (CAPS if query.get("t", [""])[0] == "caps" else EMPTY).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/xml")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", int(sys.argv[1]) if len(sys.argv) > 1 else 8099), Handler).serve_forever()
