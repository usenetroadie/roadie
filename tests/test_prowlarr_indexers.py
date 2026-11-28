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
        self.indexers, self.calls, self.next_id, self.tags = {}, [], 1, []
        self.failing = set()  # names Prowlarr refuses (its own test of the indexer fails)

    def __call__(self, base, key, method, path, body=None):
        self.calls.append((method, path))
        if path == "/api/v1/indexer/schema":
            return [copy.deepcopy(SCHEMA)]
        if path == "/api/v1/appprofile":
            return [{"id": 1}]
        if path == "/api/v1/tag":
            if method == "POST":
                self.tags.append({"id": len(self.tags) + 10, "label": body["label"]})
                return self.tags[-1]
            return copy.deepcopy(self.tags)
        if method == "GET":  # Prowlarr masks the key in answers
            out = copy.deepcopy(list(self.indexers.values()))
            for ix in out:
                for f in ix["fields"]:
                    if f["name"] == "apiKey":
                        f["value"] = "********"
            return out
        if method in ("POST", "PUT") and body.get("name") in self.failing:
            raise RuntimeError('400: [{"errorMessage": "Query successful, but no results in the configured '
                               'categories were returned from your indexer. This may be an issue with the '
                               'indexer or your indexer category settings. See the FAQ"}]')
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
                   "_compose": lambda ctx, *a, **k: (self.restarts.append(a) if a[:1] != ("ps",) else None) or "", "stack_healthy": lambda ctx: True}
        for name, fn in patches.items():
            original = getattr(stack, name)
            setattr(stack, name, fn)
            self.addCleanup(setattr, stack, name, original)

    def run_step(self, *indexers, changed=None):
        if changed is not None:
            self.ctx.extra["changed"] = changed
        self.ctx.answers = SimpleNamespace(data={"indexers": [dict(ix) for ix in indexers]})
        self.fake.calls.clear()
        self.restarts.clear()
        return stack.prowlarr_indexers(self.ctx)

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

    def test_indexer_without_key_gets_empty_api_key(self):
        self.run_step({"name": "A", "url": "https://a.example.com", "api_key": ""})
        self.assertEqual(self.fake.value("A", "apiKey"), "")
        self.run_step({"name": "A", "url": "https://a.example.com", "api_key": ""})
        self.assertEqual(self.writes(), [])

    def test_key_removed_is_put_with_empty_api_key(self):
        self.run_step({"name": "A", "url": "https://a.example.com", "api_key": "k1"})
        self.run_step({"name": "A", "url": "https://a.example.com", "api_key": ""}, changed={"indexers"})
        self.assertEqual(self.writes(), [("PUT", "/api/v1/indexer/1")])
        self.assertEqual(self.fake.value("A", "apiKey"), "")

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

    def tags_of(self, name):
        labels = {t["id"]: t["label"] for t in self.fake.tags}
        ix = next(i for i in self.fake.indexers.values() if i["name"] == name)
        return [labels.get(i, i) for i in ix.get("tags", [])]

    def test_main_and_fallback_indexers_get_their_tag(self):
        a = {"name": "A", "url": "https://a.example.com", "api_key": "k"}
        b = {"name": "B", "url": "https://b.example.com", "api_key": "k", "fallback": True}
        self.run_step(a, b)
        self.assertEqual((self.tags_of("A"), self.tags_of("B")), (["roadie-main"], ["roadie-fallback"]))
        self.fake.indexers[2]["tags"].append(7)  # a tag the user set stays
        self.run_step(a, dict(b, fallback=False))  # no split any more: the wizard's tags go
        self.assertEqual((self.tags_of("A"), self.tags_of("B")), ([], [7]))

    def test_without_fallback_nothing_changes_for_existing_installs(self):
        ix = {"name": "A", "url": "https://a.example.com", "api_key": "k"}
        self.run_step(ix)
        self.run_step(dict(ix, fallback=False))
        self.assertEqual(self.writes(), [])
        self.assertEqual(self.fake.tags, [])



A = {"name": "A", "url": "https://a.example.com", "api_key": "KA"}
B = {"name": "B", "url": "https://b.example.com", "api_key": "KB"}


class TolerantTest(ProwlarrIndexersTest):
    def test_one_indexer_fails_others_ok(self):
        self.fake.failing = {"A"}
        outcome = self.run_step(A, B)
        self.assertEqual(outcome.status, "warn")
        self.assertEqual(self.ctx.state.answers["_indexers_missing"], ["A"])
        self.assertEqual([i["name"] for i in self.fake.indexers.values()], ["B"])

    def test_all_indexers_fail_raises_plain(self):
        from roadie.engine import StepFailed
        self.fake.failing = {"A", "B"}
        with self.assertRaises(StepFailed) as e:
            self.run_step(A, B)
        self.assertNotIn("FAQ", e.exception.message)
        self.assertNotIn("category settings", e.exception.message)

    def test_missing_indexer_retried(self):
        self.fake.failing = {"A"}
        self.run_step(A, B)
        self.fake.failing = set()
        outcome = self.run_step(A, B)
        self.assertIsNone(outcome)
        self.assertNotIn("_indexers_missing", self.ctx.state.answers)
        self.assertEqual(sorted(i["name"] for i in self.fake.indexers.values()), ["A", "B"])

    def test_failed_update_keeps_existing_indexer(self):
        self.run_step(A, B)
        self.fake.failing = {"A"}
        self.run_step({**A, "api_key": "NEW"}, B)
        self.assertIn("A", [i["name"] for i in self.fake.indexers.values()])  # not deleted

    def test_deleted_indexer_not_readded_on_update(self):
        self.run_step(A, B)
        del self.fake.indexers[1]  # the user deleted A in Prowlarr
        self.run_step(A, B, changed=set())
        self.assertEqual(self.writes(), [])
        self.assertEqual([i["name"] for i in self.fake.indexers.values()], ["B"])
        self.assertIn("A", self.ctx.state.answers["_prowlarr_indexers"])
        self.run_step(A, B, changed=set())  # and it stays that way
        self.assertEqual(self.writes(), [])

    def test_deleted_indexer_readded_on_change(self):
        self.run_step(A, B)
        del self.fake.indexers[1]
        self.run_step(A, B, changed={"indexers"})
        self.assertEqual(self.writes(), [("POST", "/api/v1/indexer")])
        self.assertEqual(sorted(i["name"] for i in self.fake.indexers.values()), ["A", "B"])

    def test_failed_readd_retried_on_next_update(self):
        self.run_step(A, B)
        del self.fake.indexers[1]
        self.fake.failing = {"A"}
        self.run_step({**A, "api_key": "NEW"}, B, changed={"indexers"})  # re-add fails
        self.assertNotIn("A", self.ctx.state.answers["_prowlarr_indexers"])
        self.fake.failing = set()
        self.run_step({**A, "api_key": "NEW"}, B, changed=set())
        self.assertEqual(sorted(i["name"] for i in self.fake.indexers.values()), ["A", "B"])

    def test_never_applied_indexer_created_on_resume(self):
        self.run_step(A, changed=set())  # resumed first setup: nothing applied yet
        self.assertEqual(self.writes(), [("POST", "/api/v1/indexer")])

    def test_renamed_indexer_updated_not_duplicated(self):
        self.run_step(A)
        self.fake.indexers[1]["name"] = "My NZB"  # the user renamed it
        self.run_step({**A, "api_key": "NEW"}, changed={"indexers"})
        self.assertEqual(self.writes(), [("PUT", "/api/v1/indexer/1")])
        self.assertEqual(self.fake.indexers[1]["name"], "My NZB")
        self.assertEqual(self.fake.value("My NZB", "apiKey"), "NEW")

    def test_renamed_indexer_not_deleted_when_unchanged_or_dropped_name(self):
        self.run_step(A)
        self.fake.indexers[1]["name"] = "My NZB"
        self.run_step(A, changed=set())
        self.assertEqual(self.writes(), [])
        self.assertIn(1, self.fake.indexers)


class PlainTextTest(unittest.TestCase):
    def test_prowlarr_problem_no_results(self):
        text = stack.prowlarr_problem("Query successful, but no results in the configured categories were "
                                      "returned from your indexer. See the FAQ")
        self.assertNotIn("FAQ", text)
        self.assertIn("no results", text.lower())

    def test_prowlarr_problem_timeout_and_key(self):
        self.assertIn("answer", stack.prowlarr_problem("Unable to connect to indexer: timeout").lower())
        self.assertIn("key", stack.prowlarr_problem("Unauthorized: invalid API Key").lower())

    def test_pull_problem_detects_registry(self):
        out = ("Error response from daemon: Get \"https://registry-1.docker.io/v2/\": dial tcp "
               "127.0.0.1:443: connect: connection refused")
        self.assertIsNotNone(stack.pull_problem(out))
        self.assertIsNone(stack.pull_problem("some other compose error"))


if __name__ == "__main__":
    unittest.main()
