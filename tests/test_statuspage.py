"""The status page: one static HTML file, nothing secret, never breaks the watcher."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from roadie import i18n
from roadie.ops import statuspage
from roadie.ops.common import Env
from roadie.paths import Paths
from roadie.render import build_render_config, load_versions
from roadie.render.authelia import render_authelia
from roadie.render.compose import compose_dict
from roadie.render.traefik import dynamic_dict
from roadie.secretstore import SecretStore
from roadie.state import State

UP = [{"Service": "aiostreams", "State": "running", "Health": "healthy"},
      {"Service": "traefik", "State": "running", "Health": "healthy"}]


class StatusPageTest(unittest.TestCase):
    def setUp(self):
        i18n.set_language("en")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        paths = Paths(Path(self.tmp.name))
        state = State(paths.state_file)
        state.answers.update({"duckdns": {"subdomain": "example"}})
        self.env = Env(paths, state, SecretStore(paths.secrets_file))
        self.env.secrets.set("prowlarr_api_key", "SECRETKEY12345")

    def page(self, services=UP):
        with mock.patch.object(Env, "services", return_value=services):
            return statuspage.render(self.env, 1_800_000_000)

    def test_status_page_links_backup(self):
        folder = self.env.paths.stack_dir / "data/status/backup"
        folder.mkdir(parents=True)
        (folder / "roadie-backup-2026-11-19.tar.age").write_bytes(b"x" * 3_000_000)
        text = self.page()
        self.assertIn('href="backup/roadie-backup-2026-11-19.tar.age"', text)
        self.assertIn("3 MB", text)

    def test_status_page_without_backup_no_link(self):
        self.assertNotIn('href="backup/', self.page())

    def test_all_fine(self):
        text = self.page()
        self.assertIn(i18n.t("page.all_fine"), text)
        self.assertIn("aiostreams", text)
        self.assertIn(i18n.t("status.no_backup"), text)
        self.assertNotIn("<script", text)
        self.assertIn('http-equiv="refresh"', text)

    def test_problems_with_their_fix_escaped_and_without_secrets(self):
        self.env.save_json("watch.json", {"problems": {
            "indexer:x": {"key": "watch.indexer_disabled", "notified_at": 1,
                          "params": {"name": "<b>Evil</b> SECRETKEY12345"}},
            "ram": {"key": "watch.ram_low", "params": {"mb": 100}}}})  # not reported yet: not shown
        text = self.page()
        self.assertIn(i18n.t("page.needs_look"), text)
        self.assertIn("&lt;b&gt;Evil&lt;/b&gt;", text)
        self.assertNotIn("<b>Evil", text)
        self.assertNotIn("SECRETKEY12345", text)
        self.assertIn(i18n.t("watch.indexer_disabled.fix"), text)
        self.assertNotIn("100 MB", text)

    def test_stopped_service_and_backup_and_new_version(self):
        self.env.save_json("backup.json", {"time": "2026-11-15T04:07:00", "file": "/home/x/backups/b.tar.age"})
        self.env.save_json("watch.json", {"selfupdate": {"notified": "v99.0.0"}})
        text = self.page(UP + [{"Service": "prowlarr", "State": "exited"}])
        self.assertIn(i18n.t("page.not_running"), text)
        self.assertIn(i18n.t("page.needs_look"), text)
        self.assertIn("2026-11-15 04:07:00", text)
        self.assertNotIn("/home/x", text)  # where the backup lies is nobody's business in a browser
        self.assertIn(i18n.t("page.update_available", version="99.0.0"), text)

    def test_write_is_world_readable_for_the_web_server_and_never_raises(self):
        with mock.patch.object(Env, "services", return_value=UP):
            statuspage.write(self.env)
        target = self.env.paths.stack_dir / statuspage.FILE
        self.assertEqual(target.stat().st_mode & 0o777, 0o644)
        self.assertEqual(target.parent.stat().st_mode & 0o005, 0o005)
        with mock.patch.object(statuspage, "render", side_effect=RuntimeError("boom")):
            statuspage.write(self.env)  # must not raise


class StatusServiceTest(unittest.TestCase):
    def cfg(self):
        import json
        from roadie.answers import Answers
        example = json.loads((Path(__file__).parent.parent / "examples/answers.example.json").read_text())
        return build_render_config(Answers.from_dict(example), load_versions(), 4000, 50)

    def test_served_read_only_behind_the_login(self):
        cfg = self.cfg()
        svc = compose_dict(cfg)["services"]["status"]
        self.assertTrue(svc["image"].startswith("busybox:"))
        self.assertNotIn("ports", svc)
        self.assertEqual((svc["read_only"], svc["user"], svc["volumes"]), (True, "65534:65534", ["./data/status:/www:ro"]))
        router = dynamic_dict(cfg)["http"]["routers"]["status"]
        self.assertEqual(router["middlewares"], ["authelia"])
        self.assertIn("status.example.duckdns.org", router["rule"])
        from tests.test_render_authelia import HASH, SECRETS
        config = next(f.content for f in render_authelia(cfg, SECRETS, HASH) if f.path.endswith("configuration.yml"))
        self.assertIn("status.example.duckdns.org", config)


if __name__ == "__main__":
    unittest.main()
