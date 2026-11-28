import base64
import copy
import json
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from roadie.aiostreams import people as P
from roadie.aiostreams.api import AioClient, AioError
from roadie.aiostreams.builder import PERSON_FIELDS, content_fields
from roadie.secretstore import SecretStore
from roadie.state import State
from tests.helpers import MockHttp

OWN_CONTENT = {"languages": ["English"], "original_audio": False, "prefer_4k": False, "hdr": False}
BASE = {"presets": [{"type": "x", "instanceId": "roadietmdb"}], "services": [{"id": "s"}], "preferredLanguages": ["German"],
        "preferredResolutions": ["2160p", "1080p", "720p"]}


def ok(data, status=200):
    return status, json.dumps({"success": True, "data": data, "error": None}), "application/json"


def err(status, code="ERR", message="boom"):
    return status, json.dumps({"success": False, "data": None, "error": {"code": code, "message": message}}), "application/json"


def people(*others):
    return [{"id": "main", "name": "Me", "mode": "main"}, *others]


class FakeAio:
    """Tiny AIOStreams: POST creates u-<n>, PUT/DELETE check Basic auth."""

    def __init__(self):
        self.users = {}  # uuid -> {"password", "config"}
        self.fail_post_at = None  # 1-based POST number that answers 500
        self.limit_posts = 0  # number of POSTs answered with 429 first
        self.limit_gets = self.limit_puts = 0  # same for GET / PUT
        self.gets = 0
        self.fail_delete = False
        self.posts = 0
        self.puts = []
        self.srv = MockHttp({"POST /api/v1/user": self.create, "PUT /api/v1/user": self.update,
                             "DELETE /api/v1/user": self.delete, "GET /api/v1/user": self.get})

    def _user(self, headers):
        uuid, _, pw = base64.b64decode(headers["Authorization"].split()[1]).decode().partition(":")
        u = self.users.get(uuid)
        return uuid if u and u["password"] == pw else None

    def create(self, body, headers):
        self.posts += 1
        if self.limit_posts:
            self.limit_posts -= 1
            return err(429, "RATE_LIMIT_EXCEEDED", "slow down")
        if self.posts == self.fail_post_at:
            return err(500)
        req = json.loads(body)
        uuid = f"u-{len(self.users) + 1}"
        self.users[uuid] = {"password": req["password"], "config": req["config"]}
        return ok({"uuid": uuid, "encryptedPassword": f"ENC-{uuid}"}, 201)

    def get(self, query, headers):
        self.gets += 1
        if self.limit_gets:
            self.limit_gets -= 1
            return err(429, "RATE_LIMIT_EXCEEDED", "slow down")
        uuid = self._user(headers)
        if not uuid:
            return err(401, "USER_INVALID_PASSWORD")
        return ok({"userData": self.users[uuid]["config"]})

    def update(self, body, headers):
        if self.limit_puts:
            self.limit_puts -= 1
            return err(429, "RATE_LIMIT_EXCEEDED", "slow down")
        uuid = self._user(headers)
        if not uuid:
            return err(401, "USER_INVALID_PASSWORD")
        self.users[uuid]["config"] = json.loads(body)["config"]
        self.puts.append(uuid)
        return ok({})

    def delete(self, body, headers):
        uuid = self._user(headers)
        if self.fail_delete or not uuid:
            return err(500)
        del self.users[uuid]
        return ok({})


class PureTest(unittest.TestCase):
    def test_default_people_is_main_only(self):
        self.assertEqual(P.people_of({}), [{"id": "main", "name": "Me", "mode": "main"}])
        given = people({"id": "lisa", "name": "Lisa", "mode": "inherit"})
        self.assertEqual(P.people_of({"people": given}), given)

    def test_slug_unique(self):
        self.assertEqual(P.slug("Lisa", {"lisa"}), "lisa-2")
        self.assertEqual(P.slug("Lisa", {"lisa", "lisa-2"}), "lisa-3")
        self.assertEqual(P.slug("Jörg Müller", set()), "joerg-mueller")
        self.assertEqual(P.slug("Renée", set()), "renee")
        self.assertEqual(P.slug("", set()), "person")
        self.assertEqual(P.slug("!!!", {"person"}), "person-2")
        self.assertLessEqual(len(P.slug("x" * 80, {"x" * 32})), 32)
        self.assertEqual(P.slug("Main", set()), "main-2")

    def test_secret_names(self):
        self.assertEqual(P.secret_names("main"), ("aiostreams_config_uuid", "aiostreams_config_password",
                                                  "aiostreams_config_encrypted"))
        self.assertEqual(P.secret_names("lisa"), ("person.lisa.uuid", "person.lisa.password", "person.lisa.encrypted"))

    def test_inherit_child_has_parent_and_no_overrides(self):
        child = P.child_config(BASE, "u-main", "main-pw", {"id": "lisa", "name": "Lisa", "mode": "inherit"})
        self.assertEqual(child["parentConfig"], {"uuid": "u-main", "password": "main-pw"})
        self.assertEqual(child["preferredLanguages"], ["German"])
        self.assertNotIn("parentConfig", BASE)

    def test_own_child_overrides_exactly_person_fields(self):
        person = {"id": "lisa", "name": "Lisa", "mode": "own", "content": OWN_CONTENT}
        child = P.child_config(BASE, "u-main", "main-pw", person)
        self.assertEqual(child["parentConfig"]["mergeStrategies"],
                         {"fieldOverrides": {f: "override" for f in PERSON_FIELDS}})
        for field, value in content_fields(OWN_CONTENT).items():
            self.assertEqual(child[field], value)
        self.assertEqual(child["services"], BASE["services"])
        self.assertEqual(BASE["preferredLanguages"], ["German"])


class PeopleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.secrets = SecretStore(Path(self.tmp.name) / "secrets.json")
        self.secrets.set("aiostreams_config_uuid", "u-main")
        self.secrets.set("aiostreams_config_password", "main-pw")
        self.aio = FakeAio()
        self.sleeps = []
        self.api = AioClient(self.aio.srv.url, sleep=self.sleeps.append)

    def tearDown(self):
        self.aio.srv.close()
        self.tmp.cleanup()

    def test_sync_creates_missing_and_updates_existing(self):
        data = {"people": people({"id": "lisa", "name": "Lisa", "mode": "inherit"})}
        P.sync(self.api, self.secrets, data, BASE)
        self.assertEqual(self.aio.posts, 1)
        self.assertEqual(self.secrets.get("person.lisa.uuid"), "u-1")
        self.assertEqual(self.secrets.get("person.lisa.encrypted"), "ENC-u-1")
        self.assertEqual(self.aio.users["u-1"]["password"], self.secrets.get("person.lisa.password"))
        self.assertEqual(self.aio.users["u-1"]["config"]["parentConfig"]["uuid"], "u-main")
        P.sync(self.api, self.secrets, data, BASE)
        self.assertEqual(self.aio.posts, 1)
        self.assertEqual(self.aio.puts, [])  # nothing changed: no PUT (rate limit)
        P.sync(self.api, self.secrets, data, {**BASE, "preferredLanguages": ["English"]})
        self.assertEqual(self.aio.puts, ["u-1"])

    def test_update_keeps_user_changes_outside_sections(self):
        data = {"people": people({"id": "lisa", "name": "Lisa", "mode": "inherit"})}
        P.sync(self.api, self.secrets, data, BASE)
        stored = self.aio.users["u-1"]["config"]
        stored["formatter"] = {"id": "custom"}  # changed by the user on the website
        stored["preferredLanguages"] = ["French"]  # a wizard section: replaced
        stored["parentConfig"] = {"uuid": "stale", "password": "x"}
        P.sync(self.api, self.secrets, data, {**BASE, "preferredLanguages": ["English"]})
        config = self.aio.users["u-1"]["config"]
        self.assertEqual(config["formatter"], {"id": "custom"})
        self.assertEqual(config["preferredLanguages"], ["English"])
        self.assertEqual(config["parentConfig"]["uuid"], "u-main")

    def test_setup_step_keeps_user_changes_in_main_config(self):
        import io
        from unittest import mock
        from roadie.aiostreams.api import STARTER_TEMPLATE
        from roadie.answers import Answers
        from roadie.engine import Context
        from roadie.paths import Paths
        from roadie.phases import stack
        from roadie.redact import Redactor
        from roadie.ui import Prompter
        example = json.loads((Path(__file__).parent.parent / "examples/answers.example.json").read_text())
        template = {"presets": [], "sortCriteria": {"global": []}, "formatter": {"id": "template"}}
        self.aio.srv.routes["GET /api/v1/templates"] = lambda q, h: ok(
            [{"metadata": {"id": STARTER_TEMPLATE}, "config": template}])
        self.aio.users["u-main"] = {"password": "main-pw", "config": {"formatter": {"id": "mine"},
                                                                     "preferredLanguages": ["French"]}}
        paths = Paths(Path(self.tmp.name))
        ctx = Context(paths=paths, state=State(paths.state_file), secrets=self.secrets,
                      ui=Prompter(inp=io.StringIO(""), out=io.StringIO()), redact=Redactor([]),
                      answers=Answers.from_dict(example))
        with mock.patch.object(stack, "_container_url", return_value=self.aio.srv.url):
            stack.aiostreams_config(ctx)
        config = self.aio.users["u-main"]["config"]
        self.assertEqual(config["formatter"], {"id": "mine"})
        self.assertEqual(config["preferredLanguages"], content_fields(example["content"])["preferredLanguages"])

    def _main_step(self, changed, main_config, extra_people=(), meta=False):
        """Run the AIOStreams setup step on an existing main config with ctx.extra["changed"]."""
        import io
        from unittest import mock
        from roadie.aiostreams.api import STARTER_TEMPLATE
        from roadie.answers import Answers
        from roadie.engine import Context
        from roadie.paths import Paths
        from roadie.phases import stack
        from roadie.redact import Redactor
        from roadie.ui import Prompter
        example = json.loads((Path(__file__).parent.parent / "examples/answers.example.json").read_text())
        example["people"] = people(*extra_people)
        template = {"presets": [], "sortCriteria": {"global": []}, "formatter": {"id": "template"}}
        self.aio.srv.routes["GET /api/v1/templates"] = lambda q, h: ok(
            [{"metadata": {"id": STARTER_TEMPLATE}, "config": template}])
        self.aio.users["u-main"] = {"password": "main-pw", "config": copy.deepcopy(main_config)}
        paths = Paths(Path(self.tmp.name))
        ctx = Context(paths=paths, state=State(paths.state_file), secrets=self.secrets,
                      ui=Prompter(inp=io.StringIO(""), out=io.StringIO()), redact=Redactor([]),
                      answers=Answers.from_dict(example))
        if changed is not None:
            ctx.extra["changed"] = changed
        from roadie.aiostreams import metadata
        with mock.patch.object(stack, "_container_url", return_value=self.aio.srv.url), \
                mock.patch.object(stack, "_metadata_preset", return_value=metadata.preset("META") if meta else None):
            stack.aiostreams_config(ctx)
        return ctx, self.aio.users["u-main"]["config"]

    def test_update_without_changes_puts_nothing(self):
        lisa = {"id": "lisa", "name": "Lisa", "mode": "own", "content": OWN_CONTENT}
        P.sync(self.api, self.secrets, {"people": people(lisa)}, BASE)  # Lisa exists: u-1
        self.aio.users["u-1"]["config"]["preferredLanguages"] = ["French"]  # her own edit on the website
        mine = {"formatter": {"id": "mine"}, "preferredLanguages": ["French"], "presets": []}
        ctx, config = self._main_step(set(), mine, [lisa])
        self.assertEqual(self.aio.puts, [])
        self.assertEqual(self.aio.posts, 1)
        self.assertEqual(config, mine)
        self.assertEqual(self.aio.users["u-1"]["config"]["preferredLanguages"], ["French"])
        self.assertIn("aio_api", ctx.extra)

    def test_only_languages_written(self):
        services = [{"id": "aiostreams", "enabled": True, "credentials": {"aiostreamsAuth": "mine:pw"}}]
        mine = {"formatter": {"id": "mine"}, "preferredLanguages": ["French"], "services": services,
                "presets": [{"type": "prowlarr", "instanceId": "roadieprowlarr", "enabled": False, "options": {}}]}
        _, config = self._main_step({"content"}, mine)
        self.assertEqual(self.aio.puts, ["u-main"])
        self.assertEqual(config["services"], services)
        self.assertEqual(config["formatter"], {"id": "mine"})
        self.assertNotEqual(config["preferredLanguages"], ["French"])
        self.assertEqual([p["instanceId"] for p in config["presets"]], ["roadieprowlarr"])  # no deleted addon back
        self.assertFalse(config["presets"][0]["enabled"])

    def test_own_metadata_address_survives_update_and_change(self):
        # a real user pointed roadie's AIOMetadata addon at a second AIOMetadata configuration
        from roadie.aiostreams import metadata
        own = metadata.preset("OWN")
        prowlarr = {"type": "prowlarr", "instanceId": "roadieprowlarr", "enabled": True,
                    "options": {"name": "Prowlarr", "timeout": 10000, "services": ["aiostreams"]}}
        main = {"formatter": {"id": "mine"}, "services": [{"id": "mine"}], "presets": [prowlarr, own]}
        ctx, after_update = self._main_step(set(), main, meta=True)  # update: nothing written, record started
        self.assertEqual(after_update, main)
        ctx.state.save()
        _, config = self._main_step({"content"}, after_update, meta=True)  # change settings: languages
        meta = next(p for p in config["presets"] if p["instanceId"] == "roadiemetadata")
        self.assertIn("/OWN/", meta["options"]["manifestUrl"])
        self.assertNotEqual(config["preferredLanguages"], None)

    def test_own_group_condition_and_service_survive_indexer_change(self):
        # a real user made the fallback indexers anime-only and added a service of his own
        from roadie.aiostreams.builder import fallback_groups
        prowlarr = {"type": "prowlarr", "instanceId": "roadieprowlarr", "enabled": True, "options": {}}
        fallback = {"type": "prowlarr", "instanceId": "roadieprowlarrfallback", "enabled": True, "options": {}}
        groups = fallback_groups({"languages": ["English"]})
        groups["groupings"][1]["condition"] = "(queryType == 'anime.series') and count(totalStreams) < 5"
        easynews = {"id": "easynews", "enabled": True, "credentials": {"username": "u", "password": "p"}}
        main = {"services": [{"id": "aiostreams", "enabled": True, "credentials": {}}, easynews],
                "presets": [prowlarr, fallback], "groups": groups}
        ctx, after_update = self._main_step(set(), main)
        ctx.state.save()
        _, config = self._main_step({"indexers"}, after_update)
        self.assertEqual(config.get("groups"), groups)
        self.assertIn(easynews, config["services"])

    def _tmdb_main(self):
        from roadie.aiostreams.builder import _ADDON_SUFFIX
        tmdb = {"type": "tmdb-addon", "instanceId": "roadietmdb", "enabled": False, "options": {"language": "x"}}
        mine = {"type": "custom", "instanceId": "f3a", "enabled": True, "options": {}}
        prowlarr = {"type": "prowlarr", "instanceId": "roadieprowlarr", "enabled": True, "options": {}}
        rows = [{"id": f"roadietmdb{_ADDON_SUFFIX}.tmdb.trending", "type": "movie", "enabled": False},
                {"id": "f3a1234.top", "type": "movie", "enabled": True}]
        return {"formatter": {"id": "mine"}, "preferredLanguages": ["French"], "services": [{"id": "mine"}],
                "presets": [mine, tmdb, prowlarr], "catalogModifications": rows}

    def test_metadata_swapped_in_on_content_change(self):
        _, config = self._main_step({"content"}, self._tmdb_main(), meta=True)
        self.assertEqual([p["instanceId"] for p in config["presets"]], ["f3a", "roadiemetadata", "roadieprowlarr"])
        self.assertFalse(config["presets"][1]["enabled"])
        self.assertEqual([r["id"] for r in config["catalogModifications"]][0], "f3a1234.top")  # tmdb rows go
        self.assertFalse(any(r["id"].startswith("roadietmdb") for r in config["catalogModifications"]))

    def test_metadata_newly_active_swaps_only_the_slot(self):
        main = self._tmdb_main()
        _, config = self._main_step(set(), main, meta=True)
        self.assertEqual(self.aio.puts, ["u-main"])
        self.assertEqual([p["instanceId"] for p in config["presets"]], ["f3a", "roadiemetadata", "roadieprowlarr"])
        self.assertFalse(config["presets"][1]["enabled"])
        self.assertEqual(config["presets"][2], main["presets"][2])  # prowlarr not rewritten
        for key in ("formatter", "preferredLanguages", "services"):
            self.assertEqual(config[key], main[key])
        self.assertNotIn("resultLimits", config)
        self.assertEqual(config["catalogModifications"], main["catalogModifications"][1:])

    def test_slot_swap_does_not_bring_back_other_rows(self):
        from roadie.aiostreams.builder import _ADDON_SUFFIX
        main = self._tmdb_main()
        main["presets"].append({"type": "custom", "instanceId": "roadiestreaming", "enabled": True, "options": {}})
        _, config = self._main_step(set(), main, meta=True)
        self.assertFalse(any(r["id"].startswith("roadiestreaming") for r in config["catalogModifications"]))
        self.assertEqual(config["catalogModifications"], main["catalogModifications"][1:])

    def test_metadata_newly_active_after_user_deleted_tmdb(self):
        main = self._tmdb_main()
        main["presets"] = [p for p in main["presets"] if p["instanceId"] != "roadietmdb"]
        _, config = self._main_step(set(), main, meta=True)
        self.assertEqual(self.aio.puts, [])
        self.assertEqual(config, main)

    def test_metadata_gone_swaps_back(self):
        from roadie.aiostreams import metadata
        main = self._tmdb_main()
        main["presets"][1] = dict(metadata.preset("META"), enabled=False)
        main["catalogModifications"] = main["catalogModifications"][1:]
        _, config = self._main_step(set(), main, meta=False)
        self.assertEqual([p["instanceId"] for p in config["presets"]], ["f3a", "roadietmdb", "roadieprowlarr"])
        self.assertFalse(config["presets"][1]["enabled"])
        self.assertTrue(any(r["id"].startswith("roadietmdb") for r in config["catalogModifications"]))

    def test_missing_changed_means_everything(self):
        """Other entry points (no ctx.extra["changed"]) keep the old behaviour."""
        _, config = self._main_step(None, {"formatter": {"id": "mine"}})
        self.assertIn("roadiestreaming", [p["instanceId"] for p in config["presets"]])

    def test_new_person_created_on_people_change(self):
        lisa = {"id": "lisa", "name": "Lisa", "mode": "inherit"}
        P.sync(self.api, self.secrets, {"people": people(lisa)}, BASE)
        self.aio.users["u-1"]["config"]["formatter"] = {"id": "lisas"}
        self.aio.users["u-1"]["config"]["services"] = [{"id": "hers"}]
        ben = {"id": "ben", "name": "Ben", "mode": "own", "content": OWN_CONTENT}
        lisa_own = dict(lisa, mode="own", content=OWN_CONTENT)
        P.sync(self.api, self.secrets, {"people": people(lisa_own, ben)}, BASE, changed={"people"})
        self.assertEqual(self.aio.posts, 2)
        self.assertTrue(self.secrets.get("person.ben.uuid"))
        lisa_cfg = self.aio.users["u-1"]["config"]
        self.assertEqual(lisa_cfg["preferredLanguages"], content_fields(OWN_CONTENT)["preferredLanguages"])
        self.assertEqual(lisa_cfg["services"], [{"id": "hers"}])  # not a person field: stays
        self.assertEqual(lisa_cfg["formatter"], {"id": "lisas"})
        self.assertIn("mergeStrategies", lisa_cfg["parentConfig"])

    def test_parent_config_kept_unless_people_changed(self):
        lisa = {"id": "lisa", "name": "Lisa", "mode": "inherit"}
        P.sync(self.api, self.secrets, {"people": people(lisa)}, BASE)
        mine = {"uuid": "u-main", "password": self.aio.users["u-1"]["config"]["parentConfig"]["password"],
                "mergeStrategies": {"fieldOverrides": {"formatter": "override"}}}
        self.aio.users["u-1"]["config"]["parentConfig"] = copy.deepcopy(mine)  # set on the website
        P.sync(self.api, self.secrets, {"people": people(lisa)}, {**BASE, "preferredLanguages": ["X"]},
               changed={"content"})
        self.assertEqual(self.aio.users["u-1"]["config"]["parentConfig"], mine)
        P.sync(self.api, self.secrets, {"people": people(lisa)}, BASE, changed={"people"})
        self.assertNotIn("mergeStrategies", self.aio.users["u-1"]["config"]["parentConfig"])

    def test_missing_parent_config_set_again(self):
        lisa = {"id": "lisa", "name": "Lisa", "mode": "inherit"}
        P.sync(self.api, self.secrets, {"people": people(lisa)}, BASE)
        del self.aio.users["u-1"]["config"]["parentConfig"]
        P.sync(self.api, self.secrets, {"people": people(lisa)}, BASE, changed={"content"})
        self.assertEqual(self.aio.users["u-1"]["config"]["parentConfig"]["uuid"], "u-main")

    def test_new_person_created_even_without_changes(self):
        """A resumed first setup has no changed answers, but missing configs are still created."""
        lisa = {"id": "lisa", "name": "Lisa", "mode": "inherit"}
        P.sync(self.api, self.secrets, {"people": people(lisa)}, BASE, changed=set())
        self.assertEqual(self.aio.posts, 1)
        self.assertTrue(self.secrets.get("person.lisa.uuid"))
        P.sync(self.api, self.secrets, {"people": people(lisa)}, {**BASE, "preferredLanguages": ["X"]}, changed=set())
        self.assertEqual(self.aio.puts, [])

    def test_update_keeping_own_drops_removed_section_keys(self):
        self.aio.users["u-9"] = {"password": "pw", "config": {"tmdbApiKey": "old", "formatter": {"id": "f"}}}
        P.update_keeping_own(self.api, "u-9", "pw", {"preferredLanguages": ["German"]})
        self.assertEqual(self.aio.users["u-9"]["config"],
                         {"formatter": {"id": "f"}, "preferredLanguages": ["German"]})

    def test_update_keeping_own_merges_presets_and_catalogs_per_entry(self):
        mine = {"type": "custom", "instanceId": "f3a", "enabled": True, "options": {"name": "AIOMetadata"}}
        my_row = {"id": "f3a1234.top", "type": "movie", "enabled": True}
        old_wizard = {"type": "tmdb-addon", "instanceId": "roadietmdb", "enabled": True, "options": {"language": "en-US"}}
        self.aio.users["u-9"] = {"password": "pw", "config": {"presets": [old_wizard, mine],
                                                              "catalogModifications": [my_row]}}
        new_tmdb = dict(old_wizard, options={"language": "de-DE"})
        wizard_row = {"id": "roadietmdbe3b0.tmdb.trending", "type": "movie", "enabled": True}
        P.update_keeping_own(self.api, "u-9", "pw", {"presets": [new_tmdb], "catalogModifications": [wizard_row]})
        config = self.aio.users["u-9"]["config"]
        self.assertEqual(config["presets"], [new_tmdb, mine])
        self.assertEqual(config["catalogModifications"], [my_row, wizard_row])

    def test_stream_limits_only_when_missing(self):
        limits = {"resolution": 5, "global": 20}
        self.aio.users["u-8"] = {"password": "pw", "config": {"resultLimits": {"global": 50}}}
        self.aio.users["u-9"] = {"password": "pw", "config": {"formatter": {"id": "f"}}}
        P.update_keeping_own(self.api, "u-8", "pw", {"resultLimits": limits})
        P.update_keeping_own(self.api, "u-9", "pw", {"resultLimits": limits})
        self.assertEqual(self.aio.users["u-8"]["config"]["resultLimits"], {"global": 50})  # the user's own
        self.assertEqual(self.aio.users["u-9"]["config"]["resultLimits"], limits)
        # cleared on the website: AIOStreams keeps an empty object (fields set to undefined)
        self.aio.users["u-7"] = {"password": "pw", "config": {"resultLimits": {}}}
        P.update_keeping_own(self.api, "u-7", "pw", {"resultLimits": limits})
        self.assertEqual(self.aio.users["u-7"]["config"]["resultLimits"], {})

    def test_main_only_does_nothing(self):
        P.sync(self.api, self.secrets, {}, BASE)
        self.assertEqual((self.aio.posts, self.aio.puts), (0, []))

    def test_person_secrets_saved_before_next_person(self):
        data = {"people": people({"id": "a", "name": "A", "mode": "inherit"},
                                 {"id": "b", "name": "B", "mode": "own", "content": OWN_CONTENT})}
        self.aio.fail_post_at = 2
        with self.assertRaises(AioError):
            P.sync(self.api, self.secrets, data, BASE)
        reloaded = SecretStore(self.secrets.path)
        self.assertTrue(all(reloaded.get(n) for n in P.secret_names("a")))
        self.assertIsNone(reloaded.get("person.b.uuid"))
        self.aio.posts = 0
        self.aio.fail_post_at = None
        P.sync(self.api, reloaded, data, BASE)
        self.assertEqual(self.aio.posts, 1)
        self.assertEqual(self.aio.puts, [])  # A is unchanged: no PUT
        self.assertTrue(reloaded.get("person.b.uuid"))

    def test_create_retries_on_429(self):
        self.aio.limit_posts = 2
        uuid, enc = self.api.create_user({"presets": [1]}, "pw")
        self.assertEqual((uuid, self.aio.posts), ("u-1", 3))
        self.assertEqual(self.sleeps, [10, 10])

    def test_create_gives_up_after_six_tries(self):
        self.aio.limit_posts = 99
        with self.assertRaises(AioError):
            self.api.create_user({"presets": [1]}, "pw")
        self.assertEqual(self.aio.posts, 6)

    def test_get_retried_after_rate_limit(self):
        self.aio.users["u-9"] = {"password": "pw", "config": {"a": 1}}
        self.aio.limit_gets = 1
        self.assertEqual(self.api.get_user("u-9", "pw"), {"a": 1})
        self.assertEqual((self.aio.gets, self.sleeps), (2, [6]))

    def test_put_gives_up_after_five_retries(self):
        self.aio.users["u-9"] = {"password": "pw", "config": {}}
        self.aio.limit_puts = 6
        with self.assertRaises(AioError) as cm:
            self.api.update_user("u-9", "pw", {"a": 1})
        self.assertEqual(cm.exception.code, "RATE_LIMIT_EXCEEDED")
        self.assertEqual((self.aio.limit_puts, self.sleeps), (0, [6] * 5))

    def test_unchanged_merged_config_sends_no_put(self):
        self.aio.users["u-9"] = {"password": "pw", "config": {"formatter": {"id": "f"},
                                                              "preferredLanguages": ["German"]}}
        P.update_keeping_own(self.api, "u-9", "pw", {"preferredLanguages": ["German"]})
        self.assertEqual(self.aio.puts, [])

    def test_create_other_errors_raise_at_once(self):
        self.aio.fail_post_at = 1
        with self.assertRaises(AioError):
            self.api.create_user({"presets": [1]}, "pw")
        self.assertEqual((self.aio.posts, self.sleeps), (1, []))

    def _state_with_lisa(self):
        state = State(Path(self.tmp.name) / "state.json")
        state.answers = {"people": people({"id": "lisa", "name": "Lisa", "mode": "inherit"})}
        P.sync(self.api, self.secrets, state.answers, BASE)
        return state

    def test_remove_person_keeps_entry_on_failure(self):
        state = self._state_with_lisa()
        self.aio.fail_delete = True
        self.assertFalse(P.remove(self.api, self.secrets, state, "lisa"))
        self.assertEqual([p["id"] for p in state.answers["people"]], ["main", "lisa"])
        self.assertTrue(all(self.secrets.get(n) for n in P.secret_names("lisa")))
        self.assertIn("u-1", self.aio.users)

    def test_remove_person_deletes_config_and_secrets(self):
        state = self._state_with_lisa()
        self.assertTrue(P.remove(self.api, self.secrets, state, "lisa"))
        self.assertEqual([p["id"] for p in state.answers["people"]], ["main"])
        self.assertEqual([p["id"] for p in State(state.path).answers["people"]], ["main"])
        self.assertTrue(all(self.secrets.get(n) is None for n in P.secret_names("lisa")))
        self.assertTrue(all(SecretStore(self.secrets.path).get(n) is None for n in P.secret_names("lisa")))
        self.assertNotIn("u-1", self.aio.users)

    def test_remove_unknown_person_changes_nothing(self):
        state = self._state_with_lisa()
        state.save()
        before = state.path.read_text()
        self.assertFalse(P.remove(self.api, self.secrets, state, "nobody"))
        self.assertEqual(state.path.read_text(), before)
        self.assertEqual([p["id"] for p in state.answers["people"]], ["main", "lisa"])

    def test_remove_person_already_gone_counts_as_removed(self):
        state = self._state_with_lisa()
        del self.aio.users["u-1"]  # deleted on the website: AIOStreams answers USER_INVALID_DETAILS
        self.aio.srv.routes["DELETE /api/v1/user"] = lambda b, h: err(400, "USER_INVALID_DETAILS")
        self.assertTrue(P.remove(self.api, self.secrets, state, "lisa"))
        self.assertEqual([p["id"] for p in state.answers["people"]], ["main"])
        self.assertIsNone(self.secrets.get("person.lisa.uuid"))

    def _password_changed(self):
        """Lisa's config still exists, but its password on the website is no longer ours."""
        self.aio.srv.routes["DELETE /api/v1/user"] = lambda b, h: err(400, "USER_INVALID_DETAILS")
        self.aio.srv.routes["/stremio/u-1/ENC-u-1/manifest.json"] = (
            lambda q, h: (200, json.dumps({"id": "aiostreams"}), "application/json"))

    def test_remove_person_wrong_password_keeps_person(self):
        state = self._state_with_lisa()
        self._password_changed()
        result = P.remove(self.api, self.secrets, state, "lisa")
        self.assertFalse(result)
        self.assertIs(result, P.Removal.PASSWORD)
        self.assertEqual([p["id"] for p in state.answers["people"]], ["main", "lisa"])
        self.assertEqual(self.secrets.get("person.lisa.uuid"), "u-1")
        self.assertIn("u-1", self.aio.users)

    def test_remove_dropped_wrong_password_keeps_person(self):
        state = self._state_with_lisa()
        self._password_changed()
        lisa = state.answers["people"][1]
        state.answers = {"people": people(), "_people_dropped": [lisa]}
        data = {"people": people()}
        kept = P.remove_dropped(self.api, self.secrets, state, data)
        self.assertEqual(kept, [(lisa, P.Removal.PASSWORD)])
        self.assertEqual([p["id"] for p in data["people"]], ["main", "lisa"])
        self.assertEqual(self.secrets.get("person.lisa.uuid"), "u-1")

    def test_remove_dropped_already_gone(self):
        state = self._state_with_lisa()
        self.aio.srv.routes["DELETE /api/v1/user"] = lambda b, h: err(400, "USER_INVALID_DETAILS")
        lisa = state.answers["people"][1]
        state.answers = {"people": people(), "_people_dropped": [lisa]}
        data = {"people": people()}
        self.assertEqual(P.remove_dropped(self.api, self.secrets, state, data), [])
        self.assertEqual([p["id"] for p in data["people"]], ["main"])
        self.assertIsNone(self.secrets.get("person.lisa.uuid"))

    def test_remove_person_404_counts_as_removed(self):
        state = self._state_with_lisa()
        self.aio.srv.routes["DELETE /api/v1/user"] = lambda b, h: err(404, "USER_NOT_FOUND")
        self.assertTrue(P.remove(self.api, self.secrets, state, "lisa"))

    def _setup_step(self, answers_people, state_people):
        """Run the AIOStreams setup step like an answers-file run: store() then the step."""
        import io
        from unittest import mock
        from roadie.aiostreams.api import STARTER_TEMPLATE
        from roadie.answers import Answers
        from roadie.engine import Context
        from roadie.paths import Paths
        from roadie.phases import stack
        from roadie.redact import Redactor
        from roadie.ui import Prompter
        example = json.loads((Path(__file__).parent.parent / "examples/answers.example.json").read_text())
        if answers_people is None:
            example.pop("people", None)
        else:
            example["people"] = answers_people
        self.aio.srv.routes["GET /api/v1/templates"] = lambda q, h: ok(
            [{"metadata": {"id": STARTER_TEMPLATE}, "config": {"presets": [], "sortCriteria": {"global": []}}}])
        paths = Paths(Path(self.tmp.name))
        state = State(paths.state_file)
        state.answers = {"people": state_people}
        P.sync(self.api, self.secrets, state.answers, BASE)  # Lisa (first other person) gets u-1
        self.aio.users["u-main"] = {"password": "main-pw", "config": {}}
        answers = Answers.from_dict(example)
        answers.store(state, self.secrets)
        out = io.StringIO()
        ctx = Context(paths=paths, state=state, secrets=self.secrets, ui=Prompter(inp=io.StringIO(""), out=out),
                      redact=Redactor([]), answers=answers)
        with mock.patch.object(stack, "_container_url", return_value=self.aio.srv.url):
            stack.aiostreams_config(ctx)
        return state, out.getvalue()

    def test_answers_file_without_people_keeps_people(self):
        lisa = {"id": "lisa", "name": "Lisa", "mode": "inherit"}
        state, _ = self._setup_step(None, people(lisa))
        self.assertEqual([p["id"] for p in state.answers["people"]], ["main", "lisa"])
        self.assertIn("u-1", self.aio.users)
        self.assertEqual(self.secrets.get("person.lisa.uuid"), "u-1")

    def test_answers_file_leaving_out_a_person_removes_their_config(self):
        lisa, ben = {"id": "lisa", "name": "Lisa", "mode": "inherit"}, {"id": "ben", "name": "Ben", "mode": "inherit"}
        state, _ = self._setup_step(people(ben), people(lisa, ben))
        self.assertEqual([p["id"] for p in state.answers["people"]], ["main", "ben"])
        self.assertNotIn("u-1", self.aio.users)  # Lisa's config is deleted: her addon URL stops working
        self.assertIsNone(self.secrets.get("person.lisa.uuid"))
        self.assertNotIn("_people_dropped", State(state.path).answers)

    def test_answers_file_removal_failure_keeps_person_and_warns(self):
        lisa = {"id": "lisa", "name": "Lisa", "mode": "inherit"}
        self.aio.fail_delete = True
        state, out = self._setup_step(people(), people(lisa))
        self.assertEqual([p["id"] for p in State(state.path).answers["people"]], ["main", "lisa"])
        self.assertIn("u-1", self.aio.users)
        self.assertEqual(self.secrets.get("person.lisa.uuid"), "u-1")
        self.assertIn("Lisa", out)

    def test_main_cannot_be_removed(self):
        state = self._state_with_lisa()
        self.assertFalse(P.remove(self.api, self.secrets, state, "main"))
        self.assertEqual(self.secrets.get("aiostreams_config_uuid"), "u-main")


if __name__ == "__main__":
    unittest.main()


class OwnCatalogsSwitchTest(unittest.TestCase):
    """Switching an existing person to own catalogs (or back) moves only their AIOMetadata address."""

    def setUp(self):
        from roadie.aiostreams import metadata as M
        self.M = M
        self.prowlarr = {"type": "prowlarr", "instanceId": "roadieprowlarr", "enabled": True, "options": {}}

    def run_sync(self, current_url, own_uuid, own_catalogs, record):
        from roadie.aiostreams import people as P
        api = mock.Mock()
        current = {"presets": [self.prowlarr, self.M.preset("X")]}
        current["presets"][1]["options"]["manifestUrl"] = current_url
        api.get_user.return_value = copy.deepcopy(current)
        store = {"main_uuid_name": None}
        secrets = mock.Mock()
        values = {"aiostreams_config_uuid": "mu", "aiostreams_config_password": "mp", "person.lisa.uuid": "lu",
                  "person.lisa.password": "lp", "person.lisa.metadata_uuid": own_uuid}
        secrets.get = values.get
        secrets.generate = lambda name, n=24: values.setdefault(name, "gen")
        data = {"people": [{"id": "main", "name": "Me", "mode": "main"},
                           {"id": "lisa", "name": "Lisa", "mode": "inherit", **({"own_catalogs": True} if own_catalogs else {})}]}
        base = {"presets": [self.prowlarr, self.M.preset("MAIN")]}
        P.sync(api, secrets, data, base, {"people"}, {"lisa": record})
        if not api.update_user.called:
            return current_url
        return next(p for p in api.update_user.call_args.args[2]["presets"]
                    if p["instanceId"] == self.M.INSTANCE_ID)["options"]["manifestUrl"]

    def test_switch_to_own(self):
        url = self.run_sync(self.M.MANIFEST.format(uuid="MAIN"), "OWN", True, {})
        self.assertIn("/OWN/", url)

    def test_switch_back_to_main(self):
        url = self.run_sync(self.M.MANIFEST.format(uuid="OWN"), "OWN", False, {})
        self.assertIn("/MAIN/", url)

    def test_hand_made_address_stays(self):
        url = self.run_sync("http://aiometadata:3232/stremio/HANDMADE/manifest.json", "OWN", True, {})
        self.assertIn("/HANDMADE/", url)
