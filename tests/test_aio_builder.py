import copy
import json
import unittest
from pathlib import Path

from roadie.aiostreams.builder import (PERSON_FIELDS, SECTIONS, WIZARD_PRESETS, build_user_config, catalog_presets,
                                             content_fields, merge_catalog_mods, merge_presets, merge_sections)
from roadie.answers import Answers

EXAMPLE = json.loads((Path(__file__).parent.parent / "examples/answers.example.json").read_text())
TEMPLATE = {
    "presets": [{"type": "some-addon", "instanceId": "a", "enabled": True, "options": {"name": "Some addon"}}],
    "preferredResolutions": ["2160p", "1440p", "1080p", "720p"],
    "sortCriteria": {"global": [{"key": "cached", "direction": "desc"}, {"key": "resolution", "direction": "desc"},
                                {"key": "language", "direction": "desc"}]},
    "tmdbApiKey": "<template_placeholder>",
    "titleMatching": {"enabled": True, "similarityThreshold": 1},
    "yearMatching": {"enabled": True},
    "digitalReleaseFilter": {"enabled": True},
    "formatter": {"id": "prism"},
}
AUTH = "playback:PlayPw123"


def build(**changes):
    data = copy.deepcopy(EXAMPLE)
    for key, value in changes.items():
        node = data
        *parents, last = key.split(".")
        for p in parents:
            node = node[p]
        node[last] = value
    return build_user_config(Answers.from_dict(data), copy.deepcopy(TEMPLATE), AUTH)


class BuilderTest(unittest.TestCase):
    def test_template_not_mutated_and_kept(self):
        self.assertEqual(build()["formatter"], {"id": "prism"})

    def test_languages_in_order_with_original(self):
        self.assertEqual(build()["preferredLanguages"], ["German", "English", "Original"])
        cfg = build(**{"content.original_audio": False})
        self.assertEqual(cfg["preferredLanguages"], ["German", "English"])

    def test_language_sorted_before_resolution(self):
        keys = [c["key"] for c in build()["sortCriteria"]["global"]]
        self.assertLess(keys.index("language"), keys.index("resolution"))
        self.assertEqual(keys.count("language"), 1)

    def test_1080p_first_when_not_4k(self):
        self.assertEqual(build()["preferredResolutions"][0], "2160p")
        self.assertEqual(build(**{"content.prefer_4k": False})["preferredResolutions"][:2], ["1080p", "2160p"])

    def test_hdr_yes_prefers_hdr_tags(self):
        cfg = build(**{"content.hdr": True})
        self.assertEqual(cfg["preferredVisualTags"], ["HDR+DV", "DV", "HDR10+", "HDR10", "HDR"])
        self.assertEqual(cfg["excludedVisualTags"], [])

    def test_hdr_no_excludes_dv_only(self):
        cfg = build(**{"content.hdr": False})
        self.assertEqual(cfg["preferredVisualTags"], [])
        self.assertEqual(cfg["excludedVisualTags"], ["DV Only"])

    def test_content_fields_has_exactly_person_fields(self):
        content = copy.deepcopy(EXAMPLE["content"])
        self.assertEqual(set(content_fields(content)), set(PERSON_FIELDS))

    def test_services(self):
        services = {s["id"]: s for s in build()["services"]}
        self.assertEqual(list(services), ["aiostreams"])
        self.assertEqual(services["aiostreams"]["credentials"], {"aiostreamsAuth": AUTH})
        self.assertTrue(all(s["enabled"] for s in services.values()))

    def test_presets(self):
        presets = [p["type"] for p in build()["presets"]]  # the template's own addons are not used
        self.assertEqual(presets, ["prowlarr", "tmdb-addon", "streaming-catalogs", "tmdb-collections"])
        self.assertEqual(TEMPLATE["presets"][0]["instanceId"], "a")  # template untouched

    def test_prowlarr_preset_has_no_key(self):
        prowlarr = [p for p in build()["presets"] if p["type"] == "prowlarr"][0]
        self.assertNotIn("prowlarrApiKey", prowlarr["options"])
        self.assertEqual(prowlarr["options"]["services"], ["aiostreams"])

    def test_no_tmdb_key_disables_tmdb_features(self):
        cfg = build()
        self.assertNotIn("tmdbApiKey", cfg)
        for key in ("titleMatching", "yearMatching", "digitalReleaseFilter"):
            self.assertFalse(cfg[key]["enabled"], key)
        self.assertEqual(cfg["titleMatching"]["similarityThreshold"], 1)

    def test_tmdb_key_v3_and_v4(self):
        self.assertEqual(build(tmdb_api_key="a" * 32)["tmdbApiKey"], "a" * 32)
        cfg = build(tmdb_api_key="e" * 60)
        self.assertEqual(cfg["tmdbAccessToken"], "e" * 60)
        self.assertNotIn("tmdbApiKey", cfg)
        self.assertTrue(cfg["titleMatching"]["enabled"])

    def test_proven_defaults(self):
        cfg = build()
        self.assertTrue(cfg["failover"]["enabled"])
        self.assertEqual(cfg["failover"]["contentTypes"], ["usenet"])
        self.assertTrue(cfg["precacheNextEpisode"])
        self.assertTrue(any(e["score"] > 0 and "age(" in e["expression"] for e in cfg["rankedStreamExpressions"]))
        self.assertTrue(any(e["score"] < 0 for e in cfg["rankedStreamExpressions"]))
        self.assertEqual(cfg["rankedRegexPatterns"][0]["name"], "Fix release")

    def test_forced_subtitles_in_the_chosen_languages_rank_higher(self):
        cfg = build(**{"content.languages": ["German", "English"]})
        expressions = [e["expression"] for e in cfg["rankedStreamExpressions"]]
        self.assertIn("subtitleTrack(streams, 'forced', 'lang=German|English')", expressions)

    def test_catalog_presets_language_de(self):
        content = {"languages": ["German", "English"]}
        presets = {p["instanceId"]: p for p in catalog_presets(content, None)}
        self.assertEqual(list(presets), ["roadietmdb", "roadiestreaming", "roadiecollections"])
        self.assertEqual(presets["roadietmdb"]["options"]["language"], "de-DE")
        self.assertEqual(presets["roadiecollections"]["options"]["language"], "de")
        self.assertTrue(all(p["enabled"] and p["options"]["timeout"] > 0 for p in presets.values()))
        self.assertEqual(presets["roadiestreaming"]["options"]["catalogs"], ["nfx", "amp", "dnp"])

    def test_catalog_language_english_and_fallbacks(self):
        def langs(first):
            presets = {p["instanceId"]: p["options"].get("language") for p in catalog_presets({"languages": [first]}, None)}
            return presets["roadietmdb"], presets["roadiecollections"]
        self.assertEqual(langs("English"), ("en-US", "en"))
        self.assertEqual(langs("French"), ("fr-FR", "fr"))
        self.assertEqual(langs("Latino"), ("en-US", "en"))  # no catalog language for it
        self.assertEqual(langs("Chinese"), ("zh-CN", "en"))  # collections know no Chinese

    def test_catalogs_without_tmdb_key(self):
        content = {"languages": ["German"]}
        self.assertEqual(catalog_presets(content, None), catalog_presets(content, "a" * 32))
        cfg = build(tmdb_api_key=None)
        self.assertIn("roadietmdb", [p["instanceId"] for p in cfg["presets"]])

    def test_home_catalogs_few_then_discover_only(self):
        mods = build()["catalogModifications"]
        home = [m for m in mods if not m.get("onlyOnDiscover")]
        self.assertTrue(6 <= len(home) <= 10, len(home))
        self.assertEqual(mods[:len(home)], home)  # start page rows come first
        self.assertEqual(home[0], {"id": "roadietmdbe3b0.tmdb.trending", "type": "movie", "enabled": True})
        self.assertTrue(all(m["enabled"] for m in mods))
        self.assertEqual(len({(m["id"], m["type"]) for m in mods}), len(mods))

    def test_merge_catalog_mods_keeps_user_entries(self):
        new = build()["catalogModifications"]
        mine = {"id": "abc1234.top", "type": "movie", "enabled": True}
        hidden = dict(new[0], enabled=False)  # the user hid a wizard row on the website
        merged = merge_catalog_mods([mine, hidden], new)
        self.assertEqual(merged[:2], [mine, hidden])
        self.assertEqual(merged[2:], new[1:])

    def test_user_preset_survives_merge(self):
        mine = {"type": "custom", "instanceId": "f3a", "enabled": True, "options": {"name": "AIOMetadata"}}
        old_tmdb = {"type": "tmdb-addon", "instanceId": "roadietmdb", "enabled": True, "options": {"language": "en-US"}}
        new = build()["presets"]
        merged = merge_presets([mine, old_tmdb], new)
        self.assertEqual(merged, new + [mine])

    def test_wizard_preset_replaced(self):
        old = [{"type": "prowlarr", "instanceId": "roadieprowlarr", "enabled": False, "options": {}},
               {"type": "tmdb-addon", "instanceId": "roadietmdb", "enabled": True, "options": {"language": "en-US"}}]
        new = build()["presets"]
        merged = merge_presets(old, new)
        self.assertEqual(merged, new)  # prowlarr and tmdb replaced by the new ones
        self.assertEqual(WIZARD_PRESETS, ("roadieprowlarr", "roadieprowlarrfallback", "roadietmdb", "roadiestreaming",
                                          "roadiecollections", "roadiemetadata"))
        self.assertNotIn("presets", SECTIONS["streams"])

    def test_fallback_indexers_are_asked_only_when_the_main_ones_find_too_little(self):
        ixs = [{"name": "A", "url": "https://a.example.com", "api_key": "k"},
               {"name": "B", "url": "https://b.example.com", "api_key": "k", "fallback": True}]
        cfg = build(indexers=ixs, **{"content.languages": ["German", "English"]})
        presets = {p["instanceId"]: p for p in cfg["presets"]}
        self.assertEqual(presets["roadieprowlarr"]["options"]["tags"], "roadie-main")
        self.assertEqual(presets["roadieprowlarrfallback"]["options"]["tags"], "roadie-fallback")
        groups = cfg["groups"]
        self.assertEqual(groups["behaviour"], "sequential")
        self.assertEqual([g["addons"] for g in groups["groupings"]], [["roadieprowlarr"], ["roadieprowlarrfallback"]])
        self.assertEqual(groups["groupings"][1]["condition"], "count(language(totalStreams, 'German')) < 5")
        english = build(indexers=ixs, **{"content.languages": ["English"]})
        self.assertEqual(english["groups"]["groupings"][1]["condition"], "count(totalStreams) < 5")

    def test_no_split_no_tags_no_groups(self):
        cfg = build()
        self.assertNotIn("groups", cfg)
        self.assertNotIn("tags", next(p for p in cfg["presets"] if p["instanceId"] == "roadieprowlarr")["options"])
        all_fallback = [{"name": "A", "url": "https://a.example.com", "api_key": "k", "fallback": True}]
        self.assertNotIn("groups", build(indexers=all_fallback))

    def test_merge_groups_keeps_the_users_own(self):
        from roadie.aiostreams.builder import merge_groups
        own = {"enabled": True, "groupings": [{"addons": ["mine"], "condition": "true"}]}
        ours = {"enabled": True, "groupings": [{"addons": ["roadieprowlarrfallback"], "condition": "true"}]}
        self.assertEqual(merge_groups(own, None), own)
        self.assertIsNone(merge_groups(ours, None))
        self.assertEqual(merge_groups(own, ours), ours)

    def test_stream_limits_set(self):
        self.assertEqual(build()["resultLimits"], {"resolution": 5, "global": 20})
        self.assertNotIn("resultLimits", SECTIONS["quality"])  # merged by people.update_keeping_own

    def test_merge_sections_keeps_hand_edits(self):
        current = {"formatter": {"id": "custom"}, "preferredLanguages": ["English"], "services": [1]}
        new = {"formatter": {"id": "prism"}, "preferredLanguages": ["German"], "services": [2]}
        merged = merge_sections(current, new, ["languages"])
        self.assertEqual(merged, {"formatter": {"id": "custom"}, "preferredLanguages": ["German"], "services": [1]})
        with self.assertRaises(ValueError):
            merge_sections(current, new, ["nope"])
