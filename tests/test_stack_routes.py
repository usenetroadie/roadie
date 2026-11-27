"""Traefik routes live in a folder; an old routes.yml is retired; a broken compose.override.yaml is named."""

import io
import tempfile
import types
import unittest
from unittest import mock
from pathlib import Path

from roadie import system
from roadie.engine import Context, StepFailed
from roadie.paths import Paths
from roadie.phases import stack
from roadie.redact import Redactor
from roadie.render import RenderedFile, traefik
from roadie.render.yaml import dump
from roadie.secretstore import SecretStore
from roadie.state import State
from tests.test_render_traefik import cfg


class RoutesFolderTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        paths = Paths(root=Path(tmp.name))
        (Path(tmp.name) / "etc/roadie").mkdir(parents=True)
        self.ctx = Context(paths=paths, state=State(paths.state_file), secrets=SecretStore(paths.secrets_file),
                           ui=None, redact=Redactor())
        self.old = paths.stack_dir / "config/traefik/routes.yml"
        self.old.parent.mkdir(parents=True)
        self.files = traefik.render_traefik(cfg())
        self.new = next(f for f in self.files if f.path == traefik.DYNAMIC_FILE).content

    def test_dynamic_folder_provider(self):
        provider = traefik.static_dict(cfg())["providers"]["file"]
        self.assertEqual(provider, {"directory": "/etc/traefik/dynamic", "watch": True})
        self.assertEqual(traefik.DYNAMIC_FILE, "config/traefik/dynamic/roadie.yml")

    def test_old_routes_removed_when_unchanged(self):
        self.old.write_text("# old header\n\n" + "".join(l + "\n" for l in self.new.splitlines()
                                                          if not l.startswith("#")))
        self.ctx.state.answers["_written"] = {traefik.OLD_DYNAMIC_FILE: "x"}
        stack._retire_old_routes(self.ctx, self.files)
        self.assertFalse(self.old.exists())
        self.assertEqual(list(self.old.parent.glob("*.bak")), [])
        self.assertNotIn(traefik.OLD_DYNAMIC_FILE, self.ctx.state.answers["_written"])

    def test_old_routes_kept_as_copy_when_edited(self):
        self.old.write_text(self.new + "\n# my route\nhttp: {routers: {mine: {}}}\n")
        self.ctx.state.answers["_written"] = {traefik.OLD_DYNAMIC_FILE: "x"}
        stack._retire_old_routes(self.ctx, self.files)
        self.assertFalse(self.old.exists())
        copies = list(self.old.parent.glob("routes.yml.mine-*.bak"))
        self.assertEqual(len(copies), 1)
        self.assertIn("my route", copies[0].read_text())
        self.assertEqual(self.ctx.extra["mine_copies"], [(traefik.OLD_DYNAMIC_FILE, str(copies[0]))])
        self.assertNotIn(traefik.OLD_DYNAMIC_FILE, self.ctx.state.answers["_written"])

    def test_old_routes_stay_when_writing_fails(self):
        self.old.write_text("http: {routers: {mine: {}}}\n")
        self.ctx.extra["render"] = cfg()
        self.ctx.answers = types.SimpleNamespace(data={"auth": {"password": "p"}, "usenet": {"providers": []}})
        self.ctx.secrets.set("authelia_hash_for", stack._fingerprint("p"))
        with mock.patch.object(stack, "_secrets", return_value={}), \
                mock.patch.object(stack.system, "run"), mock.patch.object(stack.statuspage, "write"), \
                mock.patch.object(stack.authelia, "render_authelia", return_value=[]), \
                mock.patch.object(stack.compose, "render_compose", return_value=[]), \
                mock.patch.object(stack.envfiles, "render_env", return_value=[]), \
                mock.patch.object(stack.guard, "write_guarded", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                stack.stack_files(self.ctx)
        self.assertTrue(self.old.exists())

    def test_missing_new_file_keeps_old_routes(self):
        self.old.write_text("x: 1\n")
        stack._retire_old_routes(self.ctx, [])
        self.assertTrue(self.old.exists())

    def test_kept_copy_explains_where_own_routes_go(self):
        from roadie import cli, i18n
        out = io.StringIO()
        ui = mock.Mock()
        ui.warn = lambda m: out.write(m + "\n")
        ui.info = ui.hint = lambda m: out.write(m + "\n")
        self.ctx.extra["mine_copies"] = [(traefik.OLD_DYNAMIC_FILE, "/opt/stack/config/traefik/routes.yml.mine-1.bak"),
                                         ("config/other.yml", "/x/other.bak")]
        cli._show_mine_copies(ui, self.ctx)
        text = out.getvalue()
        self.assertIn("config/traefik/dynamic/", text)
        self.assertIn("routes.yml.mine-1.bak", text)
        self.assertEqual(text.count("not active any more"), 1)


    def test_unsure_copies_get_their_own_notice(self):
        from roadie import cli, i18n
        out = io.StringIO()
        ui = mock.Mock()
        ui.warn = ui.info = ui.hint = lambda m: out.write(m + "\n")
        self.ctx.extra["mine_copies"] = [("config/a.yml", "/x/a.bak"), ("config/b.yml", "/x/b.bak")]
        self.ctx.extra["unsure_copies"] = {"/x/b.bak", "/x/gone.bak"}
        cli._show_mine_copies(ui, self.ctx)
        text = out.getvalue()
        self.assertLess(text.index(i18n.t("guard.copies")), text.index("/x/a.bak"))
        self.assertLess(text.index("/x/a.bak"), text.index(i18n.t("guard.copies_unsure")))
        self.assertLess(text.index(i18n.t("guard.copies_unsure")), text.index("/x/b.bak"))
        self.assertNotIn("gone.bak", text)
        self.assertEqual(text.count("docs/advanced.md"), 1)
        out.truncate(0)
        out.seek(0)
        self.ctx.extra["mine_copies"] = [("config/b.yml", "/x/b.bak")]
        cli._show_mine_copies(ui, self.ctx)
        self.assertNotIn(i18n.t("guard.copies"), out.getvalue())

class OverrideErrorTest(unittest.TestCase):
    def run_up(self, failing):
        ctx = mock.Mock()
        err = system.CommandFailed(["docker", "compose"], 1,
                                   "validating /opt/roadie/stack/compose.override.yaml: services.x invalid")

        def fake(ctx, *args, check=True):
            if args[0] == "ps":
                return ""
            if args[0] == failing:
                raise err
            return ""
        with mock.patch.object(stack, "_compose", fake):
            return stack.stack_up(ctx)

    def test_override_error_named(self):
        for failing in ("pull", "up"):
            with self.subTest(failing=failing), self.assertRaises(StepFailed) as cm:
                self.run_up(failing)
            self.assertIn("compose.override.yaml", str(cm.exception))
