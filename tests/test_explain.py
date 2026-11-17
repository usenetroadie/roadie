"""sudo roadie explain: real values of this server, every topic in both languages."""

import io
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from roadie import explain, i18n
from roadie.ops.common import Env
from roadie.paths import Paths
from roadie.secretstore import SecretStore
from roadie.state import State
from roadie.ui import Prompter

COMPOSE = "name: roadie\nservices:\n  traefik:\n    image: a\n  aiostreams:\n    image: b\n  mytool:\n    image: c\nnetworks:\n  roadie:\n    name: roadie\n"


class ExplainTest(unittest.TestCase):
    def setUp(self):
        i18n.set_language("en")
        self.addCleanup(i18n.set_language, "en")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        paths = Paths(Path(self.tmp.name))
        state = State(paths.state_file)
        state.answers.update({"duckdns": {"subdomain": "example"}, "admin_user": "streamadmin",
                              "_ssh_ports": [2222], "maintenance": {"reboot_time": "04:30"}})
        self.env = Env(paths, state, SecretStore(paths.secrets_file))
        paths.stack_dir.mkdir(parents=True)
        (paths.stack_dir / "compose.yaml").write_text(COMPOSE)
        self.patch = mock.patch("roadie.ops.backup.backup_dir", return_value=Path("/home/streamadmin/backups"))
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def text(self, topic, lang="en"):
        i18n.set_language(lang)
        out = io.StringIO()
        explain.run(self.env, Prompter(inp=io.StringIO(""), out=out), topic)
        return out.getvalue()

    def test_every_topic_in_both_languages_with_this_servers_values(self):
        for lang in ("en", "de"):
            text = self.text("all", lang)
            for value in ("aiostreams.example.duckdns.org", "streamadmin", "2222", "04:30",
                          str(self.env.paths.stack_dir), "/home/streamadmin/backups"):
                self.assertIn(value, text, (lang, value))
            self.assertNotIn("{", text, lang)  # no placeholder left
            for name in explain.TOPICS:
                if name != "services":
                    self.assertIn(i18n.t(f"explain.{name}.title"), text)

    def test_services_are_the_ones_in_compose_yaml(self):
        text = self.text("services")
        self.assertIn(i18n.t("explain.svc.traefik.name"), text)
        self.assertIn(i18n.t("explain.svc.aiostreams.name"), text)
        self.assertIn("https://aiostreams.example.duckdns.org", text)
        self.assertNotIn(i18n.t("explain.svc.prowlarr.name"), text)  # not in this compose.yaml
        self.assertIn("mytool", text)
        self.assertIn(i18n.t("explain.svc.unknown"), text)
        self.assertIn("sudo docker compose logs --tail 50 aiostreams", text)

    def test_every_known_service_has_its_texts(self):
        for lang in ("en", "de"):
            catalog = i18n.load(lang)
            for name in explain.SERVICES:
                for part in ("name", "what", "files"):
                    self.assertIn(f"explain.svc.{name}.{part}", catalog, (lang, name, part))

    def test_duckdns_job_only_with_duckdns(self):
        self.assertIn("roadie-duckdns", self.text("jobs"))
        self.env.answers.pop("duckdns")
        self.env.answers["domain"] = "example.com"
        self.assertNotIn("roadie-duckdns", self.text("jobs"))

    def test_commands_are_copyable_lines(self):
        out = io.StringIO()
        ui = Prompter(inp=io.StringIO(""), out=out)
        ui.width = 40  # narrow: a command must still stay on one line
        explain.run(self.env, ui, "docker")
        self.assertIn("\nsudo docker compose logs --tail 50 aiostreams\n", out.getvalue())

    def test_without_a_terminal_everything_is_printed(self):
        text = self.text(None)
        self.assertIn(i18n.t("explain.security.title"), text)
        self.assertIn(i18n.t("explain.overview.title"), text)

    def test_lang_option_wins_over_the_setup_language(self):
        from roadie import cli
        self.env.answers["language"] = "en"
        self.env.state.save()
        out = io.StringIO()
        cli.main(["--root", self.tmp.name, "--lang", "de", "explain", "jobs"],
                 ui=Prompter(inp=io.StringIO(""), out=out))
        self.assertIn(i18n.load("de")["explain.jobs.title"], out.getvalue())

    def test_cli_and_menu(self):
        from roadie import cli, menu
        self.assertEqual(cli.ALIASES["erklaeren"], "explain")
        self.assertIn("explain", [item.key for item in menu.ITEMS])


if __name__ == "__main__":
    unittest.main()
