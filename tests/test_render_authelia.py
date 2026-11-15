import json
import re
import unittest
from pathlib import Path

from roadie.answers import Answers
from roadie.render import build_render_config, load_versions
from roadie.render.authelia import BYPASS_PATHS, PROTECTED_PATHS, config_dict, render_authelia

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
        rules = config_dict(cfg())["access_control"]["rules"]
        bypass = [r for r in rules if r["policy"] == "bypass"]
        self.assertEqual(len(bypass), 1)
        self.assertEqual(bypass[0]["methods"], ["GET", "HEAD", "OPTIONS"])
        self.assertEqual(bypass[0]["domain"], ["aiostreams.example.duckdns.org"])

    def test_configure_page_is_protected_before_bypass(self):
        rules = config_dict(cfg())["access_control"]["rules"]
        self.assertEqual(rules[0]["policy"], "two_factor")
        self.assertEqual(rules[0]["resources"], PROTECTED_PATHS)

    def test_stremio_paths(self):
        uuid_path = "/stremio/0b7e-uuid/ENCRYPTED/"
        for path in (uuid_path + "manifest.json", uuid_path + "stream/movie/tt1.json",
                     "/stremio/manifest.json", "/api/v1/proxy/abc", "/api/v1/usenet/x", "/api/v1/debrid/playback/x"):
            self.assertTrue(matches(BYPASS_PATHS, path), path)
        for path in ("/stremio/configure", uuid_path + "configure", uuid_path + "configure/x"):
            self.assertTrue(matches(PROTECTED_PATHS, path), path)
        for path in ("/", "/api/v1/user", "/api/v1/dashboard", "/stremiox/"):
            self.assertFalse(matches(BYPASS_PATHS, path), path)

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
