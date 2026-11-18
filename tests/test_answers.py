import copy
import json
import unittest
from pathlib import Path

from roadie import answers as A

EXAMPLE = json.loads((Path(__file__).parent.parent / "examples/answers.example.json").read_text())


def example():
    return copy.deepcopy(EXAMPLE)


class AnswersTest(unittest.TestCase):
    def test_example_file_is_valid(self):
        self.assertEqual(A.validate(example()), [])

    def test_collects_all_errors(self):
        d = example()
        d["language"] = "fr"
        d["admin_user"] = "root"
        d["duckdns"]["subdomain"] = "bad_name!"
        errors = A.validate(d)
        self.assertEqual(len(errors), 3, errors)
        self.assertTrue(any(e.startswith("language:") for e in errors))
        self.assertTrue(any(e.startswith("admin_user:") for e in errors))
        self.assertTrue(any(e.startswith("duckdns.subdomain:") for e in errors))

    def test_missing_field_reported(self):
        d = example()
        del d["auth"]
        self.assertTrue(any(e.startswith("auth") for e in A.validate(d)))

    def test_system_account_refused_as_admin(self):
        import pwd
        from unittest import mock
        entries = {"daemon": ("daemon", "x", 1, 1, "", "/usr/sbin", "/usr/sbin/nologin"),
                   "olduser": ("olduser", "x", 1001, 1001, "", "/home/olduser", "/bin/false"),
                   "ubuntu": ("ubuntu", "x", 1000, 1000, "", "/home/ubuntu", "/bin/bash")}

        def getpwnam(name):
            if name not in entries:
                raise KeyError(name)
            return pwd.struct_passwd(entries[name])
        with mock.patch.object(A.pwd, "getpwnam", side_effect=getpwnam):
            for user, ok in (("daemon", False), ("olduser", False), ("ubuntu", True), ("streamadmin", True)):
                d = example()
                d["admin_user"] = user
                errors = [e for e in A.validate(d) if e.startswith("admin_user:")]
                self.assertEqual(not errors, ok, user)
                self.assertEqual(A.admin_user_problem(user) is None, ok, user)

    def test_normalises_duckdns_name(self):
        self.assertEqual(A.normalise_duckdns(" https://MyName.duckdns.org/ "), "myname")

    def test_rejects_bad_duckdns(self):
        for bad in ("", "a.b", "x" * 64, "under_score"):
            with self.assertRaises(ValueError, msg=bad):
                A.normalise_duckdns(bad)

    def test_accepts_crlf_ssh_key(self):
        key = EXAMPLE["ssh_public_key"]
        self.assertEqual(A.normalise_ssh_key("  " + key + "\r\n"), key)

    def test_rejects_private_key(self):
        with self.assertRaises(ValueError) as cm:
            A.normalise_ssh_key("-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n")
        self.assertIn("private", str(cm.exception).lower())

    def test_rejects_garbage_key(self):
        with self.assertRaises(ValueError):
            A.normalise_ssh_key("ssh-ed25519 not-base64!!")

    def test_newznab_url_variants(self):
        for url in ("https://x.tld/api/", "https://x.tld/", "https://x.tld/api", " https://x.tld "):
            self.assertEqual(A.normalise_newznab_url(url), "https://x.tld", url)
        self.assertEqual(A.normalise_newznab_url("https://x.tld/sub/api"), "https://x.tld/sub")
        with self.assertRaises(ValueError):
            A.normalise_newznab_url("x.tld")

    def test_newznab_url_refuses_whitespace_and_control_characters(self):
        for bad in ("https://x.example/api\r\nX: y", "https://x.example/a b", "https://x.example/\x00", "https://x.\texample"):
            with self.assertRaises(ValueError, msg=repr(bad)):
                A.normalise_newznab_url(bad)

    def test_ntfy_url_checked(self):
        for bad, ok in (("http://ntfy.example.com", False), ("file:///etc/passwd", False),
                        ("https://ntfy.example.com/a b", False), ("https://ntfy.example.com", True)):
            d = example()
            d["notify"]["ntfy_url"] = bad
            errors = [e for e in A.validate(d) if e.startswith("notify.ntfy_url")]
            self.assertEqual(not errors, ok, bad)

    def test_normalise_time(self):
        self.assertEqual(A.normalise_time("5:00"), "05:00")
        with self.assertRaises(ValueError):
            A.normalise_time("25:00")

    def test_exactly_one_primary_provider(self):
        d = example()
        p2 = dict(d["usenet"]["providers"][0], host="news2.example.com")
        d["usenet"]["providers"].append(p2)
        self.assertTrue(any("primary" in e for e in A.validate(d)))

    def test_provider_id_must_be_known(self):
        d = example()
        d["usenet"]["providers"][0]["provider"] = "eweka"
        self.assertEqual(A.validate(d), [])
        d["usenet"]["providers"][0]["provider"] = "nope"
        self.assertTrue(any(e.startswith("usenet.providers[0].provider") for e in A.validate(d)))

    def test_age_recipient_is_stripped(self):
        d = example()
        d["backup"]["age_recipient"] = "  " + d["backup"]["age_recipient"] + "\n"
        self.assertEqual(A.Answers.from_dict(d).data["backup"]["age_recipient"], EXAMPLE["backup"]["age_recipient"])

    def test_usenet_requires_indexer(self):
        d = example()
        d["indexers"] = []
        self.assertTrue(any(e.startswith("indexers") for e in A.validate(d)))

    def test_indexer_names_are_unique(self):
        d = example()
        d["indexers"].append(dict(d["indexers"][0], url="https://other.example.com"))
        d["indexers"][1]["name"] = d["indexers"][0]["name"].upper()
        self.assertTrue(any(e.startswith("indexers[1].name") for e in A.validate(d)))

    def test_login_name_message_fits_the_rule(self):
        d = example()
        d["auth"]["username"] = "Max Mustermann"
        errors = [e for e in A.validate(d) if e.startswith("auth.username")]
        self.assertEqual(len(errors), 1)
        self.assertIn("spaces", errors[0])
        d["auth"]["username"] = "Max.M"  # capitals and dots are fine
        self.assertFalse(any(e.startswith("auth.username") for e in A.validate(d)))

    def test_usenet_requires_provider(self):
        d = example()
        d["usenet"] = {"providers": []}
        self.assertTrue(any(e.startswith("usenet.providers") for e in A.validate(d)))

    def test_unknown_content_language(self):
        d = example()
        d["content"]["languages"] = ["Klingon"]
        self.assertTrue(any(e.startswith("content.languages") for e in A.validate(d)))

    def test_short_password_rejected(self):
        d = example()
        d["auth"]["password"] = "short"
        self.assertTrue(any(e.startswith("auth.password") for e in A.validate(d)))

    def test_from_dict_normalises(self):
        d = example()
        d["duckdns"]["subdomain"] = "Example.duckdns.org"
        d["indexers"][0]["url"] = "https://indexer.example.com/api/"
        a = A.Answers.from_dict(d)
        self.assertEqual(a.data["duckdns"]["subdomain"], "example")
        self.assertEqual(a.data["indexers"][0]["url"], "https://indexer.example.com")

    def test_from_dict_invalid_raises_with_all_errors(self):
        d = example()
        d["language"] = "fr"
        d["admin_user"] = "root"
        with self.assertRaises(A.AnswersInvalid) as cm:
            A.Answers.from_dict(d)
        self.assertEqual(len(cm.exception.errors), 2)

    def test_to_dict_drops_secrets(self):
        a = A.Answers.from_dict(example())
        text = json.dumps(a.to_dict())
        for secret in ("example-password", "EXAMPLEINDEXERKEY",
                       "change-me-to-something-long", "00000000-0000-0000-0000-000000000000"):
            self.assertNotIn(secret, text)
        self.assertIn("EXAMPLEINDEXERKEY", json.dumps(a.to_dict(include_secrets=True)))

    def test_secret_values_listed(self):
        a = A.Answers.from_dict(example())
        self.assertIn("example-password", a.secret_values())


class PersistTest(unittest.TestCase):
    def test_store_and_restore_roundtrip(self):
        import tempfile
        from roadie.secretstore import SecretStore
        from roadie.state import State
        with tempfile.TemporaryDirectory() as tmp:
            state, secrets = State(Path(tmp) / "s.json"), SecretStore(Path(tmp) / "x.json")
            a = A.Answers.from_dict(example())
            a.store(state, secrets)
            self.assertNotIn("example-password", (Path(tmp) / "s.json").read_text())
            self.assertEqual(secrets.get("answer.usenet.providers[0].password"), "example-password")
            b = A.Answers.restore(State(Path(tmp) / "s.json"), SecretStore(Path(tmp) / "x.json"))
            self.assertEqual(b.data, a.data)

    def test_store_keeps_internal_markers(self):
        import tempfile
        from roadie.secretstore import SecretStore
        from roadie.state import State
        with tempfile.TemporaryDirectory() as tmp:
            state, secrets = State(Path(tmp) / "s.json"), SecretStore(Path(tmp) / "x.json")
            state.answers["_ssh_hardened"] = True
            A.Answers.from_dict(example()).store(state, secrets)
            self.assertTrue(State(Path(tmp) / "s.json").answers["_ssh_hardened"])


    def test_new_ssh_key_answer_replaces_marker(self):
        import tempfile
        from roadie.secretstore import SecretStore
        from roadie.state import State
        with tempfile.TemporaryDirectory() as tmp:
            state, secrets = State(Path(tmp) / "s.json"), SecretStore(Path(tmp) / "x.json")
            state.answers["_ssh_key_created"] = True
            A.Answers.from_dict(example()).store(state, secrets)  # answers file: marker kept
            self.assertTrue(state.answers["_ssh_key_created"])
            A.Answers.from_dict({**example(), "_ssh_key_created": False}).store(state, secrets)
            self.assertFalse(State(Path(tmp) / "s.json").answers["_ssh_key_created"])

    def test_removed_secret_answer_stays_removed(self):
        import tempfile
        from roadie.secretstore import SecretStore
        from roadie.state import State
        with tempfile.TemporaryDirectory() as tmp:
            state, secrets = State(Path(tmp) / "s.json"), SecretStore(Path(tmp) / "x.json")
            secrets.set("prowlarr_api_key", "generated")  # not an answer: must stay
            d = example()
            d["tmdb_api_key"] = "EXAMPLETMDBKEY"
            d["notify"]["healthchecks_url"] = "https://hc-ping.example.com/abc"
            d["indexers"] = d["indexers"] + [{**d["indexers"][0], "name": "Second indexer"}]
            A.Answers.from_dict(d).store(state, secrets)
            self.assertEqual(secrets.get("answer.tmdb_api_key"), "EXAMPLETMDBKEY")
            d = example()  # both removed again, one indexer less
            A.Answers.from_dict(d).store(state, secrets)
            secrets = SecretStore(Path(tmp) / "x.json")
            self.assertIsNone(secrets.get("answer.tmdb_api_key"))
            self.assertIsNone(secrets.get("answer.notify.healthchecks_url"))
            self.assertIsNone(secrets.get("answer.indexers[1].api_key"))
            self.assertEqual(secrets.get("prowlarr_api_key"), "generated")
            b = A.Answers.restore(State(Path(tmp) / "s.json"), secrets)
            self.assertIsNone(b.data["tmdb_api_key"])
            self.assertIsNone(b.data["notify"]["healthchecks_url"])


class PeopleAnswersTest(unittest.TestCase):
    OWN = {"languages": ["English"], "original_audio": False, "prefer_4k": False, "hdr": False}

    def errors(self, people):
        d = example()
        d["people"] = people
        return A.validate(d)

    def test_people_valid(self):
        self.assertEqual(self.errors([{"id": "main", "name": "Me", "mode": "main"},
                                      {"id": "lisa", "name": "Lisa", "mode": "inherit"},
                                      {"id": "ben-2", "name": "Ben", "mode": "own", "content": self.OWN}]), [])

    def test_people_validation(self):
        main = {"id": "main", "name": "Me", "mode": "main"}
        cases = {
            "two mains": [main, {"id": "x", "name": "X", "mode": "main"}],
            "main not first": [{"id": "x", "name": "X", "mode": "inherit"}, main],
            "duplicate id": [main, {"id": "x", "name": "X", "mode": "inherit"}, {"id": "x", "name": "Y", "mode": "inherit"}],
            "non-main person with id main": [{"id": "me", "name": "Me", "mode": "main"},
                                             {"id": "main", "name": "X", "mode": "inherit"}],
            "main entry with other id": [{"id": "me", "name": "Me", "mode": "main"}],
            "own without content": [main, {"id": "x", "name": "X", "mode": "own"}],
            "bad id": [main, {"id": "X Y", "name": "X", "mode": "inherit"}],
            "empty name": [main, {"id": "x", "name": "", "mode": "inherit"}],
            "long name": [main, {"id": "x", "name": "n" * 41, "mode": "inherit"}],
            "blank name": [main, {"id": "x", "name": "   ", "mode": "inherit"}],
            "tab in name": [main, {"id": "x", "name": "An\tna", "mode": "inherit"}],
            "newline in name": [main, {"id": "x", "name": "An\nna", "mode": "inherit"}],
            "bad mode": [main, {"id": "x", "name": "X", "mode": "other"}],
            "bad own content": [main, {"id": "x", "name": "X", "mode": "own", "content": {**self.OWN, "languages": []}}],
            "content on inherit": [main, {"id": "x", "name": "X", "mode": "inherit", "content": self.OWN}],
            "too many": [main] + [{"id": f"p{i}", "name": "P", "mode": "inherit"} for i in range(20)],
            "empty": [],
        }
        for label, value in cases.items():
            errs = self.errors(value)
            self.assertTrue(errs, label)
            self.assertTrue(all(e.startswith("people") for e in errs), (label, errs))

    def test_people_name_is_stripped(self):
        d = example()
        d["people"] = [{"id": "main", "name": "  Me  ", "mode": "main"}]
        self.assertEqual(A.Answers.from_dict(d).data["people"][0]["name"], "Me")
