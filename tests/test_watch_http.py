"""The watcher's own HTTP checks never abort a run: a cut-off answer counts as 'not reachable'
or 'no indexer info', so pushes and the outside ping still happen."""

import http.client
import tempfile
import unittest
import urllib.request
from pathlib import Path
from types import SimpleNamespace

from roadie import system
from roadie.ops import watch


def broken_urlopen(*args, **kwargs):
    raise http.client.IncompleteRead(b"partial")


class WatchHttpTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        etc = Path(tmp.name)
        (etc / "addon-url.txt").write_text("https://aiostreams.example.duckdns.org/stremio/x/y/manifest.json\n")
        self.env = SimpleNamespace(domain="example.duckdns.org", paths=SimpleNamespace(etc_dir=etc),
                                   secrets=SimpleNamespace(get=lambda name: "key"),
                                   compose=lambda *a, **k: "cid\n")
        for obj, name, fn in ((urllib.request, "urlopen", broken_urlopen),
                              (watch, "tls_days_left", lambda host: None),
                              (system, "run", lambda *a, **k: "172.18.0.5\n")):
            original = getattr(obj, name)
            setattr(obj, name, fn)
            self.addCleanup(setattr, obj, name, original)

    def test_cut_off_answer_means_unreachable(self):
        self.assertEqual([p.key for p in watch._outside_problems(self.env)], ["watch.unreachable"])

    def test_cut_off_prowlarr_answer_is_no_problem(self):
        self.assertEqual(watch._indexer_problems(self.env), [])


if __name__ == "__main__":
    unittest.main()
