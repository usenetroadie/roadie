"""Own AIOMetadata: only with a TMDB key and enough RAM; rendered files, AIOStreams preset, setup step."""

import copy
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from roadie.aiostreams import metadata as M
from roadie.aiostreams.builder import build_user_config
from roadie.answers import Answers
from roadie.render import build_render_config, load_versions, wants_metadata
from roadie.render.authelia import METADATA_IMAGE_PATHS, METADATA_PLAYER_PATHS, METADATA_POST_PATHS, config_dict
from roadie.render.compose import compose_dict
from roadie.render.envfiles import render_env
from roadie.render.traefik import dynamic_dict

EXAMPLE = json.loads((Path(__file__).parent.parent / "examples/answers.example.json").read_text())
KEY = "a" * 32
SECRETS = {"aiostreams_secret_key": "s", "aiostreams_admin_password": "a", "aiostreams_playback_password": "p",
           "prowlarr_api_key": "k", "aiometadata_addon_password": "AddonPw123"}


def answers(tmdb=KEY):
    data = copy.deepcopy(EXAMPLE)
    data["tmdb_api_key"] = tmdb
    return Answers.from_dict(data)


def cfg(tmdb=KEY, mem=4000):
    return build_render_config(answers(tmdb), load_versions(), mem_mb=mem, disk_gb=60)


class WhenTest(unittest.TestCase):
    def test_needs_key_and_ram(self):
        self.assertTrue(wants_metadata({"tmdb_api_key": KEY}, 4000))
        self.assertFalse(wants_metadata({"tmdb_api_key": None}, 8000))
        self.assertTrue(wants_metadata({"tmdb_api_key": KEY}, 2000))  # 2 GB servers too
        self.assertFalse(wants_metadata({"tmdb_api_key": KEY}, 1000))
        self.assertFalse(wants_metadata({"tmdb_api_key": "e" * 60}, 8000))  # read access token: not taken
        self.assertTrue(cfg().metadata)
        self.assertFalse(cfg(tmdb=None).metadata)


class RenderTest(unittest.TestCase):
    def test_services_only_with_metadata(self):
        services = compose_dict(cfg())["services"]
        self.assertTrue(services["aiometadata"]["image"].startswith("ghcr.io/cedya77/aiometadata:"))
        self.assertEqual(services["aiometadata"]["depends_on"], {"aiometadata-redis": {"condition": "service_healthy"}})
        self.assertIn("redis:8", services["aiometadata-redis"]["image"])
        self.assertNotIn("ports", services["aiometadata"])  # only Traefik publishes ports
        # healthy only once it accepts requests (while loading data it answers 503)
        self.assertIn("http://127.0.0.1:3232/health/ready", services["aiometadata"]["healthcheck"]["test"])
        plain = compose_dict(cfg(tmdb=None))["services"]
        self.assertNotIn("aiometadata", plain)
        self.assertNotIn("aiometadata-redis", plain)

    def test_env_file_has_key_and_no_warmup(self):
        files = {f.path: f for f in render_env(cfg(), SECRETS, [], KEY)}
        env = files["env/aiometadata.env"]
        self.assertEqual(env.mode, 0o600)
        self.assertIn(f"BUILT_IN_TMDB_API_KEY={KEY}", env.content)
        self.assertIn("ADDON_PASSWORD=AddonPw123", env.content)
        self.assertIn("ENABLE_CACHE_WARMING=false", env.content)
        self.assertIn("HOST_NAME=https://metadata.example.duckdns.org", env.content)
        self.assertNotIn("env/aiometadata.env", {f.path for f in render_env(cfg(tmdb=None), SECRETS, [], None)})
        self.assertIn("MANIFEST_CACHE_TTL=900", files["env/aiostreams.env"].content)

    def test_settings_page_behind_login(self):
        routers = dynamic_dict(cfg())["http"]["routers"]
        self.assertEqual(routers["metadata"]["middlewares"], ["authelia"])
        self.assertNotIn("metadata", dynamic_dict(cfg(tmdb=None))["http"]["routers"])
        rules = config_dict(cfg())["access_control"]["rules"]
        protected = [r for r in rules if r.get("policy") == "two_factor" and "metadata.example.duckdns.org" in r["domain"]]
        self.assertTrue(protected)
        open_rules = [r for r in rules if r.get("policy") == "bypass" and "metadata.example.duckdns.org" in r["domain"]]
        self.assertEqual([r["resources"] for r in open_rules],  # images and player routes, progress reports
                         [METADATA_IMAGE_PATHS + METADATA_PLAYER_PATHS, METADATA_POST_PATHS])


class PresetTest(unittest.TestCase):
    TEMPLATE = {"presets": [], "sortCriteria": {"global": []}}

    def test_replaces_the_simple_tmdb_addon(self):
        preset = M.preset("uuid-1")
        cfg_ = build_user_config(answers(), copy.deepcopy(self.TEMPLATE), "playback:pw", preset)
        ids = [p["instanceId"] for p in cfg_["presets"]]
        self.assertEqual(ids, ["roadieprowlarr", "roadiemetadata", "roadiestreaming", "roadiecollections"])
        meta = cfg_["presets"][1]
        self.assertEqual(meta["options"]["manifestUrl"], "http://aiometadata:3232/stremio/uuid-1/manifest.json")
        self.assertFalse(any(m["id"].startswith("roadietmdb") for m in cfg_["catalogModifications"]))
        without = build_user_config(answers(), copy.deepcopy(self.TEMPLATE), "playback:pw")
        self.assertIn("roadietmdb", [p["instanceId"] for p in without["presets"]])

    def test_config_uses_tmdb_only_and_the_first_language(self):
        c = M.metadata_config({"languages": ["German", "English"]})
        self.assertEqual(c["language"], "de-DE")
        self.assertEqual(c["providers"]["series"], "tmdb")  # TVDB would need another key
        self.assertFalse(c["usePosterProxy"])  # players load posters from TMDB, not from this server
        self.assertEqual(c["apiKeys"], {})
        self.assertTrue(all(cat["source"] == "tmdb" and cat["id"].startswith("tmdb.") for cat in c["catalogs"]))
        self.assertTrue(any(cat["showInHome"] for cat in c["catalogs"]))


class StepTest(unittest.TestCase):
    def ctx(self, metadata=True, uuid=None):
        from roadie.redact import Redactor
        store = {"aiometadata_addon_password": "AddonPw123"}
        if uuid:
            store["aiometadata_uuid"] = uuid
        secrets = mock.Mock()
        secrets.get = store.get
        secrets.set = store.__setitem__
        secrets.generate = lambda name, n=24: store.setdefault(name, "CfgPw" + "x" * 10)
        return SimpleNamespace(extra={"render": SimpleNamespace(metadata=metadata)}, secrets=secrets,
                               answers=SimpleNamespace(data={"content": {"languages": ["English"]}}),
                               redact=Redactor()), store

    def test_created_once(self):
        from roadie.phases import stack
        ctx, store = self.ctx()
        with mock.patch.object(stack, "_container_url", return_value="http://10.0.0.9:3232"), \
                mock.patch.object(M, "create", return_value="new-uuid") as create:
            stack.metadata_config(ctx)
            stack.metadata_config(ctx)  # a second run leaves the user's configuration alone
        create.assert_called_once()
        self.assertEqual(store["aiometadata_uuid"], "new-uuid")
        self.assertEqual(create.call_args.args[3], "AddonPw123")

    def test_nothing_without_metadata(self):
        from roadie.phases import stack
        ctx, _ = self.ctx(metadata=False)
        with mock.patch.object(M, "create") as create:
            stack.metadata_config(ctx)
        create.assert_not_called()



class PersonCatalogsTest(unittest.TestCase):
    """A person with own catalogs gets an own AIOMetadata configuration (a real user's wish)."""

    def data(self):
        return {"content": {"languages": ["English"]},
                "people": [{"id": "main", "name": "Me", "mode": "main"},
                           {"id": "lisa", "name": "Lisa", "mode": "inherit", "own_catalogs": True},
                           {"id": "tom", "name": "Tom", "mode": "own", "content": {"languages": ["German"]},
                            "own_catalogs": True},
                           {"id": "kim", "name": "Kim", "mode": "inherit"}]}

    def secrets(self):
        store = {}
        s = mock.Mock()
        s.get = store.get
        s.set = store.__setitem__
        s.generate = lambda name, n=24: store.setdefault(name, f"pw-{name}-123456")
        return s, store

    def test_created_once_per_person_with_own_catalogs(self):
        secrets, store = self.secrets()
        with mock.patch.object(M, "create", side_effect=["u-lisa", "u-tom"]) as create:
            M.ensure_people("http://x:3232", secrets, self.data(), "AddonPw")
            M.ensure_people("http://x:3232", secrets, self.data(), "AddonPw")  # nothing new the second time
        self.assertEqual(create.call_count, 2)
        self.assertEqual(store["person.lisa.metadata_uuid"], "u-lisa")
        self.assertEqual(store["person.tom.metadata_uuid"], "u-tom")
        self.assertNotIn("person.kim.metadata_uuid", store)
        self.assertEqual(create.call_args_list[0].args[1]["language"], "en-US")  # Lisa: the main languages
        self.assertEqual(create.call_args_list[1].args[1]["language"], "de-DE")  # Tom: his own
        self.assertEqual(create.call_args_list[0].args[3], "AddonPw")

    def test_child_config_points_at_the_persons_configuration(self):
        from roadie.aiostreams import people as P
        base = {"presets": [{"type": "prowlarr", "instanceId": "roadieprowlarr", "options": {}}, M.preset("MAIN")]}
        child = P.child_config(base, "mu", "mp", {"id": "lisa", "mode": "inherit"}, metadata_uuid="u-lisa")
        meta = next(p for p in child["presets"] if p["instanceId"] == M.INSTANCE_ID)
        self.assertIn("/u-lisa/", meta["options"]["manifestUrl"])
        self.assertIn("/MAIN/", base["presets"][1]["options"]["manifestUrl"])  # base untouched
        plain = P.child_config(base, "mu", "mp", {"id": "kim", "mode": "inherit"})
        self.assertIn("/MAIN/", plain["presets"][1]["options"]["manifestUrl"])

    def test_answers_accept_own_catalogs_only_as_bool_and_not_for_main(self):
        from roadie.answers import validate
        example = json.loads((Path(__file__).parent.parent / "examples/answers.example.json").read_text())
        example["people"] = [{"id": "main", "name": "Me", "mode": "main"},
                             {"id": "lisa", "name": "Lisa", "mode": "inherit", "own_catalogs": True}]
        self.assertEqual(validate(example), [])
        example["people"][1]["own_catalogs"] = "yes"
        self.assertTrue(any(e.startswith("people[1].own_catalogs") for e in validate(example)))
        example["people"][1]["own_catalogs"] = True
        example["people"][0]["own_catalogs"] = True
        self.assertTrue(any(e.startswith("people[0].own_catalogs") for e in validate(example)))

if __name__ == "__main__":
    unittest.main()
