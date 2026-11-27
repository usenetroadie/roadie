"""Indexers that need no API key: the key may stay empty everywhere."""

import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from roadie import answers as A
from roadie.checks import newznab
from roadie.checks.http import HttpResponse
from roadie.questions import ask_indexers
from roadie.redact import Redactor
from roadie.secretstore import SecretStore
from roadie.state import State
from roadie.ui import Prompter
from tests.test_answers import example
from tests.test_questions import FakeChecks


def ask(lines, secrets, data=None):
    out, queue, prompts = io.StringIO(), list(secrets), []
    ui = Prompter(inp=io.StringIO("\n".join(lines) + "\n"), out=out,
                  secret_reader=lambda p: (prompts.append(p), queue.pop(0))[1])
    data = data if data is not None else {}
    ask_indexers(ui, data, FakeChecks())
    return data, prompts, out.getvalue()


NEW = ["Anime", "https://anime.example.com", "", "", "n"]  # name, url, two limits, another?
from roadie.questions import PREFILL  # noqa: E402

OLD = {PREFILL: True, "indexers": [{"name": "Anime", "url": "https://anime.example.com", "api_key": "oldkey123"}]}


class QuestionTest(unittest.TestCase):
    def test_empty_key_is_accepted(self):
        data, prompts, _ = ask(NEW, [""])
        self.assertEqual(data["indexers"][0]["api_key"], "")
        self.assertIn("empty", prompts[0])

    def test_enter_keeps_existing_key(self):
        data, prompts, _ = ask(NEW, [""], copy.deepcopy(OLD))
        self.assertEqual(data["indexers"][0]["api_key"], "oldkey123")
        self.assertIn("-", prompts[0])

    def test_dash_removes_existing_key(self):
        data, _, _ = ask(NEW, ["-"], copy.deepcopy(OLD))
        self.assertEqual(data["indexers"][0]["api_key"], "")

    def test_lone_dash_without_key_means_no_key(self):
        data, _, _ = ask(NEW, ["-"])
        self.assertEqual(data["indexers"][0]["api_key"], "")

    def test_dash_hint_only_with_existing_key(self):
        _, prompts, _ = ask(NEW, [""])
        self.assertNotIn("remove", prompts[0])

    def test_review_shows_none_for_empty_key(self):
        from roadie.questions import _summary
        d = example()
        d["indexers"][0]["api_key"] = ""
        line = next(x for x in _summary("usenet", d) if "Indexer" in x)
        self.assertIn("key —", line)
        self.assertNotIn("•", line)


class AnswersTest(unittest.TestCase):
    def test_empty_key_valid(self):
        d = example()
        d["indexers"][0]["api_key"] = ""
        self.assertEqual(A.validate(d), [])

    def test_key_must_be_string(self):
        for bad in (None, 5):
            d = example()
            d["indexers"][0]["api_key"] = bad
            self.assertTrue(any(e.startswith("indexers[0].api_key") for e in A.validate(d)), bad)
        d = example()
        del d["indexers"][0]["api_key"]
        self.assertTrue(any(e.startswith("indexers[0].api_key") for e in A.validate(d)))

    def test_empty_key_survives_store_and_restore(self):
        d = example()
        d["indexers"][0]["api_key"] = ""
        with tempfile.TemporaryDirectory() as tmp:
            state, secrets = State(Path(tmp) / "s.json"), SecretStore(Path(tmp) / "x.json")
            a = A.Answers.from_dict(d)
            a.store(state, secrets)
            b = A.Answers.restore(State(Path(tmp) / "s.json"), SecretStore(Path(tmp) / "x.json"))
        self.assertEqual(b.data["indexers"][0]["api_key"], "")
        self.assertNotIn("", a.secret_values())


class RedactTest(unittest.TestCase):
    def test_empty_and_short_values_ignored(self):
        r = Redactor(["", "ab"])
        r.add("")
        self.assertEqual(r("plain text ab"), "plain text ab")


class UrlTest(unittest.TestCase):
    def urls(self, fn):
        seen = []

        def fake_get(url, **kw):
            seen.append(url)
            return HttpResponse(200, b'<caps><categories/></caps>')
        with mock.patch.object(newznab, "http_get", side_effect=fake_get):
            fn()
        return seen

    def test_no_apikey_param_when_empty(self):
        for fn in (lambda: newznab.check_newznab("https://i.example.com", ""),
                   lambda: newznab.probe("https://i.example.com", ""),
                   lambda: newznab.detect_limits("https://i.example.com", "")):
            urls = self.urls(fn)
            self.assertTrue(urls)
            for u in urls:
                self.assertNotIn("apikey", u)

    def test_key_still_sent(self):
        urls = self.urls(lambda: newznab.check_newznab("https://i.example.com", "K"))
        self.assertIn("apikey=K", urls[0])
