import json
import unittest
from pathlib import Path

from roadie.answers import Answers
from roadie.render import build_render_config, load_versions
from roadie.render.traefik import dynamic_dict, render_traefik, static_dict

EXAMPLE = json.loads((Path(__file__).parent.parent / "examples/answers.example.json").read_text())


def cfg(**changes):
    data = json.loads(json.dumps(EXAMPLE))
    data.update(changes)
    return build_render_config(Answers.from_dict(data), load_versions(), mem_mb=4000, disk_gb=60)


class TraefikTest(unittest.TestCase):
    def test_no_dashboard_no_docker_provider(self):
        s = static_dict(cfg())
        self.assertNotIn("api", s)
        self.assertEqual(list(s["providers"]), ["file"])

    def test_no_telemetry(self):
        g = static_dict(cfg())["global"]
        self.assertEqual((g["checkNewVersion"], g["sendAnonymousUsage"]), (False, False))

    def test_http_redirects_to_https(self):
        web = static_dict(cfg())["entryPoints"]["web"]
        self.assertEqual(web["http"]["redirections"]["entryPoint"]["to"], "websecure")

    def test_access_log_unbuffered(self):
        log = static_dict(cfg())["accessLog"]
        self.assertNotIn("bufferingSize", log)
        self.assertEqual(log["filePath"], "/data/access.log")

    def test_encoded_characters_allowed_except_null(self):
        enc = static_dict(cfg())["entryPoints"]["websecure"]["http"]["encodedCharacters"]
        self.assertTrue(enc["allowEncodedSlash"])
        self.assertTrue(enc["allowEncodedPercent"])
        self.assertNotIn("allowEncodedNullCharacter", enc)

    def test_every_router_tls(self):
        for name, r in dynamic_dict(cfg())["http"]["routers"].items():
            self.assertEqual(r["tls"], {"certResolver": "letsencrypt"}, name)
            self.assertEqual(r["entryPoints"], ["websecure"], name)

    def test_admin_routes_have_auth(self):
        routers = dynamic_dict(cfg())["http"]["routers"]
        for name, r in routers.items():
            if name != "authelia":
                self.assertIn("authelia", r["middlewares"], name)
        self.assertEqual(routers["aiostreams"]["rule"], "Host(`aiostreams.example.duckdns.org`)")

    def test_prowlarr_route(self):
        self.assertIn("prowlarr", dynamic_dict(cfg())["http"]["routers"])

    def test_security_headers(self):
        h = dynamic_dict(cfg())["http"]["middlewares"]["security-headers"]["headers"]
        self.assertGreaterEqual(h["stsSeconds"], 31536000)
        self.assertTrue(h["contentTypeNosniff"])

    def test_test_mode_uses_self_signed_certificate(self):
        c = cfg()
        c.test_mode = True
        self.assertNotIn("certificatesResolvers", static_dict(c))
        self.assertEqual(dynamic_dict(c)["http"]["routers"]["aiostreams"]["tls"], {})

    def test_files(self):
        paths = {f.path for f in render_traefik(cfg())}
        self.assertEqual(paths, {"config/traefik/traefik.yml", "config/traefik/routes.yml"})
