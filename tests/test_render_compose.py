import json
import unittest
from pathlib import Path

from roadie.answers import Answers
from roadie.render import build_render_config, load_versions
from roadie.render.compose import compose_dict, render_compose

EXAMPLE = json.loads((Path(__file__).parent.parent / "examples/answers.example.json").read_text())


def cfg(**changes):
    data = json.loads(json.dumps(EXAMPLE))
    data.update(changes)
    return build_render_config(Answers.from_dict(data), load_versions(), mem_mb=4000, disk_gb=60)


class ComposeTest(unittest.TestCase):
    def test_no_latest_tags(self):
        for name, comp in load_versions().items():
            self.assertNotIn(comp["tag"], ("", "latest"), name)
        for svc in compose_dict(cfg())["services"].values():
            self.assertNotRegex(svc["image"], r":latest$|^[^:]+$")

    def test_only_traefik_publishes_ports(self):
        services = compose_dict(cfg())["services"]
        self.assertEqual(services["traefik"]["ports"], ["0.0.0.0:80:80", "0.0.0.0:443:443"])
        for name, svc in services.items():
            if name != "traefik":
                self.assertNotIn("ports", svc, name)

    def test_ipv6_binding_optional(self):
        c = cfg()
        c.ipv6 = True
        self.assertIn("[::]:443:443", compose_dict(c)["services"]["traefik"]["ports"])

    def test_no_docker_socket(self):
        text = render_compose(cfg())[0].content
        self.assertNotIn("docker.sock", text)

    def test_every_service_has_limits_and_restart(self):
        for name, svc in compose_dict(cfg())["services"].items():
            self.assertEqual(svc["restart"], "always", name)
            self.assertIn("mem_limit", svc, name)
            self.assertEqual(svc["logging"]["driver"], "json-file", name)
            self.assertIn("max-size", svc["logging"]["options"], name)
            self.assertIn("healthcheck", svc, name)

    def test_prowlarr_service(self):
        self.assertIn("prowlarr", compose_dict(cfg())["services"])

    def test_small_server_gets_smaller_limit(self):
        c = cfg()
        c.mem_mb = 1900
        mb = lambda conf: int(compose_dict(conf)["services"]["aiostreams"]["mem_limit"].rstrip("m"))
        self.assertLess(mb(c), mb(cfg()))

    def test_no_secret_values_in_compose(self):
        text = render_compose(cfg())[0].content
        for secret in Answers.from_dict(EXAMPLE).secret_values():
            self.assertNotIn(secret, text)

    def test_compose_file_mode_and_path(self):
        f = render_compose(cfg())[0]
        self.assertEqual((f.path, f.mode), ("compose.yaml", 0o644))
