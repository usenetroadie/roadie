import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

from roadie.answers import Answers
from roadie.render import build_render_config, load_versions
from roadie.render.authelia import AIOSTREAMS_PLAYER_PATHS, METADATA_IMAGE_PATHS, METADATA_PLAYER_PATHS, PROTECTED_PATHS, config_dict, read_own_rules, render_authelia

EXAMPLE = json.loads((Path(__file__).parent.parent / "examples/answers.example.json").read_text())
SECRETS = {"authelia_jwt_secret": "J" * 64, "authelia_session_secret": "S" * 64, "authelia_storage_key": "K" * 64}
HASH = "$argon2id$v=19$m=65536,t=3,p=4$c2FsdHNhbHQ$aGFzaGhhc2g"


def cfg():
    return build_render_config(Answers.from_dict(EXAMPLE), load_versions(), mem_mb=4000, disk_gb=60)


def matches(patterns, path):
    return any(re.search(p, path) for p in patterns)


class AutheliaTest(unittest.TestCase):
    def test_default_policy_deny(self):
        self.assertEqual(config_dict(cfg())["access_control"]["default_policy"], "deny")

    def test_authelia_forward_auth_cookie_only(self):
        # AIOStreams' page sends "Authorization: Basic"; with header logins allowed Authelia
        # answers with WWW-Authenticate and the browser opens a login popup
        authz = config_dict(cfg())["server"]["endpoints"]["authz"]
        self.assertEqual(authz, {"forward-auth": {"implementation": "ForwardAuth",
                                                  "authn_strategies": [{"name": "CookieSession"}]}})

    def test_bypass_only_safe_methods(self):
        c = cfg()
        c.metadata = True
        rules = config_dict(c)["access_control"]["rules"]
        bypass = [r for r in rules if r["policy"] == "bypass" and "methods" in r]
        self.assertEqual(len(bypass), 2)
        for rule in bypass:
            self.assertEqual(rule["methods"], ["GET", "HEAD", "OPTIONS"])
        self.assertEqual(bypass[0]["domain"], ["aiostreams.example.duckdns.org"])
        self.assertEqual(bypass[1]["domain"], ["metadata.example.duckdns.org"])

    def test_jellyfin_open_all_methods_on_aiostreams_only(self):
        # apps log in with POST and use websockets; every route of the API checks the login itself
        rules = config_dict(cfg())["access_control"]["rules"]
        jelly = [r for r in rules if r.get("resources") == ["^/jellyfin([/?]|$)"]]
        self.assertEqual(jelly, [{"domain": ["aiostreams.example.duckdns.org"],
                                  "resources": ["^/jellyfin([/?]|$)"], "policy": "bypass"}])
        self.assertTrue(matches(jelly[0]["resources"], "/jellyfin"))
        self.assertTrue(matches(jelly[0]["resources"], "/jellyfin/u/alias"))
        self.assertTrue(matches(jelly[0]["resources"], "/jellyfin?x=1"))
        self.assertTrue(matches(jelly[0]["resources"], "/jellyfin/users?x=1"))
        for path in ("/jellyfinx", "/api/v1/jellyfin", "//jellyfin", "/jellyfin2?x=1"):
            self.assertFalse(matches(jelly[0]["resources"], path), path)
        order = [r["policy"] for r in rules]
        self.assertLess(rules.index(jelly[0]), len(rules) - 1)  # before the catch-all
        self.assertEqual(order[-1], "two_factor")

    def test_metadata_images_open_settings_closed(self):
        # posters through AIOMetadata's RPDB proxy or image cache must load in the player
        for path in ("/poster/movie/tt0111161?fallback=x&lang=en&key=t0-free-rpdb", "/logo/series/tmdb:1",
                     "/background/movie/tt1", "/poster-cache/proxy/poster/movie/tt1", "/poster-cache/w342/ab.jpg",
                     "/api/image/blur?url=x"):
            self.assertTrue(matches(METADATA_IMAGE_PATHS, path), path)
        for path in ("/", "/configure", "/dashboard", "/api/config/save", "/stremio/uuid/configure",
                     "/api/dashboard/poster-cache/stats", "/postermaker"):
            self.assertFalse(matches(METADATA_IMAGE_PATHS, path), path)

    def test_metadata_player_routes_open_settings_closed(self):
        for path in ("/stremio/u-1/manifest.json", "/stremio/u-1/manifest.json?x=1", "/stremio/x/manifest.json?v=1",
                     "/stremio/u-1/catalog/movie/top.json", "/stremio/u-1/catalog/movie/top/skip=20.json",
                     "/stremio/u-1/catalog/movie/top/genre=Action&skip=20.json?a=b",
                     "/stremio/u-1/meta/movie/tt1.json", "/stremio/u-1/meta/series/tmdb:1.json?x=1",
                     "/stremio/u-1/stream/movie/tt1.json", "/stremio/u-1/subtitles/movie/tt1.json",
                     "/stremio/u-1/subtitles/movie/tt1/videoHash=ab.json", "/stremio/u-1/watch_state/pull.json",
                     "/stremio/u-1/rating", "/stremio/u-1/rating?id=tt1",
                     "/stremio/u-1/catalog/movie/top/search=AC/DC.json"):
            self.assertTrue(matches(METADATA_PLAYER_PATHS, path), path)
        for path in ("/stremio/u/configure", "/stremio/u/configure/x", "/stremio/u/manifest.json/../configure",
                     "/stremio/configure", "/api/config/save", "/dashboard", "/", "/configure",
                     "/stremio/u/catalog/movie/x.json/../../configure", "/stremio/u/meta/../configure/x.json",
                     "/stremio/u/rating/x", "/stremio/u/catalog/movie.json",
                     "/stremio/x/manifest.json?/configure", "/stremio/x/rating?/configure",
                     "/stremio/x/catalog/movie/top.json?/../configure",
                     "/stremio/x/catalog/movie/top/search=why?.json"):  # "?" in a search: refused (fail closed)
            self.assertFalse(matches(METADATA_PLAYER_PATHS, path), path)

    def test_no_metadata_rule_without_metadata(self):
        c = cfg()
        c.metadata = False
        hosts = [h for r in config_dict(c)["access_control"]["rules"] for h in r["domain"]]
        self.assertNotIn("metadata.example.duckdns.org", hosts)

    def test_configure_page_is_protected_before_bypass(self):
        rules = config_dict(cfg())["access_control"]["rules"]
        self.assertEqual(rules[0]["policy"], "two_factor")
        self.assertEqual(rules[0]["resources"], PROTECTED_PATHS)

    def test_stremio_paths(self):
        base = "/stremio/0b7e5c3a-1f2d-4e6b-9a8c-7d6e5f4a3b2c/eyJpdiI6IkFCQyIsImRhdGEiOiJ4In0"
        for path in ("/stremio/manifest.json", base + "/manifest.json", base + "/manifest.json?v=kids",
                     base + "/v/kids/manifest.json", base + "/v/kids/stream/movie/tt0111161.json",
                     base + "/catalog/movie/abc123.tmdb.trending.json", base + "/catalog/movie/top/skip=100.json",
                     base + "/catalog/series/top/genre=Action&skip=20.json", base + "/meta/movie/tt0111161.json",
                     base + "/stream/movie/tt0111161.json", base + "/stream/series/tt0111161:1:2.json",
                     base + "/subtitles/movie/tt0111161.json",
                     base + "/subtitles/movie/tt0111161/videoHash=ab12&videoSize=1000&filename=Movie.2020.mkv.json",
                     base + "/addon_catalog/addon/top.json", base + "/catalog/movie/x/search=AC/DC.json",
                     base + "/catalog/movie/x/search=AC/DC&skip=20.json",
                     "/stremio/u/family/manifest.json", "/stremio/u/family/stream/movie/tt0111161.json"):
            self.assertTrue(matches(AIOSTREAMS_PLAYER_PATHS, path), path)
        for path in ("/stremio/configure", base + "/configure", base + "/configure/x", base + "/v/kids/configure"):
            self.assertTrue(matches(PROTECTED_PATHS, path), path)
        # a search with "?" decodes to a query start inside the path: refused on purpose (fail closed)
        self.assertFalse(matches(AIOSTREAMS_PLAYER_PATHS, base + "/catalog/movie/x/search=why?.json"))
        self.assertFalse(matches(AIOSTREAMS_PLAYER_PATHS, base + "/catalog/movie/x/search=a/../../configure.json"))

    def test_playback_paths(self):
        for path in ("/api/v1/usenet/stream/eyJ0b2tlbiI6IngifQ",
                     "/api/v1/usenet/stream/eyJ0b2tlbiI6IngifQ/Movie.2020.1080p.WEB-DL.mkv",
                     "/api/v1/usenet/stream/eyJ0b2tlbiI6IngifQ?download=1",
                     "/api/v1/debrid/playback/ENCAUTH/FALLBACK/FILEINFO/tt0111161:1:2/Movie.2020.mkv",
                     "/api/v1/debrid/playback/ENCAUTH/FILEINFO/tt0111161/Movie.2020.mkv",
                     "/api/v1/proxy/" + "A" * 40, "/api/v1/proxy/" + "A" * 40 + "/Movie.2020.mkv",
                     "/api/v1/proxy/e." + "A" * 40 + ".B/",  # stream without a file name
                     "/api/v1/posters/rpdb?id=tt0111161&type=movie&fallback=https%3A%2F%2Fimage.tmdb.org%2Fx.jpg",
                     "/static/downloading.mp4", "/static/no_matching_file.mp4"):
            self.assertTrue(matches(AIOSTREAMS_PLAYER_PATHS, path), path)

    def test_private_aiostreams_paths_stay_closed(self):
        for path in ("/", "/stremio/configure", "/stremio/a/b/configure", "/stremio/a/b/c/configure",
                     "/stremio/a/b/v/c/configure", "/stremio/a/b/manifest.json?/configure",
                     "/stremio/a/b/catalog/movie/x.json?/../configure", "/stremio/a/../../dashboard",
                     "/stremio/a/b/manifest.json/../configure", "/stremio/../api/v1/user/manifest.json",
                     "/stremio/a/b/meta/movie/x/y.json", "/stremio/a/b/stream/movie.json",
                     "/stremio/stream/movie/tt1.json", "/stremiox/manifest.json", "/stremio/manifest.jsonx",
                     "/api/v1/user", "/api/v1/dashboard", "/api/v1/templates", "/api/v1/profiles",
                     "/dashboard", "/api/v1/proxy/short", "/api/v1/proxy/generate",
                     "/api/v1/proxy/" + "A" * 40 + "/../../user", "/api/v1/usenet/stream/x/../../user",
                     "/api/v1/usenet/stream/x?/../../user", "/api/v1/usenet/other",
                     "/api/v1/debrid/playback/a/../../../user", "/api/v1/posters/../user",
                     "/api/v1/posters/rpdb/x", "/static/../app.js", "/static/x.mp4/../y",
                     "/builtins/library/refresh/x/y", "/chilllink/a/b/manifest"):
            self.assertFalse(matches(AIOSTREAMS_PLAYER_PATHS, path), path)

    def test_passkey_settings(self):
        w = config_dict(cfg())["webauthn"]
        self.assertTrue(w["enable_passkey_login"])
        self.assertTrue(w["experimental_enable_passkey_uv_two_factors"])
        self.assertEqual(w["selection_criteria"], {"discoverability": "required", "user_verification": "required"})

    def test_sqlite_and_file_notifier(self):
        c = config_dict(cfg())
        self.assertIn("local", c["storage"])
        self.assertIn("filesystem", c["notifier"])
        self.assertNotIn("redis", c["session"])

    def test_no_secrets_in_config_but_in_secret_files(self):
        files = {f.path: f for f in render_authelia(cfg(), SECRETS, HASH)}
        config = files["config/authelia/configuration.yml"].content
        for value in SECRETS.values():
            self.assertNotIn(value, config)
        self.assertEqual(files["secrets/authelia/session_secret"].content, "S" * 64)
        self.assertEqual(files["secrets/authelia/session_secret"].mode, 0o600)
        self.assertIn("AUTHELIA_SESSION_SECRET_FILE", files["env/authelia.env"].content)

    def test_no_plaintext_password(self):
        users = {f.path: f for f in render_authelia(cfg(), SECRETS, HASH)}["config/authelia/users.yml"]
        self.assertIn(HASH, users.content)
        self.assertNotIn(EXAMPLE["auth"]["password"], users.content)
        self.assertEqual(users.mode, 0o600)

    def test_rejects_non_argon_hash(self):
        with self.assertRaises(ValueError):
            render_authelia(cfg(), SECRETS, "plaintext")

    def test_session_cookie_domain(self):
        cookie = config_dict(cfg())["session"]["cookies"][0]
        self.assertEqual(cookie["domain"], "example.duckdns.org")
        self.assertEqual(cookie["authelia_url"], "https://auth.example.duckdns.org")


class OwnRulesTest(unittest.TestCase):
    def read(self, text):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "own-rules.yml"
            path.write_text(text)
            return read_own_rules(path)

    def test_own_rules_before_catch_all(self):
        mine = [{"domain": "aiostreams.example.duckdns.org", "resources": ["^/x"], "policy": "bypass"},
                {"domain": ["a.example.org"], "policy": "one_factor"}]
        rules = config_dict(cfg(), mine)["access_control"]["rules"]
        self.assertEqual(rules[-3:-1], mine)
        self.assertEqual(rules[-1]["policy"], "two_factor")
        jelly = next(i for i, r in enumerate(rules) if r.get("resources") == ["^/jellyfin([/?]|$)"])
        self.assertLess(jelly, len(rules) - 3)
        self.assertEqual(config_dict(cfg())["access_control"]["rules"], config_dict(cfg(), ())["access_control"]["rules"])

    def test_missing_file_is_fine(self):
        self.assertEqual(read_own_rules(Path("/nonexistent/own-rules.yml")), ([], None))

    def test_good_file_read(self):
        rules, reason = self.read("- domain: [a.example.org]\n  policy: bypass\n  resources: ['^/x']\n"
                                  "- domain: b.example.org\n  policy: one_factor\n")
        self.assertIsNone(reason)
        self.assertEqual(len(rules), 2)

    def test_bad_own_rules_ignored_with_reason(self):
        for text in ("", "domain: x\npolicy: bypass\n", "- policy: bypass\n", "- domain: x\n",
                     "- domain: [x, 3]\n  policy: bypass\n", "- domain: x\n  policy: 5\n", "- just text\n",
                     "- domain: [unclosed\n",
                     "- domain: x\n  policy: allow\n", "- domain: ''\n  policy: bypass\n",
                     "- domain: []\n  policy: bypass\n", "- domain: ['']\n  policy: deny\n"):
            rules, reason = self.read(text)
            self.assertEqual(rules, [], text)
            self.assertTrue(reason, text)

    def test_without_python3_yaml(self):
        saved = sys.modules.get("yaml")
        sys.modules["yaml"] = None  # makes "import yaml" raise ImportError
        try:
            self.assertEqual(self.read("- domain: x\n  policy: bypass\n"), ([], "python3-yaml missing"))
        finally:
            if saved is None:
                del sys.modules["yaml"]
            else:
                sys.modules["yaml"] = saved
