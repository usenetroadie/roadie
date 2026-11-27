import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from roadie import i18n
from roadie.access import sections, show, write_url_files
from roadie.ops.common import Env
from roadie.paths import Paths
from roadie.secretstore import SecretStore
from roadie.state import State
from roadie.ui import Prompter


class AccessBase(unittest.TestCase):
    def setUp(self):
        i18n.set_language("en")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.paths = Paths(Path(self.tmp.name))

    def env(self, people=None, backup=None):
        answers = {"duckdns": {"subdomain": "example"}, "admin_user": "tim",
                   "auth": {"username": "tim"}}
        if people:
            answers["people"] = people
        self.paths.etc_dir.mkdir(parents=True)
        self.paths.state_file.write_text(json.dumps({"answers": answers}))
        secrets = SecretStore(self.paths.secrets_file)
        secrets.set("answer.auth.password", "LOGINPW")
        secrets.set("ntfy_topic", "TOPIC123")
        secrets.set("aiostreams_admin_password", "ADMINPW")
        secrets.set("aiostreams_config_uuid", "UUID-MAIN")
        secrets.set("aiostreams_config_password", "PW-MAIN")
        secrets.set("aiostreams_config_encrypted", "ENC-MAIN")
        secrets.set("person.anna.uuid", "UUID-ANNA")
        secrets.set("person.anna.password", "PW-ANNA")
        secrets.set("person.anna.encrypted", "ENC-ANNA")
        env = Env(self.paths, State(self.paths.state_file), SecretStore(self.paths.secrets_file))
        if backup:
            env.save_json("backup.json", backup)
        return env

    def titles(self, secs):
        return [title for title, _ in secs]


class AccessTest(AccessBase):
    def test_sections_order_and_titles(self):
        secs = sections(self.env(backup={"time": "2026-11-14T03:00:00", "file": "/var/backups/roadie/x.tar.age"}))
        self.assertEqual(self.titles(secs), [i18n.t(f"access.t.{k}") for k in
                                              ("server", "players", "settings", "config", "dashboard", "prowlarr", "push", "backup")])
        flat = "\n".join(l for _, ls in secs for l in ls)
        self.assertIn("\0ssh tim@example.duckdns.org", flat)
        self.assertIn("https://prowlarr.example.duckdns.org", flat)
        self.assertIn("LOGINPW", flat)
        self.assertIn("TOPIC123", flat)
        self.assertIn("/var/backups/roadie/x.tar.age", flat)
        self.assertIn("2026-11-14 03:00:00", flat)

    def test_dashboard_login_in_full_view_only(self):
        env = self.env()
        flat = "\n".join(l for _, ls in sections(env) for l in ls)
        self.assertIn(i18n.t("access.dashboard_text"), flat)
        self.assertIn("\0https://aiostreams.example.duckdns.org/dashboard", flat)
        self.assertIn("\0admin", flat)
        self.assertIn("\0ADMINPW", flat)
        small = "\n".join(l for _, ls in sections(env, essentials_only=True) for l in ls)
        self.assertNotIn("ADMINPW", small)
        self.assertIn("ADMINPW", env.secrets.values())  # so the Redactor hides it in logs

    def test_backup_folder_is_admin_home(self):
        from unittest import mock
        from roadie.ops import backup
        with mock.patch.object(backup.system, "run", return_value="tim:x:1001:1001::/home/tim:/bin/bash"):
            secs = dict(sections(self.env()))
        self.assertIn(i18n.t("access.backup_text", dir="/home/tim/backups"), secs[i18n.t("access.t.backup")])

    def test_no_backup_yet(self):
        secs = dict(sections(self.env()))
        self.assertIn(i18n.t("access.no_backup"), secs[i18n.t("access.t.backup")])

    def test_every_person_listed_with_url(self):
        env = self.env(people=[{"id": "main", "name": "Tim", "mode": "main"},
                               {"id": "anna", "name": "Anna", "mode": "inherit"}])
        secs = dict(sections(env))
        players = "\n".join(secs[i18n.t("access.t.players")])
        self.assertIn("\0https://aiostreams.example.duckdns.org/stremio/UUID-MAIN/ENC-MAIN/manifest.json", players)
        self.assertIn("\0https://aiostreams.example.duckdns.org/stremio/UUID-ANNA/ENC-ANNA/manifest.json", players)
        self.assertIn("Anna", players)
        config = "\n".join(secs[i18n.t("access.t.config")])
        self.assertEqual(config.count(i18n.t("access.config_uuid")), 2)
        self.assertEqual(config.count(i18n.t("access.config_password")), 2)
        for value in ("UUID-MAIN", "PW-MAIN", "UUID-ANNA", "PW-ANNA"):
            self.assertIn("\0" + value, config)

    def test_urls_marked_copyable(self):
        for _, lines in sections(self.env()):
            for line in lines:
                if "https://" in line or "LOGINPW" in line or "TOPIC123" in line or "ssh " in line:
                    self.assertTrue(line.startswith("\0"), line)
                    self.assertNotIn("\0", line[1:])

    def test_empty_values_are_skipped(self):
        env = self.env()
        env.secrets.delete("ntfy_topic")
        env.secrets.delete("answer.auth.password")
        secs = dict(sections(env))
        self.assertNotIn(i18n.t("access.t.push"), secs)
        self.assertNotIn(i18n.t("access.login_password"), secs[i18n.t("access.t.settings")])

    def test_no_admin_user_omits_hint(self):
        env = self.env()
        del env.answers["admin_user"]
        server = dict(sections(env))[i18n.t("access.t.server")]
        self.assertNotIn(i18n.t("access.server_hint"), server)

    def test_show_renders_boxes_without_markers(self):
        out = io.StringIO()
        self.assertEqual(show(self.env(), Prompter(inp=io.StringIO(""), out=out)), 0)
        text = out.getvalue()
        self.assertNotIn("\0", text)
        self.assertIn("https://aiostreams.example.duckdns.org", text)
        self.assertIn("password manager", text)
        self.assertIn("Adjust it yourself", " ".join(text.split()))

    def test_players_text_says_keep_private(self):
        self.assertIn("private", i18n.t("access.players_text"))

    def test_person_without_config_has_no_dead_url(self):
        env = self.env(people=[{"id": "main", "name": "Tim", "mode": "main"},
                               {"id": "ben", "name": "Ben", "mode": "inherit"}])
        players = dict(sections(env))[i18n.t("access.t.players")]
        self.assertIn(i18n.t("people.no_url", name="Ben"), players)
        self.assertFalse(any("None" in line or "/stremio//" in line for line in players), players)

    def test_url_files_skip_person_without_config(self):
        env = self.env(people=[{"id": "main", "name": "Tim", "mode": "main"},
                               {"id": "anna", "name": "Anna", "mode": "inherit"},
                               {"id": "ben", "name": "Ben", "mode": "inherit"}])
        write_url_files(env)
        lines = (self.paths.etc_dir / "addon-urls.txt").read_text().splitlines()
        self.assertEqual([line.split("\t")[0] for line in lines], ["Tim", "Anna"])
        self.assertNotIn("None", "".join(lines))
        self.assertEqual((self.paths.etc_dir / "addon-url.txt").read_text(),
                         "https://aiostreams.example.duckdns.org/stremio/UUID-MAIN/ENC-MAIN/manifest.json\n")

    def test_server_box_uses_created_key_file(self):
        env = self.env()
        env.state.answers["_ssh_key_created"] = True
        flat = [l for _, ls in sections(env) for l in ls]
        self.assertIn("\0ssh -i ~/.ssh/roadie tim@example.duckdns.org", flat)

    def test_no_domain_no_ssh_line(self):
        env = self.env()
        del env.answers["duckdns"]
        secs = dict(sections(env))
        self.assertFalse(any(line.startswith("\0ssh") for line in secs[i18n.t("access.t.server")]))
        self.assertNotIn("None", "\n".join(l for ls in secs.values() for l in ls))


class SummaryTest(AccessBase):
    def run_summary(self, **extra):
        from roadie.engine import Context
        from roadie.phases import stack
        from roadie.redact import Redactor
        env = self.env()
        out = io.StringIO()
        ctx = Context(paths=self.paths, state=env.state, secrets=env.secrets,
                      ui=Prompter(inp=io.StringIO(""), out=out), redact=Redactor([]))
        ctx.extra.update(extra)
        stack.summary(ctx)
        self.assertTrue((self.paths.etc_dir / "addon-urls.txt").exists())
        return out.getvalue()

    def test_interactive_setup_shows_access(self):
        out = self.run_summary(interactive=True)
        self.assertIn("https://aiostreams.", out)
        self.assertIn(i18n.t("summary.heading"), out)

    def test_feedback_hint_once(self):
        from roadie.engine import Context
        from roadie.phases import stack
        from roadie.redact import Redactor
        env = self.env()
        outs = []
        for _ in range(2):
            out = io.StringIO()
            ctx = Context(paths=self.paths, state=env.state, secrets=env.secrets,
                          ui=Prompter(inp=io.StringIO(""), out=out), redact=Redactor([]))
            ctx.extra["interactive"] = True
            stack.summary(ctx)
            outs.append(out.getvalue())
        self.assertIn("issues/new/choose", outs[0])
        self.assertNotIn("issues/new/choose", outs[1])  # only after the first complete setup

    def test_summary_essentials_first(self):
        out = self.run_summary(interactive=True)
        flat = " ".join(out.replace("| ", "").split())
        self.assertIn(i18n.t("access.t.players"), out)
        self.assertIn(i18n.t("access.t.settings"), out)
        self.assertNotIn(i18n.t("access.t.config"), out)  # UUIDs and config passwords: later, in "My access"
        self.assertNotIn("PW-MAIN", out)
        self.assertIn(" ".join(i18n.t("summary.details_later").split()), flat)

    def test_summary_after_restore_says_nothing_changes(self):
        from roadie.engine import Context
        from roadie.phases import stack
        from roadie.redact import Redactor
        env = self.env()
        env.state.answers["_restored"] = {"file": "roadie-backup-2026-11-20.tar.age", "at": "x"}
        out = io.StringIO()
        ctx = Context(paths=self.paths, state=env.state, secrets=env.secrets,
                      ui=Prompter(inp=io.StringIO(""), out=out), redact=Redactor([]))
        ctx.extra["interactive"] = True
        stack.summary(ctx)
        self.assertIn(" ".join(i18n.t("summary.restored").split()),
                      " ".join(out.getvalue().replace("| ", "").split()))

    def test_small_server_line(self):
        from types import SimpleNamespace
        out = self.run_summary(interactive=True, facts=SimpleNamespace(mem_mb=2048))
        self.assertIn(" ".join(i18n.t("summary.small_server").split()), " ".join(out.replace("| ", "").split()))

    def test_no_small_server_line_with_4_gb(self):
        from types import SimpleNamespace
        out = self.run_summary(interactive=True, facts=SimpleNamespace(mem_mb=4096))
        self.assertNotIn(" ".join(i18n.t("summary.small_server").split()), " ".join(out.replace("| ", "").split()))

    def test_first_login_hint(self):
        hint = i18n.t("access.login_first_time")
        for word in ("Authelia", "passkey", "code app", "sudo roadie code", "email"):
            self.assertIn(word, hint)
        out = self.run_summary(interactive=True)
        self.assertIn(" ".join(hint.split())[:60], " ".join(out.replace("| ", "").split()))

    def test_summary_masks_login_password(self):
        out = self.run_summary(interactive=True)
        self.assertNotIn("LOGINPW", out)
        self.assertIn(i18n.t("access.password_masked"), out)

    def test_menu_access_shows_login_password(self):
        out = io.StringIO()
        show(self.env(), Prompter(inp=io.StringIO(""), out=out))
        self.assertIn("LOGINPW", out.getvalue())
        self.assertNotIn(i18n.t("access.password_masked"), out.getvalue())

    def test_summary_next_step_points_above(self):
        from roadie import help as helppages
        out = " ".join(self.run_summary(interactive=True).replace("| ", "").split())
        self.assertIn(i18n.t("help.next_steps.first_summary"), out)
        self.assertNotIn(i18n.t("help.next_steps.first_summary"), helppages.body(None, "next_steps"))

    def test_summary_heading_softer_with_open_points(self):
        out = self.run_summary(interactive=True, open_points=[("SSH", "open.ssh_hardening")])
        self.assertIn(i18n.t("summary.heading_open"), out)
        self.assertNotIn(i18n.t("summary.heading"), out)

    def test_summary_lists_next_steps_last(self):
        out = self.run_summary(interactive=True)
        self.assertGreater(out.index(i18n.t("help.next_steps.title")), out.index(i18n.t("access.t.settings")))

    def test_non_interactive_and_update_show_no_passwords(self):
        for extra in ({"interactive": False}, {"interactive": True, "update": True}, {}):
            out = self.run_summary(**extra)
            for secret in ("LOGINPW", "PW-MAIN", "TOPIC123", "ENC-MAIN"):
                self.assertNotIn(secret, out, extra)
            self.assertIn(i18n.t("summary.access_later"), out)
            shutil.rmtree(self.paths.etc_dir)  # self.env() builds a fresh one



class MetadataAccessTest(AccessBase):
    def test_shown_only_while_it_runs_and_with_the_addon_password(self):
        env = self.env()
        env.secrets.set("aiometadata_uuid", "META-UUID")
        env.secrets.set("aiometadata_config_password", "META-PW")
        env.secrets.set("aiometadata_addon_password", "ADDON-PW")
        flat = "\n".join(l for _, ls in sections(env) for l in ls)
        self.assertNotIn("metadata.example.duckdns.org", flat)  # taken down: the id alone is no proof
        env.paths.stack_dir.mkdir(parents=True)
        (env.paths.stack_dir / "compose.yaml").write_text("services:\n  aiometadata:\n    image: x\n")
        flat = "\n".join(l for _, ls in sections(env) for l in ls)
        for text in ("https://metadata.example.duckdns.org/configure", "META-UUID", "META-PW", "ADDON-PW"):
            self.assertIn(text, flat)


if __name__ == "__main__":
    unittest.main()
