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
from roadie.render.authelia import config_dict
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
        self.assertFalse(wants_metadata({"tmdb_api_key": KEY}, 2000))
        self.assertFalse(wants_metadata({"tmdb_api_key": "e" * 60}, 8000))  # read access token: not taken
        self.assertTrue(cfg().metadata)
        self.assertFalse(cfg(tmdb=None).metadata)


class RenderTest(unittest.TestCase):
    def test_services_only_with_metadata(self):
        services = compose_dict(cfg())["services"]
        self.assertEqual(services["aiometadata"]["image"], "ghcr.io/cedya77/aiometadata:3.9.0")
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

    def test_settings_page_behind_login(self):
        routers = dynamic_dict(cfg())["http"]["routers"]
        self.assertEqual(routers["metadata"]["middlewares"], ["authelia"])
        self.assertNotIn("metadata", dynamic_dict(cfg(tmdb=None))["http"]["routers"])
        rules = config_dict(cfg())["access_control"]["rules"]
        protected = [r for r in rules if r.get("policy") == "two_factor" and "metadata.example.duckdns.org" in r["domain"]]
        self.assertTrue(protected)
        self.assertFalse(any("metadata.example.duckdns.org" in r["domain"] for r in rules if r.get("policy") == "bypass"))


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


if __name__ == "__main__":
    unittest.main()
