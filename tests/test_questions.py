import copy
import io
import json
import unittest
from pathlib import Path

from roadie import i18n
from roadie.answers import Answers, content_languages
from roadie.checks import CheckResult
from roadie.questions import ask_all
from roadie.ui import Prompter

KEY = json.loads((Path(__file__).parent.parent / "examples/answers.example.json").read_text())["ssh_public_key"]
TOKEN = "11111111-2222-3333-4444-555555555555"
OK = CheckResult(True, "check.ok")


class FakeChecks:
    def __init__(self, newznab=None, limits=(None, None)):
        self.calls = []
        self.newznab_results = list(newznab or [OK])
        self.limit_result = limits

    def duckdns(self, sub, token):
        self.calls.append(("duckdns", sub))
        return OK

    def nntp(self, host, port, tls, user, password):
        self.calls.append(("nntp", host))
        return OK

    def newznab(self, url, key):
        self.calls.append(("newznab", url))
        return self.newznab_results.pop(0) if len(self.newznab_results) > 1 else self.newznab_results[0]

    def newznab_limits(self, url, key):
        return self.limit_result

    def tmdb(self, key):
        return OK

    def domain(self, name):
        self.calls.append(("domain", name))
        return OK

    def optional_names(self, name):
        return []

    def server_ip(self):
        return "203.0.113.10"


def provider_no(pid):
    from roadie.checks.nntp import known_providers
    return str([p["id"] for p in known_providers()].index(pid) + 1)


def lang_no(name, ui="en"):
    """Number of a content language in the list: interface language first, then English, then the rest."""
    first = {"en": ["English"], "de": ["German", "English"]}[ui]
    order = first + [x for x in content_languages() if x not in first]
    return str(order.index(name) + 1)


def german():
    return lang_no("German")


def run(lines, secrets, checks=None):
    out = io.StringIO()
    queue = list(secrets)
    ui = Prompter(inp=io.StringIO("\n".join(lines) + "\n"), out=out, secret_reader=lambda p: queue.pop(0))
    checks = checks or FakeChecks()
    data = ask_all(ui, checks)
    return data, out.getvalue(), checks


USENET_LINES = [
    "1",            # language en
    "",             # start page: ready
    "streamadmin",  # admin user
    "1",            # ssh key: I have one
    KEY,            # ssh key
    "1",            # web address: DuckDNS
    "Example.duckdns.org",  # duckdns
    provider_no("eweka"),  # provider from the list
    "user",         # usenet username
    "",             # connections (default)
    "n",            # another provider?
    "My indexer",   # indexer name
    "https://indexer.example.com/api",  # indexer url
    "",             # daily search limit: none
    "",             # daily download limit: none
    "n",            # another indexer?
    "n",            # tmdb key?
    f"{german()},1",  # content languages: German, English (English is first in an English UI)
    "", "1", "",    # original audio (default), quality: 4K first, hdr (default no)
    "1",            # people: only me
    "",             # auth username default
    "n",            # advanced settings? no
    "",             # backup key: create later
    "1",            # review: ok
]
USENET_SECRETS = [TOKEN, "usenetpw", "INDEXERKEY", "a-long-password-123", "a-long-password-123"]


class QuestionsTest(unittest.TestCase):
    def tearDown(self):
        i18n.set_language("en")

    def test_full_flow_usenet_only_is_valid(self):
        data, out, checks = run(USENET_LINES, USENET_SECRETS)
        a = Answers.from_dict(data)
        self.assertEqual(a.data["duckdns"]["subdomain"], "example")
        self.assertEqual(a.data["usenet"]["providers"][0]["host"], "news.eweka.nl")
        self.assertEqual(a.data["usenet"]["providers"][0]["role"], "primary")
        self.assertEqual(a.data["indexers"][0]["url"], "https://indexer.example.com")
        self.assertEqual(a.data["content"]["languages"], ["German", "English"])
        self.assertTrue(a.data["content"]["prefer_4k"])
        self.assertIsNone(a.data["backup"]["age_recipient"])
        self.assertEqual([c[0] for c in checks.calls], ["duckdns", "nntp", "newznab"])

    def test_tmdb_recommended_text(self):
        _, out, _ = run(USENET_LINES, USENET_SECRETS)
        flat = " ".join(out.split())
        self.assertIn(i18n.t("q.tmdb.help"), flat)
        for k in ("q.tmdb.step1", "q.tmdb.step2", "q.tmdb.step3"):
            self.assertIn(i18n.t(k), flat)
        self.assertIn("Recommended", flat)

    def test_secrets_not_echoed(self):
        _, out, _ = run(USENET_LINES, USENET_SECRETS)
        for s in USENET_SECRETS:
            self.assertNotIn(s, out)

    def test_failed_check_reasks(self):
        bad = CheckResult(False, "check.newznab.bad_key")
        lines = USENET_LINES[:14] + ["https://indexer.example.com", "", ""] + USENET_LINES[15:]
        lines[12] = "https://indexer.example.com"
        secrets = USENET_SECRETS[:3] + ["GOODKEY"] + USENET_SECRETS[3:]
        data, out, checks = run(lines, secrets, FakeChecks(newznab=[bad, OK]))
        self.assertEqual(data["indexers"][0]["api_key"], "GOODKEY")
        self.assertIn(i18n.t("check.newznab.bad_key"), " ".join(out.split()))  # output is word-wrapped

    def test_limits_read_from_indexer_are_not_asked(self):
        lines = USENET_LINES[:13] + USENET_LINES[15:]
        data, out, _ = run(lines, USENET_SECRETS, FakeChecks(limits=(100, 25)))
        self.assertEqual(data["indexers"][0]["daily_api_limit"], 100)
        self.assertEqual(data["indexers"][0]["daily_grab_limit"], 25)
        self.assertIn("100", out)

    def test_limits_asked_when_indexer_does_not_tell(self):
        lines = list(USENET_LINES)
        lines[13], lines[14] = "2000", "450"
        data, _, _ = run(lines, USENET_SECRETS)
        self.assertEqual(data["indexers"][0]["daily_api_limit"], 2000)
        self.assertEqual(data["indexers"][0]["daily_grab_limit"], 450)

    def test_second_indexer_with_small_limit_is_suggested_as_fallback(self):
        second = ["y", "Small", "https://small.example.com", "25", "", "n"]  # another, name, url, limits, no more
        lines = USENET_LINES[:15] + second + ["", ""] + USENET_LINES[16:]  # Enter takes both suggestions
        data, out, _ = run(lines, USENET_SECRETS[:3] + ["SMALLKEY"] + USENET_SECRETS[3:])
        self.assertEqual([ix.get("fallback") for ix in data["indexers"]], [False, True])
        self.assertIn(i18n.t("q.indexers.tiers"), " ".join(out.split()))

    def test_one_indexer_is_never_a_fallback(self):
        data, out, _ = run(USENET_LINES, USENET_SECRETS)
        self.assertNotIn("fallback", data["indexers"][0])
        self.assertNotIn(i18n.t("q.indexers.tiers"), " ".join(out.split()))

    def test_password_mismatch_reasks(self):
        secrets = USENET_SECRETS[:3] + ["a-long-password-123", "different-password-1", "b-long-password-123", "b-long-password-123"]
        data, out, _ = run(USENET_LINES, secrets)
        self.assertEqual(data["auth"]["password"], "b-long-password-123")
        self.assertIn(i18n.t("q.auth.mismatch"), out)

    def test_own_domain_flow(self):
        lines = list(USENET_LINES)
        lines[5:7] = ["2", "https://Example.com/"]  # own domain instead of DuckDNS name
        data, out, checks = run(lines, USENET_SECRETS[1:])  # no DuckDNS token asked
        a = Answers.from_dict(data)
        self.assertEqual(a.data["domain"], "example.com")
        self.assertNotIn("duckdns", a.data)
        self.assertEqual([c[0] for c in checks.calls], ["domain", "nntp", "newznab"])
        flat = " ".join(out.split())
        self.assertIn("203.0.113.10", flat)  # the address the A records must point to
        self.assertIn("auth.", flat)

    def test_backbone_warning_shown(self):
        lines = USENET_LINES[:10] + ["y", provider_no("easynews"), "user2", "", "n"] + USENET_LINES[11:]
        data, out, _ = run(lines, USENET_SECRETS[:2] + ["pw2"] + USENET_SECRETS[2:])
        self.assertEqual([p["role"] for p in data["usenet"]["providers"]], ["primary", "backup"])
        self.assertIn("Omicron", out)
        self.assertEqual(data["usenet"]["providers"][1]["provider"], "easynews")
        self.assertEqual(out.count(i18n.t("q.usenet.backbones.title")), 1)  # once, before the first backup


class NewQuestionsTest(unittest.TestCase):
    def tearDown(self):
        i18n.set_language("en")

    def test_generated_ssh_key_shown_once_and_only_public_part_kept(self):
        lines = list(USENET_LINES)
        lines[3:5] = ["2", "n", "j"]  # create a key; "saved?" no -> shown again -> yes
        data, out, _ = run(lines, USENET_SECRETS)
        a = Answers.from_dict(data)
        self.assertTrue(a.data["ssh_public_key"].startswith("ssh-ed25519 "))
        self.assertEqual(out.count("BEGIN OPENSSH PRIVATE KEY"), 2)
        self.assertNotIn("PRIVATE", json.dumps(a.to_dict(include_secrets=True)))


def de_lines():
    lines = list(USENET_LINES)
    lines[0] = "2"
    lines[lines.index(f"{german()},1")] = "1,2"  # German UI: German first, English second
    return lines


class BeginnerTest(unittest.TestCase):
    def tearDown(self):
        i18n.set_language("en")

    def test_parts_are_numbered(self):
        _, out, _ = run(de_lines(), USENET_SECRETS)
        self.assertIn("Teil 1 von 8", out)
        self.assertIn("Teil 8 von 8", out)
        self.assertNotIn("Teil 9", out)

    def test_start_page_lists_what_is_needed(self):
        _, out, _ = run(de_lines(), USENET_SECRETS)
        flat = " ".join(out.split())
        self.assertIn("DuckDNS", flat)
        self.assertIn("Indexer", flat)
        self.assertIn("Strg+C", flat)

    def test_review_hides_secrets(self):
        _, out, _ = run(USENET_LINES, USENET_SECRETS)
        self.assertIn("\u2022\u2022\u2022 (set)", out)
        self.assertIn("example.duckdns.org", out)
        for s in USENET_SECRETS:
            self.assertNotIn(s, out)

    def test_review_change_reasks_only_that_part(self):
        lines = USENET_LINES[:-1] + ["2", part_no("auth"), "otheruser", "1"]
        secrets = USENET_SECRETS + ["b-long-password-123", "b-long-password-123"]
        data, out, checks = run(lines, secrets)
        self.assertEqual(data["auth"], {"username": "otheruser", "password": "b-long-password-123"})
        self.assertEqual([c[0] for c in checks.calls], ["duckdns", "nntp", "newznab"])
        Answers.from_dict(data)

    def test_review_people_reasked_from_scratch(self):
        lines = list(USENET_LINES)
        lines[21] = "2"           # several people
        lines[22:22] = ["", "Anna", "", ""]  # main name default, Anna inherits, done
        lines[-1:] = ["2", part_no("people"), "1", "1"]  # change people -> only me -> ok
        data, _, _ = run(lines, USENET_SECRETS)
        self.assertEqual([p["id"] for p in data["people"]], ["main"])

    def test_review_back_reasks_nothing(self):
        from roadie.questions import PART_KEYS
        back = str(len(PART_KEYS) + 1)
        lines = USENET_LINES[:-1] + ["2", back, "1"]  # change something -> back -> ok
        data, out, checks = run(lines, USENET_SECRETS)
        self.assertEqual(out.count(i18n.t("review.heading")), 2)
        self.assertEqual([c[0] for c in checks.calls], ["duckdns", "nntp", "newznab"])
        self.assertIn(i18n.t("review.back"), out)
        Answers.from_dict(data)

    def test_name_check_rejects_control_characters(self):
        from roadie.questions import name_check
        check = name_check(set())
        self.assertIsNone(check("Anna"))
        self.assertIsNotNone(check("An\tna"))
        self.assertIsNotNone(check("An\x1bna"))

    def test_login_hint_quotes_match_language(self):
        self.assertIn("'tim'", i18n.t("q.server.login_as", user="tim"))
        i18n.set_language("de")
        self.assertIn("„tim“", i18n.t("q.server.login_as", user="tim"))

    def test_people_several_with_own_settings(self):
        lines = list(USENET_LINES)
        lines[21:22] = ["2", "Tim", "Anna", "2", german(), "", "2", "Ben", "", ""]
        data, out, _ = run(lines, USENET_SECRETS)
        Answers.from_dict(data)
        self.assertEqual([(p["id"], p["mode"]) for p in data["people"]],
                         [("main", "main"), ("anna", "own"), ("ben", "inherit")])
        self.assertEqual(data["people"][0]["name"], "Tim")
        self.assertEqual(data["people"][1]["content"]["languages"], ["German"])
        self.assertFalse(data["people"][1]["content"]["prefer_4k"])
        self.assertIn("(recommended)", out)

    def test_people_only_me_named_after_admin(self):
        data, _, _ = run(USENET_LINES, USENET_SECRETS)
        self.assertEqual(data["people"], [{"id": "main", "name": "streamadmin", "mode": "main"}])

    def test_main_person_default_is_admin_name(self):
        lines = list(USENET_LINES)
        lines[21:22] = ["2", "", ""]  # several people, main name default, nobody else
        data, out, _ = run(lines, USENET_SECRETS)
        self.assertEqual(data["people"][0]["name"], "streamadmin")
        self.assertIn("(Enter = streamadmin)", out)

    def test_provider_list_not_listed_option(self):
        from roadie.checks.nntp import known_providers
        lines = list(USENET_LINES)
        lines[7:8] = [str(len(known_providers()) + 1), "news.example.com", "", ""]  # last option, host, port, TLS
        data, out, _ = run(lines, USENET_SECRETS)
        p = data["usenet"]["providers"][0]
        self.assertEqual((p["host"], p["port"], p["tls"]), ("news.example.com", 563, True))
        self.assertNotIn("provider", p)
        self.assertIn(f"[{len(known_providers()) + 1}] {i18n.t('q.usenet.not_listed')}", out)
        self.assertIn("XS News", out)  # the plain full list, no search

    def test_provider_from_list(self):
        data, out, _ = run(USENET_LINES, USENET_SECRETS)
        self.assertEqual(data["usenet"]["providers"][0]["provider"], "eweka")
        self.assertEqual(data["usenet"]["providers"][0]["host"], "news.eweka.nl")

    def test_language_list_ui_language_first(self):
        _, out, _ = run(USENET_LINES, USENET_SECRETS)
        self.assertIn("[1] English", out)
        _, out, _ = run(de_lines(), USENET_SECRETS)
        self.assertIn("[1] Deutsch", out)
        self.assertIn("[2] Englisch", out)

    def test_few_connections_hint(self):
        _, out, _ = run(USENET_LINES, USENET_SECRETS)
        self.assertNotIn(i18n.t("q.usenet.few_connections"), out)
        lines = list(USENET_LINES)
        lines[9] = "2"
        data, out, _ = run(lines, USENET_SECRETS)
        self.assertEqual(data["usenet"]["providers"][0]["connections"], 2)
        self.assertIn(i18n.t("q.usenet.few_connections"), " ".join(out.split()))

    def test_narrow_language_hint(self):
        _, out, _ = run(USENET_LINES, USENET_SECRETS)
        self.assertNotIn(i18n.t("q.content.narrow"), " ".join(out.split()))
        lines = list(USENET_LINES)
        lines[17:19] = [german(), "n"]  # only German, no original audio
        data, out, _ = run(lines, USENET_SECRETS)
        self.assertEqual(data["content"]["languages"], ["German"])
        self.assertIn(i18n.t("q.content.narrow"), " ".join(out.split()))

    def test_weak_password_rejected(self):
        weak = ["admin1234567", "ADMINsecret-99", "123456789012", "abcdefghijklm"]  # login name, digits, letters
        secrets = USENET_SECRETS[:3] + weak + USENET_SECRETS[3:]
        data, out, _ = run(USENET_LINES, secrets)
        self.assertEqual(data["auth"]["password"], "a-long-password-123")
        self.assertEqual(" ".join(out.split()).count(i18n.t("answers.err.password_weak")), 4)

    def test_short_login_name_not_checked_in_password(self):
        from roadie.questions import password_problem
        self.assertIsNone(password_problem("ab-long-password-1", "ab"))
        self.assertIsNone(password_problem("a-long-password-1", ""))
        self.assertIsNotNone(password_problem("xtim-long-password-1", "Tim"))

    def test_review_shows_own_person_settings(self):
        lines = list(USENET_LINES)
        lines[21:22] = ["2", "Tim", "Anna", "2", lang_no("English"), "", "2", ""]
        _, out, _ = run(lines, USENET_SECRETS)
        review = " ".join(out.split()).split(i18n.t("review.heading"))[-1]
        self.assertIn("Anna", review)
        self.assertIn("English + Original", review)
        self.assertIn("1080p first", review)

    def test_language_names_localized(self):
        data, out, _ = run(de_lines(), USENET_SECRETS)
        self.assertIn("Englisch", out)
        self.assertEqual(data["content"]["languages"], ["German", "English"])

    def test_language_names_cover_every_language(self):
        names = json.loads((Path(__file__).parent.parent / "roadie/data/language_names.json").read_text())
        self.assertEqual(set(names["de"]), set(content_languages()))

    def test_advanced_skipped_uses_defaults(self):
        data, out, _ = run(USENET_LINES, USENET_SECRETS)
        self.assertEqual(data["maintenance"]["reboot_time"], "05:00")
        self.assertIsNone(data["notify"]["healthchecks_url"])
        self.assertNotIn(i18n.t("q.ops.healthchecks"), out)

    def test_advanced_asked_when_wanted(self):
        lines = list(USENET_LINES)
        lines[23:24] = ["y", "06:30", "https://hc-ping.com/abc"]
        data, _, _ = run(lines, USENET_SECRETS)
        self.assertEqual(data["maintenance"]["reboot_time"], "06:30")
        self.assertEqual(data["notify"]["healthchecks_url"], "https://hc-ping.com/abc")

    def test_admin_user_login_hint(self):
        _, out, _ = run(de_lines(), USENET_SECRETS)
        self.assertIn("streamadmin", " ".join(out.split()).split("ubuntu")[0])

def stored_answers():
    """Answers as "change settings" gets them: a finished setup, secrets restored."""
    data, _, _ = run(USENET_LINES, USENET_SECRETS)
    data["tmdb_api_key"] = "TMDBKEY"
    data["maintenance"]["reboot_time"] = "04:30"
    data["notify"]["healthchecks_url"] = "https://hc.example.com/ping/abc"
    data["people"] = [{"id": "main", "name": "Me", "mode": "main"}, {"id": "anna", "name": "Anna", "mode": "inherit"}]
    data = Answers.from_dict(data).to_dict(include_secrets=True)
    return {k: v for k, v in data.items() if not k.startswith("_")}  # markers stay in the state file


def change(lines, data, secrets=(), checks=None):
    from roadie.questions import change_settings
    out = io.StringIO()
    queue = list(secrets)
    ui = Prompter(inp=io.StringIO("\n".join(lines) + "\n"), out=out,
                  secret_reader=lambda p: (out.write(p + "\n"), queue.pop(0) if queue else "")[1])
    checks = checks or FakeChecks()
    return change_settings(ui, data, checks), out.getvalue(), checks


def part_no(key):
    from roadie.questions import PART_KEYS
    return str(PART_KEYS.index(key) + 1)


class ChangeSettingsTest(unittest.TestCase):
    """"Change settings": review first, one part prefilled (spec 4)."""

    def tearDown(self):
        i18n.set_language("en")

    def test_change_part_enter_keeps_everything(self):
        from roadie.questions import PART_KEYS
        stored = stored_answers()
        lines = []
        for key in PART_KEYS:
            lines += ["2", part_no(key)] + [""] * 30  # extra Enter at the review is just refused
        data, out, checks = change(lines + ["1"], copy.deepcopy(stored))
        self.assertEqual(data, stored)
        self.assertEqual([c[0] for c in checks.calls], ["duckdns", "nntp", "newznab"])
        self.assertEqual(out.count(i18n.t("review.heading")), len(PART_KEYS) + 1)
        for secret in ("TMDBKEY", "hc.example.com", TOKEN, "usenetpw", "INDEXERKEY",
                       "a-long-password-123"):
            self.assertNotIn(secret, out)
        self.assertNotIn(i18n.t("q.domain.change_warning"), out)
        self.assertIn(i18n.t("ui.keep_secret"), out)
        Answers.from_dict(data)

    def test_change_starts_at_review(self):
        stored = stored_answers()
        data, out, checks = change(["1"], copy.deepcopy(stored))
        self.assertEqual(data, stored)
        self.assertEqual(checks.calls, [])
        self.assertIn(i18n.t("review.heading"), out)
        self.assertNotIn(i18n.t("q.language"), out)

    def test_change_only_chosen_part_asked(self):
        stored = stored_answers()
        lines = ["2", part_no("auth"), "otheruser", "1"]
        data, out, checks = change(lines, copy.deepcopy(stored), ["b-long-password-123", "b-long-password-123"])
        self.assertEqual(data["auth"], {"username": "otheruser", "password": "b-long-password-123"})
        self.assertEqual({k: v for k, v in data.items() if k != "auth"},
                         {k: v for k, v in stored.items() if k != "auth"})
        self.assertNotIn(i18n.t("q.domain.subdomain"), out)
        self.assertNotIn(i18n.t("q.usenet.username"), out)
        self.assertEqual(checks.calls, [])

    def test_change_rechecks_part_credentials(self):
        stored = stored_answers()
        data, _, checks = change(["2", part_no("usenet")] + [""] * 30 + ["1"], copy.deepcopy(stored))
        self.assertEqual(checks.calls, [("nntp", "news.eweka.nl"), ("newznab", "https://indexer.example.com")])
        self.assertEqual(data, stored)

    def test_change_domain_warns(self):
        stored = stored_answers()
        data, out, checks = change(["2", part_no("domain"), "", "other", "1"], copy.deepcopy(stored))
        self.assertEqual(data["duckdns"], {"subdomain": "other", "token": TOKEN})
        self.assertEqual(checks.calls, [("duckdns", "other")])
        self.assertIn(i18n.t("q.domain.change_warning"), " ".join(out.split()))

    def test_change_to_own_domain_drops_duckdns(self):
        stored = stored_answers()
        data, out, checks = change(["2", part_no("domain"), "2", "Media.Example.COM", "1"], copy.deepcopy(stored))
        self.assertEqual(data["domain"], "media.example.com")
        self.assertNotIn("duckdns", data)
        self.assertEqual(checks.calls, [("domain", "media.example.com")])
        self.assertIn(i18n.t("q.domain.change_warning"), " ".join(out.split()))
        self.assertIn("aiostreams.", out)
        Answers.from_dict(data)

    def test_change_language_switches_ui_at_once(self):
        stored = stored_answers()
        data, out, _ = change(["2", part_no("basics"), "2", "1"], copy.deepcopy(stored))
        self.assertEqual(data["language"], "de")
        self.assertIn(i18n.load("de")["review.heading"], out)

    def test_change_people_points_to_people_menu(self):
        stored = stored_answers()
        data, out, _ = change(["2", part_no("people"), "1"], copy.deepcopy(stored))
        self.assertEqual(data["people"], stored["people"])
        self.assertIn("Manage people", " ".join(out.split()))
        self.assertNotIn(i18n.t("q.people.one"), out)

    def test_change_new_secret_replaces_old(self):
        stored = stored_answers()
        data, out, _ = change(["2", part_no("tmdb"), "", "1"], copy.deepcopy(stored), ["NEWTMDB"])
        self.assertEqual(data["tmdb_api_key"], "NEWTMDB")

    def test_long_tmdb_token_asks_for_the_short_key(self):
        token, short = "eyJ" + "a" * 200, "0123456789abcdef0123456789abcdef"
        data, out, _ = change(["2", part_no("tmdb"), "", "", "1"], copy.deepcopy(stored_answers()), [token, short])
        self.assertIn(i18n.t("q.tmdb.token_given"), " ".join(out.split()))
        self.assertEqual(data["tmdb_api_key"], short)
        data, _, _ = change(["2", part_no("tmdb"), "", "n", "1"], copy.deepcopy(stored_answers()), [token])
        self.assertEqual(data["tmdb_api_key"], token)  # the token still works for AIOStreams

    def test_change_server_keeps_admin_user_adds_key(self):
        stored = stored_answers()
        new_key = KEY.split()[0] + " " + KEY.split()[1][:-4] + "AAAA"
        data, out, _ = change(["2", part_no("server"), "2", new_key, "1"], copy.deepcopy(stored))
        self.assertEqual(data["admin_user"], stored["admin_user"])
        self.assertTrue(data["ssh_public_key"].startswith(new_key))
        text = " ".join(out.split())
        self.assertIn(i18n.t("q.server.admin_user_fixed", user="streamadmin"), text)
        self.assertIn(i18n.t("q.server.ssh_added"), text)
        self.assertNotIn(i18n.t("q.server.admin_user"), text)

    def test_change_indexer_url_needs_new_key(self):
        stored = stored_answers()
        lines = ["2", part_no("usenet"), "", "", "", "", "", "https://other.example.com/api"] + [""] * 6 + ["1"]
        data, out, _ = change(lines, copy.deepcopy(stored), ["", "", "NEWKEY"])  # pw kept, empty key refused
        self.assertEqual(data["indexers"][0]["url"], "https://other.example.com")
        self.assertEqual(data["indexers"][0]["api_key"], "NEWKEY")
        self.assertIn(i18n.t("ui.required"), out)

    def test_change_indexer_limit_zero_removes_it(self):
        stored = stored_answers()
        stored["indexers"][0]["daily_api_limit"] = 100
        lines = ["2", part_no("usenet")] + [""] * 6 + ["0"] + [""] * 10 + ["1"]
        data, out, _ = change(lines, copy.deepcopy(stored))
        self.assertIsNone(data["indexers"][0]["daily_api_limit"])
        self.assertIn(i18n.t("q.indexers.limit_zero"), " ".join(out.split()))

    def test_change_login_name_rechecks_kept_password(self):
        stored = stored_answers()
        data, out, _ = change(["2", part_no("auth"), "long", "1"], copy.deepcopy(stored),
                              ["", "Xy-safe-pass-9876", "Xy-safe-pass-9876"])
        self.assertEqual(data["auth"], {"username": "long", "password": "Xy-safe-pass-9876"})
        self.assertIn(i18n.t("answers.err.password_weak"), " ".join(out.split()))

    def test_change_healthchecks_dash_removes_it(self):
        stored = stored_answers()
        data, out, _ = change(["2", part_no("operations"), "", "", "", "1"], copy.deepcopy(stored), ["-"])
        self.assertIsNone(data["notify"]["healthchecks_url"])
        self.assertEqual(data["maintenance"]["reboot_time"], "04:30")
        self.assertIn(i18n.t("q.ops.healthchecks_change"), out)

    def test_change_review_shows_created_backup_key(self):
        stored = stored_answers()
        stored["_age_recipient"] = "age1" + "q" * 58
        data, out, _ = change(["1"], copy.deepcopy(stored))
        self.assertIn(i18n.t("review.backup_key", value=i18n.t("review.backup_present")), out)
        self.assertNotIn("_age_recipient", data)
