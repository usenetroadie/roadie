"""The indexer step keeps Prowlarr in line with the answers: add, update changed ones,
remove the ones the wizard added that are gone - and leaves indexers the user added alone."""

import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from roadie.engine import Context
from roadie.paths import Paths
from roadie.phases import stack
from roadie.redact import Redactor
from roadie.secretstore import SecretStore
from roadie.state import State

SCHEMA = {"implementation": "Newznab", "name": "Generic Newznab",
          "fields": [{"name": n, "value": None} for n in
                     ("baseUrl", "apiPath", "apiKey", "baseSettings.queryLimit", "baseSettings.grabLimit")]}


class FakeProwlarr:
    def __init__(self):
        self.indexers, self.calls, self.next_id = {}, [], 1

    def __call__(self, base, key, method, path, body=None):
        self.calls.append((method, path))
        if path == "/api/v1/indexer/schema":
            return [copy.deepcopy(SCHEMA)]
        if path == "/api/v1/appprofile":
            return [{"id": 1}]
        if method == "GET":  # Prowlarr masks the key in answers
            out = copy.deepcopy(list(self.indexers.values()))
            for ix in out:
                for f in ix["fields"]:
                    if f["name"] == "apiKey":
                        f["value"] = "********"
            return out
        if method == "POST":
            body = {**body, "id": self.next_id}
            self.indexers[self.next_id], self.next_id = body, self.next_id + 1
            return body
        ident = int(path.rsplit("/", 1)[1])
        if method == "PUT":
            self.indexers[ident] = body
        if method == "DELETE":
            del self.indexers[ident]
        return None

    def value(self, name, field):
        ix = next(i for i in self.indexers.values() if i["name"] == name)
        return next(f["value"] for f in ix["fields"] if f["name"] == field)


class ProwlarrIndexersTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        paths = Paths(root=Path(tmp.name))
        paths.etc_dir.mkdir(parents=True)
        self.ctx = Context(paths=paths, state=State(paths.state_file), secrets=SecretStore(paths.secrets_file),
                           ui=None, redact=Redactor())
        self.fake, self.restarts = FakeProwlarr(), []
        patches = {"_prowlarr": self.fake, "_container_url": lambda ctx, s, p: "http://prowlarr",
                   "_compose": lambda ctx, *a: self.restarts.append(a), "stack_healthy": lambda ctx: True}
        for name, fn in patches.items():
            original = getattr(stack, name)
            setattr(stack, name, fn)
            self.addCleanup(setattr, stack, name, original)

    def run_step(self, *indexers):
        self.ctx.answers = SimpleNamespace(data={"indexers": [dict(ix) for ix in indexers]})
        self.fake.calls.clear()
        self.restarts.clear()
        stack.prowlarr_indexers(self.ctx)

    def writes(self):
        return [c for c in self.fake.calls if c[0] != "GET"]

    def test_add_then_unchanged_does_nothing(self):
        ix = {"name": "A", "url": "https://a.example.com", "api_key": "k1", "daily_api_limit": 100}
        self.run_step(ix)
        self.assertEqual(self.fake.value("A", "baseSettings.queryLimit"), 100)
        self.assertEqual(self.restarts, [("restart", "aiostreams")])
        self.run_step(ix)
        self.assertEqual(self.writes(), [])
        self.assertEqual(self.restarts, [])

    def test_changed_key_and_limit_are_updated(self):
        self.run_step({"name": "A", "url": "https://a.example.com", "api_key": "k1"})
        self.run_step({"name": "A", "url": "https://a.example.com", "api_key": "k2", "daily_grab_limit": 5})
        self.assertEqual(self.writes(), [("PUT", "/api/v1/indexer/1")])
        self.assertEqual(self.fake.value("A", "apiKey"), "k2")
        self.assertEqual(self.fake.value("A", "baseSettings.grabLimit"), 5)
        self.assertEqual(self.restarts, [("restart", "aiostreams")])

    def test_removed_limit_is_cleared(self):
        self.run_step({"name": "A", "url": "https://a.example.com", "api_key": "k", "daily_api_limit": 100})
        self.run_step({"name": "A", "url": "https://a.example.com", "api_key": "k"})
        self.assertIsNone(self.fake.value("A", "baseSettings.queryLimit"))

    def test_dropped_wizard_indexer_removed_user_indexer_kept(self):
        self.run_step({"name": "A", "url": "https://a.example.com", "api_key": "k"},
                      {"name": "B", "url": "https://b.example.com", "api_key": "k"})
        self.fake.indexers[99] = {"id": 99, "name": "Mine", "fields": []}  # added by the user in Prowlarr
        self.run_step({"name": "A", "url": "https://a.example.com", "api_key": "k"})
        self.assertEqual(sorted(i["name"] for i in self.fake.indexers.values()), ["A", "Mine"])
        self.assertEqual(self.writes(), [("DELETE", "/api/v1/indexer/2")])


if __name__ == "__main__":
    unittest.main()
