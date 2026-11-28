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
        by_id = {p["instanceId"]: p for p in new}
        self.assertEqual(merge_presets([mine, old_tmdb], new), [mine, by_id["roadietmdb"]])
        readd = {"roadieprowlarr"}
        self.assertEqual(merge_presets([mine, old_tmdb], new, readd),
                         [by_id["roadieprowlarr"], mine, by_id["roadietmdb"]])

    def test_wizard_preset_replaced(self):
        old = [{"type": "prowlarr", "instanceId": "roadieprowlarr", "enabled": True, "options": {}},
               {"type": "tmdb-addon", "instanceId": "roadietmdb", "enabled": True, "options": {"language": "en-US"}}]
        new = build()["presets"]
        merged = merge_presets(old, new)
        self.assertEqual(merged, new[:2])  # prowlarr and tmdb replaced by the new ones, nothing added
        self.assertEqual(WIZARD_PRESETS, ("roadieprowlarr", "roadieprowlarrfallback", "roadietmdb", "roadiestreaming",
                                          "roadiecollections", "roadiemetadata", "roadieeasynews"))
        self.assertNotIn("presets", SECTIONS["streams"])

    def test_deleted_roadie_addon_stays_deleted(self):
        new = build()["presets"]
        current = [p for p in new if p["instanceId"] != "roadiestreaming"]
        merged = merge_presets(current, new)
        self.assertNotIn("roadiestreaming", [p["instanceId"] for p in merged])
        self.assertEqual(merged, current)

    def test_disabled_roadie_addon_stays_disabled(self):
        new = build()["presets"]
        current = [dict(p, enabled=p["instanceId"] != "roadiecollections") for p in new]
        merged = {p["instanceId"]: p for p in merge_presets(current, new, set(WIZARD_PRESETS))}
        self.assertFalse(merged["roadiecollections"]["enabled"])
        self.assertTrue(merged["roadieprowlarr"]["enabled"])

    def test_prowlarr_preset_readded_when_indexers_changed(self):
        from roadie.aiostreams.builder import FALLBACK_PRESET, presets_for
        new = build()["presets"]
        current = [p for p in new if p["instanceId"] != "roadieprowlarr"]
        flags = presets_for({"indexers"})
        self.assertTrue(flags["write"])
        self.assertEqual(flags["readd"], {"roadieprowlarr", FALLBACK_PRESET})
        merged = merge_presets(current, new, flags["readd"])
        self.assertEqual(merged[0]["instanceId"], "roadieprowlarr")
        self.assertEqual(merged[1:], current)

    def test_user_presets_keep_order(self):
        new = build()["presets"]
        a = {"type": "custom", "instanceId": "aaa", "enabled": True, "options": {}}
        b = {"type": "custom", "instanceId": "bbb", "enabled": False, "options": {}}
        current = [a, new[2], b, new[0]]  # the user sorted wizard and own addons on the website
        merged = merge_presets(current, new)
        self.assertEqual([p["instanceId"] for p in merged], ["aaa", new[2]["instanceId"], "bbb", "roadieprowlarr"])
        self.assertEqual(merged[2], b)

    def test_tmdb_slot_swapped_in_place(self):
        """AIOMetadata replaces the simple TMDB addon at the same place, with the user's enabled."""
        from roadie.aiostreams import metadata
        mine = {"type": "custom", "instanceId": "aaa", "enabled": True, "options": {}}
        old_tmdb = {"type": "tmdb-addon", "instanceId": "roadietmdb", "enabled": False, "options": {}}
        new = [metadata.preset("META") if p["instanceId"] == "roadietmdb" else p for p in build()["presets"]]
        merged = merge_presets([mine, old_tmdb], new)
        self.assertEqual([p["instanceId"] for p in merged], ["aaa", "roadiemetadata"])
        self.assertFalse(merged[1]["enabled"])
        # and back: with readd nothing is doubled
        back = merge_presets(merged, build()["presets"], {"roadietmdb", "roadiemetadata"})
        self.assertEqual([p["instanceId"] for p in back], ["aaa", "roadietmdb"])
        self.assertFalse(back[1]["enabled"])
        # deleted both: nothing comes back without readd
        self.assertEqual(merge_presets([mine], new), [mine])

    def test_row_addon(self):
        from roadie.aiostreams.builder import row_addon
        rows = build()["catalogModifications"]
        self.assertEqual(row_addon(rows[0]), "roadietmdb")
        self.assertEqual(row_addon({"id": "f3a1234.top"}), "f3a")

    def test_sections_for_changed_answers(self):
        from roadie.aiostreams.builder import sections_for
        self.assertEqual(sections_for({"*"}), list(SECTIONS))
        self.assertEqual(sections_for(set()), [])
        self.assertEqual(sections_for({"content"}), ["languages", "quality", "ranking"])
        self.assertEqual(sections_for({"indexers"}), ["streams"])
        self.assertEqual(sections_for({"tmdb_api_key"}), ["metadata"])
        self.assertEqual(sections_for({"usenet", "auth", "people"}), [])
        self.assertEqual(sections_for({"tmdb_api_key", "indexers"}), ["streams", "metadata"])

    def test_presets_for_changed_answers(self):
        from roadie.aiostreams.builder import FALLBACK_PRESET, presets_for
        catalogs = {"roadietmdb", "roadiestreaming", "roadiecollections", "roadiemetadata"}
        self.assertEqual(presets_for(set()), {"write": False, "readd": set()})
        self.assertEqual(presets_for({"usenet"}), {"write": False, "readd": set()})
        self.assertEqual(presets_for({"content"}), {"write": True, "readd": set()})
        self.assertEqual(presets_for({"catalogs"}), {"write": True, "readd": catalogs})
        self.assertEqual(presets_for({"tmdb_api_key"}), {"write": True, "readd": catalogs})
        self.assertEqual(presets_for({"*"}), {"write": True,
                                              "readd": catalogs | {"roadieprowlarr", FALLBACK_PRESET, "roadieeasynews"}})

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


class PresetOptionsTest(unittest.TestCase):
    """Settings inside roadie's own addons that the user changed by hand stay (per option key)."""

    def preset(self, iid, **options):
        return {"type": "custom", "instanceId": iid, "enabled": True, "options": options}

    def test_first_look_records_without_taking_users_value(self):
        record = {}
        current = [self.preset("roadiemetadata", name="AIOMetadata", manifestUrl="http://aiometadata:3232/stremio/mine/manifest.json")]
        new = [self.preset("roadiemetadata", name="AIOMetadata", manifestUrl="http://aiometadata:3232/stremio/main/manifest.json")]
        merged = merge_presets(current, new, record=record)
        self.assertIn("/mine/", merged[0]["options"]["manifestUrl"])  # differed on first look: the user's
        self.assertIn("manifestUrl", record["roadiemetadata"])

    def test_untouched_option_gets_roadies_new_value(self):
        record = {}
        old = [self.preset("roadieprowlarr", name="Prowlarr", timeout=10000)]
        merge_presets(old, old, record=record)  # roadie wrote this
        new = [self.preset("roadieprowlarr", name="Prowlarr", timeout=10000, tags="roadie-main")]
        merged = merge_presets(old, new, record=record)
        self.assertEqual(merged[0]["options"]["tags"], "roadie-main")
        newer = [self.preset("roadieprowlarr", name="Prowlarr", timeout=12000, tags="roadie-main")]
        self.assertEqual(merge_presets(merged, newer, record=record)[0]["options"]["timeout"], 12000)

    def test_hand_edited_option_stays_across_changes(self):
        record = {}
        wrote = [self.preset("roadiemetadata", manifestUrl="u-main", name="AIOMetadata")]
        merge_presets(wrote, wrote, record=record)
        edited = [self.preset("roadiemetadata", manifestUrl="u-person", name="AIOMetadata")]
        merged = merge_presets(edited, [self.preset("roadiemetadata", manifestUrl="u-main", name="Meta")], record=record)
        self.assertEqual(merged[0]["options"], {"manifestUrl": "u-person", "name": "Meta"})

    def test_option_the_user_added_stays(self):
        record = {}
        wrote = [self.preset("roadieprowlarr", name="Prowlarr")]
        merge_presets(wrote, wrote, record=record)
        mine = [self.preset("roadieprowlarr", name="Prowlarr", mediaTypes=["anime"])]
        self.assertEqual(merge_presets(mine, wrote, record=record)[0]["options"]["mediaTypes"], ["anime"])

    def test_without_record_behaves_as_before(self):
        current = [self.preset("roadieprowlarr", name="Mine")]
        self.assertEqual(merge_presets(current, [self.preset("roadieprowlarr", name="Prowlarr")])[0]["options"]["name"],
                         "Prowlarr")

    def test_record_holds_no_values(self):
        record = {}
        p = [self.preset("roadietmdb", apiKey="secret-tmdb-key")]
        merge_presets(p, p, record=record)
        self.assertNotIn("secret-tmdb-key", json.dumps(record))


class ServicesAndGroupsTest(unittest.TestCase):
    """Services the user added and groups the user changed stay when roadie writes its part."""

    def test_users_own_service_stays(self):
        from roadie.aiostreams.builder import merge_services
        record = {}
        roadie = [{"id": "aiostreams", "enabled": True, "credentials": {"aiostreamsAuth": "p:1"}}]
        mine = {"id": "easynews", "enabled": True, "credentials": {"username": "me", "password": "x"}}
        merged = merge_services(roadie + [mine], roadie, record)
        self.assertEqual([s["id"] for s in merged], ["aiostreams", "easynews"])
        self.assertEqual(merged[1], mine)

    def test_service_roadie_added_goes_when_no_longer_wanted(self):
        from roadie.aiostreams.builder import merge_services
        record = {}
        en = {"id": "easynews", "enabled": True, "credentials": {"username": "u", "password": "p"}}
        base = {"id": "aiostreams", "enabled": True, "credentials": {}}
        merge_services([], [base, en], record)  # roadie wrote both
        self.assertEqual([s["id"] for s in merge_services([base, en], [base], record)], ["aiostreams"])

    def test_groups_changed_by_hand_stay(self):
        from roadie.aiostreams.builder import fallback_groups, merge_groups
        record = {}
        wrote = fallback_groups({"languages": ["German"]})
        self.assertEqual(merge_groups(None, wrote, record), wrote)
        mine = copy.deepcopy(wrote)
        mine["groupings"][1]["condition"] = "(queryType == 'anime.series') and count(totalStreams) < 5"
        self.assertEqual(merge_groups(mine, fallback_groups({"languages": ["French"]}), record), mine)

    def test_untouched_groups_follow_roadie(self):
        from roadie.aiostreams.builder import fallback_groups, merge_groups
        record = {}
        wrote = merge_groups(None, fallback_groups({"languages": ["German"]}), record)
        new = fallback_groups({"languages": ["French"]})
        self.assertEqual(merge_groups(wrote, new, record), new)


class EasynewsTest(unittest.TestCase):
    """With an Easynews account roadie switches on AIOStreams' built-in Easynews Search."""

    def test_without_account_nothing(self):
        cfg = build()
        self.assertNotIn("easynews", [s["id"] for s in cfg["services"]])
        self.assertNotIn("roadieeasynews", [p["instanceId"] for p in cfg["presets"]])

    def test_service_and_search_addon(self):
        cfg = build(easynews={"username": "me", "password": "pw"})
        en = next(s for s in cfg["services"] if s["id"] == "easynews")
        self.assertEqual(en["credentials"], {"username": "me", "password": "pw"})
        self.assertTrue(en["enabled"])
        preset = next(p for p in cfg["presets"] if p["instanceId"] == "roadieeasynews")
        self.assertEqual(preset["type"], "easynews-search")
        self.assertEqual(preset["options"]["services"], ["easynews"])
        self.assertIn("roadieeasynews", WIZARD_PRESETS)

    def test_with_fallback_indexers_it_runs_in_the_first_group(self):
        indexers = copy.deepcopy(EXAMPLE["indexers"])
        indexers.append({**indexers[0], "name": "Spare", "fallback": True})
        cfg = build(indexers=indexers, easynews={"username": "me", "password": "pw"})
        self.assertIn("roadieeasynews", cfg["groups"]["groupings"][0]["addons"])

    def test_change_of_easynews_writes_streams_and_brings_it_back(self):
        from roadie.aiostreams.builder import presets_for, sections_for
        self.assertEqual(sections_for({"easynews"}), ["streams"])
        flags = presets_for({"easynews"})
        self.assertTrue(flags["write"])
        self.assertIn("roadieeasynews", flags["readd"])
