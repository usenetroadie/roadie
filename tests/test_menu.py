import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from roadie import explain, help as helppages, i18n, menu
from roadie.aiostreams.api import AioError
from roadie.ops.common import Env
from roadie.paths import Paths
from roadie.secretstore import SecretStore
from roadie.state import State
from roadie.ui import Prompter

EXAMPLE = Path(__file__).parent.parent / "examples/answers.example.json"


class FakeApi:
    """Stands in for AioClient: no HTTP, records calls."""

    def __init__(self, fail_delete=False, fail_create=False, password_changed=False):
        self.users, self.deleted, self.fail_delete, self.fail_create = {}, [], fail_delete, fail_create
        self.password_changed = password_changed  # DELETE refused, but the config still works
        self.updated = []

    def starter_template(self):
        return {"presets": [], "sortCriteria": {"global": []}}

    def create_user(self, config, password):
        if self.fail_create:
            raise AioError("ERR", "boom", 500)
        uuid = f"u-{len(self.users) + 1}"
        self.users[uuid] = config
        return uuid, f"ENC-{uuid}"

    def get_user(self, uuid, password):
        return self.users[uuid]

    def update_user(self, uuid, password, config):
        self.users[uuid] = config
        self.updated.append(uuid)

    def manifest_ok(self, uuid, encrypted):
        return self.password_changed

    def delete_user(self, uuid, password):
        if self.password_changed:
            raise AioError("USER_INVALID_DETAILS", "Invalid UUID or password", 400)
        if self.fail_delete:
            raise AioError("ERR", "boom", 500)
        self.deleted.append(uuid)


class MenuBase(unittest.TestCase):
    def setUp(self):
        i18n.set_language("en")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.paths = Paths(Path(self.tmp.name))
        answers = json.loads(EXAMPLE.read_text())
        for key in ("auth",):
            answers[key] = {k: v for k, v in answers[key].items() if k != "password"}
        self.paths.etc_dir.mkdir(parents=True)
        self.paths.state_file.write_text(json.dumps({"answers": answers, "done": ["summary"]}))
        secrets = SecretStore(self.paths.secrets_file)
        for name, value in {"aiostreams_config_uuid": "UUID-MAIN", "aiostreams_config_password": "PW-MAIN",
                            "aiostreams_config_encrypted": "ENC-MAIN", "aiostreams_playback_password": "PB",
                            "answer.auth.password": "LOGINPW"}.items():
            secrets.set(name, value)
        self.env = Env(self.paths, State(self.paths.state_file), SecretStore(self.paths.secrets_file))

    def ui(self, text):
        self.out = io.StringIO()
        return Prompter(inp=io.StringIO(text), out=self.out, secret_reader=lambda p: "")


class MenuTest(MenuBase):
    def test_menu_shows_an_update_first(self):
        self.env.save_json("watch.json", {"selfupdate": {"notified": "v99.0.0", "news": ["Faster setup"]}})
        menu.run(self.env, self.ui("0\n"), actions={"change": lambda e, u: 0, "update": lambda e, u: 0})
        text = self.out.getvalue()
        self.assertLess(text.index("99.0.0"), text.index(i18n.t("menu.title")))
        self.assertIn("Faster setup", text)

    def test_menu_lists_items_and_exits(self):
        self.assertEqual(menu.run(self.env, self.ui("0\n"), actions={"change": lambda e, u: 0,
                                                                     "update": lambda e, u: 0}), 0)
        text = self.out.getvalue()
        for key in ("menu.access", "menu.code", "menu.status", "menu.restart", "menu.people", "menu.change",
                    "menu.backup", "menu.update", "menu.explain"):
            self.assertIn(i18n.t(key), text)
        self.assertIn("[0] " + i18n.t("menu.exit"), text)
        self.assertLess(text.index(i18n.t("menu.access")), text.index(i18n.t("menu.explain")))

    def test_menu_runs_access_then_back(self):
        self.assertEqual(menu.run(self.env, self.ui("1\n\n0\n")), 0)
        text = self.out.getvalue()
        self.assertIn("https://aiostreams.", text)
        self.assertEqual(text.count(i18n.t("menu.title")), 2)

    def test_injected_action_is_used(self):
        calls = []
        items = [i.key for i in menu.ITEMS if i.key != "ssh"]
        number = items.index("change") + 1
        menu.run(self.env, self.ui(f"{number}\n\n0\n"), actions={"change": lambda e, u: calls.append(1) or 0})
        self.assertEqual(calls, [1])

    def test_new_item_needs_one_entry(self):
        extra = menu.MenuItem("extra", "menu.extra", lambda env, ui: ui.info("EXTRA RAN") or 0)
        with mock.patch.object(menu, "ITEMS", [*menu.ITEMS, extra]):
            n = len(menu.visible_items(self.env, {"change": 1, "update": 1}))
            menu.run(self.env, self.ui(f"{n}\n\n0\n"), actions={"change": lambda e, u: 0, "update": lambda e, u: 0})
        self.assertIn("EXTRA RAN", self.out.getvalue())

    def test_menu_reloads_env_after_change(self):
        """'Change settings' writes state and secrets through its own objects; a later
        people action in the same session must not write the old data back."""
        def change(env, ui):
            state, secrets = State(self.paths.state_file), SecretStore(self.paths.secrets_file)
            state.answers["admin_user"] = "changed"
            state.save()
            secrets.set("new.secret", "NEW")
            return 0
        api = FakeApi()
        keys = [i.key for i in menu.ITEMS if i.key != "ssh"]
        actions = {"change": change, "update": lambda e, u: 0,
                   "people": lambda env, ui: menu.people_menu(env, ui, lambda e: api)}
        text = f"{keys.index('change') + 1}\n\n{keys.index('people') + 1}\n1\nAnna\n1\n\n0\n"
        loader = lambda: Env(self.paths, State(self.paths.state_file), SecretStore(self.paths.secrets_file))  # noqa: E731
        menu.run(self.env, self.ui(text), actions, reload=loader)
        state, secrets = State(self.paths.state_file), SecretStore(self.paths.secrets_file)
        self.assertEqual(state.answers["admin_user"], "changed")
        self.assertEqual(secrets.get("new.secret"), "NEW")
        self.assertEqual(state.answers["people"][1]["name"], "Anna")

    def test_invalid_choice_asks_again(self):
        self.assertEqual(menu.run(self.env, self.ui("99\n0\n")), 0)

    def test_failing_action_returns_to_menu_without_traceback(self):
        def boom(env, ui):
            raise RuntimeError("secret PW-MAIN leaked")
        number = [i.key for i in menu.ITEMS if i.key != "ssh"].index("change") + 1
        menu.run(self.env, self.ui(f"{number}\n\n0\n"), actions={"change": boom})
        text = self.out.getvalue()
        self.assertNotIn("PW-MAIN", text)
        self.assertNotIn("Traceback", text)


class FakeEnv:
    """Prowlarr was stopped: `up -d` starts it; AIOStreams gets healthy after a few polls."""

    def __init__(self, healthy_after):
        self.calls, self.polls, self.healthy_after = [], 0, healthy_after
        self.prowlarr = "exited"

    def compose(self, *args, check=True):
        self.calls.append(args)
        if args[:2] == ("up", "-d"):
            self.prowlarr = "running"
        return ""

    def services(self):
        self.polls += 1
        health = "healthy" if self.polls >= self.healthy_after else "starting"
        return [{"Service": "aiostreams", "State": "running", "Health": health},
                {"Service": "prowlarr", "State": self.prowlarr, "Health": ""}]


class RestartTest(MenuBase):
    def setUp(self):
        super().setUp()
        self.now = [0.0]
        patches = [mock.patch.object(menu, "_sleep", lambda s: self.now.__setitem__(0, self.now[0] + s)),
                   mock.patch.object(menu, "_clock", lambda: self.now[0])]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_restart_waits_until_healthy(self):
        env = FakeEnv(healthy_after=3)
        self.assertEqual(menu.restart(env, self.ui("")), 0)
        self.assertEqual(env.calls, [("up", "-d"), ("restart", "aiostreams")])
        self.assertIn(i18n.t("restart.ok"), self.out.getvalue())

    def test_restart_gives_up_after_three_minutes(self):
        env = FakeEnv(healthy_after=10_000)
        self.assertEqual(menu.restart(env, self.ui("")), 1)
        self.assertIn(i18n.t("restart.failed"), " ".join(self.out.getvalue().split()))
        self.assertLessEqual(self.now[0], menu.RESTART_WAIT + 5)

    def test_restart_waits_for_every_service(self):
        env = FakeEnv(healthy_after=1)
        env.compose = lambda *args, check=True: env.calls.append(args) or ""  # prowlarr stays stopped
        self.assertEqual(menu.restart(env, self.ui("")), 1)
        self.assertIn(i18n.t("restart.failed"), " ".join(self.out.getvalue().split()))

    def test_restart_comes_after_status(self):
        keys = [i.key for i in menu.ITEMS if i.key != "ssh"]
        self.assertEqual(keys.index("restart"), keys.index("status") + 1)


class PeopleMenuTest(MenuBase):
    def test_people_add_inherit(self):
        api = FakeApi()
        # add -> name -> mode inherit
        code = menu.people_menu(self.env, self.ui("1\nAnna\n1\n"), lambda env: api)
        self.assertEqual(code, 0, self.out.getvalue())
        people = State(self.paths.state_file).answers["people"]
        self.assertEqual([p["mode"] for p in people], ["main", "inherit"])
        self.assertEqual(people[1]["name"], "Anna")
        secrets = SecretStore(self.paths.secrets_file)
        uuid = secrets.get(f"person.{people[1]['id']}.uuid")
        self.assertEqual(api.users[uuid]["parentConfig"]["uuid"], "UUID-MAIN")
        self.assertIn(f"/stremio/{uuid}/ENC-{uuid}/manifest.json", self.out.getvalue())

    def test_people_add_leaves_the_others_alone(self):
        api = FakeApi()
        menu.people_menu(self.env, self.ui("1\nAnna\n1\n"), lambda env: api)
        anna = SecretStore(self.paths.secrets_file).get("person.anna.uuid")
        api.users[anna]["presets"] = []  # Anna deleted the wizard's addons on the website
        menu.people_menu(self.env, self.ui("1\nBen\n1\n"), lambda env: api)
        self.assertEqual(api.updated, [])
        self.assertEqual(api.users[anna]["presets"], [])
        self.assertTrue(SecretStore(self.paths.secrets_file).get("person.ben.uuid"))

    def test_people_change_writes_only_person_fields(self):
        api = FakeApi()
        menu.people_menu(self.env, self.ui("1\nAnna\n1\n"), lambda env: api)
        anna = SecretStore(self.paths.secrets_file).get("person.anna.uuid")
        api.users[anna]["presets"] = []
        api.users[anna]["formatter"] = {"id": "annas"}
        api.users[anna]["preferredLanguages"] = ["French"]  # a person field: roadie's answer wins
        menu.people_menu(self.env, self.ui("2\n1\n1\n"), lambda env: api)  # change -> Anna -> inherit
        self.assertEqual(api.updated, [anna])
        self.assertEqual((api.users[anna]["presets"], api.users[anna]["formatter"]), ([], {"id": "annas"}))
        self.assertNotEqual(api.users[anna].get("preferredLanguages"), ["French"])

    def test_people_keep_the_own_aiometadata(self):
        """The people menu builds the same config as the setup: with the own AIOMetadata
        while it is part of the stack, and without it once it was taken down."""
        SecretStore(self.paths.secrets_file).set("aiometadata_uuid", "META-UUID")
        self.env = Env(self.paths, State(self.paths.state_file), SecretStore(self.paths.secrets_file))
        seen, api = [], FakeApi()

        def build(answers, template, playback, metadata_preset=None):
            seen.append(metadata_preset)
            return {"presets": []}
        with mock.patch.object(menu, "build_user_config", side_effect=build):
            menu.people_menu(self.env, self.ui("1\nAnna\n1\n"), lambda env: api)
            self.paths.stack_dir.mkdir(parents=True)
            (self.paths.stack_dir / "compose.yaml").write_text("services:\n  aiometadata:\n    image: x\n")
            menu.people_menu(self.env, self.ui("1\nBen\n1\n\n"), lambda env: api)  # Enter: no own catalogs
        self.assertIsNone(seen[0])
        self.assertIn("/stremio/META-UUID/manifest.json", seen[1]["options"]["manifestUrl"])

    def test_person_with_own_catalogs_gets_own_aiometadata(self):
        SecretStore(self.paths.secrets_file).set("aiometadata_uuid", "META-UUID")
        self.paths.stack_dir.mkdir(parents=True)
        (self.paths.stack_dir / "compose.yaml").write_text("services:\n  aiometadata:\n    image: x\n")
        self.env = Env(self.paths, State(self.paths.state_file), SecretStore(self.paths.secrets_file))
        api = FakeApi()
        from roadie.aiostreams import metadata as M
        with mock.patch.object(menu.system, "container_ip", return_value="10.0.0.5"), \
                mock.patch.object(self.env, "compose", return_value="abc\n"), \
                mock.patch.object(M, "create", return_value="LISA-META") as create:
            menu.people_menu(self.env, self.ui("1\nLisa\n1\ny\n"), lambda env: api)  # add, inherit, own catalogs
        create.assert_called_once()
        self.assertEqual(create.call_args.args[0], "http://10.0.0.5:3232")
        lisa = SecretStore(self.paths.secrets_file).get("person.lisa.uuid")
        meta = next(p for p in api.users[lisa]["presets"] if p["instanceId"] == M.INSTANCE_ID)
        self.assertIn("/LISA-META/", meta["options"]["manifestUrl"])
        self.assertTrue(next(p for p in self.env.answers["people"] if p["id"] == "lisa")["own_catalogs"])

    def test_people_add_own_asks_content(self):
        api = FakeApi()
        with mock.patch.object(menu.questions, "ask_content",
                               lambda ui, data: data.update(content={"languages": ["German"], "original_audio": True,
                                                                     "prefer_4k": False, "hdr": False,
                                                                     "max_size_gb": None})):
            menu.people_menu(self.env, self.ui("1\nBen\n2\n"), lambda env: api)
        person = State(self.paths.state_file).answers["people"][1]
        self.assertEqual(person["mode"], "own")
        self.assertEqual(person["content"]["languages"], ["German"])

    def test_people_add_sync_failure_reported(self):
        menu.people_menu(self.env, self.ui("1\nAnna\n1\n"), lambda env: FakeApi(fail_create=True))
        self.assertIn(i18n.t("people.sync_failed"), " ".join(self.out.getvalue().split()))

    def test_people_remove_failure_reported(self):
        self.env.state.answers["people"] = [{"id": "main", "name": "Me", "mode": "main"},
                                            {"id": "anna", "name": "Anna", "mode": "inherit"}]
        self.env.state.save()
        self.env.secrets.set("person.anna.uuid", "U-A")
        self.env.secrets.set("person.anna.password", "P-A")
        api = FakeApi(fail_delete=True)
        menu.people_menu(self.env, self.ui("3\n1\ny\n"), lambda env: api)
        text = " ".join(self.out.getvalue().split())
        self.assertIn(i18n.t("people.remove_failed", name="Anna"), text)
        self.assertEqual(len(State(self.paths.state_file).answers["people"]), 2)

    def test_people_remove_password_changed_explained(self):
        self.env.state.answers["people"] = [{"id": "main", "name": "Me", "mode": "main"},
                                            {"id": "anna", "name": "Anna", "mode": "inherit"}]
        self.env.state.save()
        for name, value in (("uuid", "U-A"), ("password", "P-A"), ("encrypted", "E-A")):
            self.env.secrets.set(f"person.anna.{name}", value)
        code = menu.people_menu(self.env, self.ui("3\n1\ny\n"), lambda env: FakeApi(password_changed=True))
        self.assertEqual(code, 1)
        self.assertIn(i18n.t("people.remove_failed_auth", name="Anna"), " ".join(self.out.getvalue().split()))
        self.assertEqual(len(State(self.paths.state_file).answers["people"]), 2)
        self.assertEqual(SecretStore(self.paths.secrets_file).get("person.anna.uuid"), "U-A")

    def test_url_file_error_does_not_hide_result(self):
        with mock.patch.object(menu.access, "write_url_files", side_effect=OSError("disk full")):
            code = menu.people_menu(self.env, self.ui("1\nAnna\n1\n"), lambda env: FakeApi())
        self.assertEqual(code, 0)

    def test_people_remove_ok(self):
        self.env.state.answers["people"] = [{"id": "main", "name": "Me", "mode": "main"},
                                            {"id": "anna", "name": "Anna", "mode": "inherit"}]
        self.env.state.save()
        self.env.secrets.set("person.anna.uuid", "U-A")
        self.env.secrets.set("person.anna.password", "P-A")
        api = FakeApi()
        menu.people_menu(self.env, self.ui("3\n1\ny\n"), lambda env: api)
        self.assertEqual(api.deleted, ["U-A"])
        self.assertEqual(len(State(self.paths.state_file).answers["people"]), 1)

    def test_url_file_follows_people_changes(self):
        urls = self.paths.etc_dir / "addon-urls.txt"
        api = FakeApi()
        menu.people_menu(self.env, self.ui("1\nAnna\n1\n"), lambda env: api)
        self.assertEqual([line.split("\t")[0] for line in urls.read_text().splitlines()], ["Me", "Anna"])
        menu.people_menu(self.env, self.ui("3\n1\ny\n"), lambda env: api)
        self.assertEqual([line.split("\t")[0] for line in urls.read_text().splitlines()], ["Me"])

    def test_people_change_without_others(self):
        menu.people_menu(self.env, self.ui("2\n"), lambda env: FakeApi())
        self.assertIn(i18n.t("people.none"), self.out.getvalue())


class SshItemTest(MenuBase):
    def test_menu_shows_ssh_item_until_hardened(self):
        keys = [item.key for item, _ in menu.visible_items(self.env, {"ssh": lambda env, ui: 0})]
        self.assertIn("ssh", keys)
        self.env.state.answers["_ssh_hardened"] = True
        keys = [item.key for item, _ in menu.visible_items(self.env, {"ssh": lambda env, ui: 0})]
        self.assertNotIn("ssh", keys)


class HelpBackTest(MenuBase):
    def test_no_second_back_prompt_after_help(self):
        number = [i.key for i, _ in menu.visible_items(self.env)].index("explain") + 1
        back = len(explain.TOPICS) + 2  # the "Back" entry of the help list
        ui = self.ui(f"{number}\n{back}\n0\n")
        ui.tty = True
        ui.inp.isatty = lambda: True  # the help list only shows on a terminal
        menu.run(self.env, ui)
        self.assertIn(i18n.t("explain.pick"), self.out.getvalue())
        self.assertNotIn(i18n.t("menu.back"), self.out.getvalue())


class HelpTest(MenuBase):
    def test_help_has_do_it_yourself_topic(self):
        self.assertIn("do_it_yourself", helppages.TOPICS)
        self.assertEqual(i18n.t("help.do_it_yourself.title"), "Adjust it yourself")
        self.assertEqual(i18n.t("help.backup_download.title"), "Download a backup")

    def test_help_shows_topic(self):
        self.assertEqual(explain.run(self.env, self.ui(""), "do_it_yourself"), 0)
        text = self.out.getvalue()
        self.assertIn(i18n.t("menu.restart"), " ".join(text.split()))
        self.assertNotIn("$ ", text)

    def test_do_it_yourself_and_more_catalogs_fit_a_phone_screen(self):
        for topic in ("do_it_yourself", "more_catalogs"):
            for lang in ("en",):
                i18n.set_language(lang)
                ui = self.ui("")
                ui.width = 50
                explain.show(self.env, ui, topic)
                box = self.out.getvalue().strip().splitlines()
                self.assertLessEqual(len(box), 22, (topic, lang))

    def test_more_catalogs_names_no_indexer(self):
        for lang in ("en",):
            i18n.set_language(lang)
            body = i18n.t("help.more_catalogs.body")
            self.assertIn("AIOMetadata", body)
            self.assertNotIn("indexer", body.lower())

    def test_help_follows_backup_key(self):
        self.assertIn(i18n.t("help.next_steps.backup"), helppages.body(self.env, "next_steps"))
        self.assertNotIn(i18n.t("help.backup_download.no_key"), helppages.body(self.env, "backup_download", dir="d",
                                                                                 user="u", host="h"))
        self.env.answers["backup"] = {"age_recipient": None}
        self.assertNotIn(i18n.t("help.next_steps.backup"), helppages.body(self.env, "next_steps"))
        self.assertTrue(helppages.body(self.env, "backup_download", dir="d", user="u", host="h")
                        .startswith(i18n.t("help.backup_download.no_key")))

    def test_every_topic_renders(self):
        for topic in helppages.TOPICS:
            explain.run(self.env, self.ui(""), topic)
            self.assertNotIn("{", self.out.getvalue())


if __name__ == "__main__":
    unittest.main()
