"""Turn answers into an AIOStreams user configuration.

The base is the starter template served by the running AIOStreams instance itself
(GET /api/v1/templates), so defaults always match the installed version. AIOStreams
validates the result when it is saved through its API."""

import copy
import hashlib

TMDB_FEATURES = ("titleMatching", "yearMatching", "digitalReleaseFilter")
# Which config keys belong to which wizard section (for "change only what was edited").
SECTIONS = {
    "languages": ["preferredLanguages", "sortCriteria"],
    "quality": ["preferredResolutions", "preferredVisualTags", "excludedVisualTags"],
    # presets and catalogModifications are merged per entry (merge_presets, merge_catalog_mods)
    "streams": ["services", "failover"],
    "metadata": ["tmdbApiKey", "tmdbAccessToken", *TMDB_FEATURES],
    "ranking": ["rankedStreamExpressions", "rankedRegexPatterns", "precacheNextEpisode"],
}
# Release age in hours. Newer releases are complete far more often than very old ones.
RANKED_EXPRESSIONS = [
    {"expression": "age(streams, 0, 720)", "score": 8, "enabled": True},      # < 30 days
    {"expression": "age(streams, 35000)", "score": -8, "enabled": True},      # > 4 years
    {"expression": "library(streams)", "score": 12, "enabled": True},         # worked before
]
# Fields a person may override in a per-person config (computed from the "content" answer only).
PERSON_FIELDS = ("preferredLanguages", "preferredResolutions", "preferredVisualTags", "excludedVisualTags")
# At most 5 streams per resolution and 20 in all; the ranking decides which stay. Set only when a
# config has no limits yet: users fine-tune them on the website.
RESULT_LIMITS = {"resolution": 5, "global": 20}
# Presets the wizard owns (by instanceId); every other preset belongs to the user and stays.
WIZARD_PRESETS = ("roadieprowlarr", "roadieprowlarrfallback", "roadietmdb", "roadiestreaming", "roadiecollections",
                  "roadiemetadata")
# Main and fallback indexers: Prowlarr tags pick them, so an indexer the user adds in Prowlarr
# joins a side by getting one of the tags. The fallback side is only asked when the main side
# found fewer than FALLBACK_BELOW streams (in the chosen languages other than English).
MAIN_TAG, FALLBACK_TAG = "roadie-main", "roadie-fallback"
FALLBACK_PRESET = "roadieprowlarrfallback"
FALLBACK_BELOW = 5
PRESET_TIMEOUT = 7000  # AIOStreams' default for the catalog presets (v2.37)
STREAMING_SERVICES = ["nfx", "amp", "dnp"]  # Netflix, Prime Video, Disney+
# Catalog language per content language (values from the presets of AIOStreams v2.37). The
# collections preset takes the first two letters; it knows no Chinese.
TMDB_LANGUAGES = {
    "English": "en-US", "Japanese": "ja-JP", "Chinese": "zh-CN", "Russian": "ru-RU", "Arabic": "ar-SA",
    "Portuguese": "pt-PT", "Portuguese (Brazil)": "pt-BR", "Spanish": "es-ES", "French": "fr-FR",
    "German": "de-DE", "Italian": "it-IT", "Korean": "ko-KR", "Hindi": "hi-IN", "Bengali": "bn-BD",
    "Marathi": "mr-MR", "Gujarati": "gu-GU", "Tamil": "ta-IN", "Telugu": "te-IN", "Kannada": "kn-IN",
    "Malayalam": "ml-IN", "Thai": "th-TH", "Vietnamese": "vi-VN", "Indonesian": "id-ID", "Turkish": "tr-TR",
    "Hebrew": "he-IL", "Persian": "fa-IR", "Ukrainian": "uk-UA", "Greek": "el-GR", "Lithuanian": "lt-LT",
    "Latvian": "lv-LV", "Estonian": "et-EE", "Polish": "pl-PL", "Czech": "cs-CZ", "Slovak": "sk-SK",
    "Hungarian": "hu-HU", "Romanian": "ro-RO", "Bulgarian": "bg-BG", "Serbian": "sr-RS", "Croatian": "hr-HR",
    "Slovenian": "sl-SI", "Dutch": "nl-NL", "Danish": "da-DK", "Finnish": "fi-FI", "Swedish": "sv-SE",
    "Norwegian": "no-NO", "Malay": "ms-MY",
}
# AIOStreams names a preset's addon instanceId + 4 hex chars of sha256(identifier); these
# presets have no identifier. Catalog ids are "<addon>.<catalog id>".
_ADDON_SUFFIX = hashlib.sha256(b"").hexdigest()[:4]
# Start page rows, in this order (instanceId, catalog id, type)...
HOME_CATALOGS = [("roadietmdb", "tmdb.trending", "movie"), ("roadietmdb", "tmdb.trending", "series"),
                 *[("roadiestreaming", s, kind) for s in STREAMING_SERVICES for kind in ("movie", "series")],
                 ("roadiecollections", "ctmdb.popular", "collections")]
# ...and the wizard's other rows only under Discover. Search catalogs stay as they are.
DISCOVER_CATALOGS = [*[("roadietmdb", f"tmdb.{c}", kind) for c in ("top", "year", "language") for kind in ("movie", "series")],
                     ("roadiestreaming", "netflix-top10-global", "movie"), ("roadiestreaming", "netflix-top10-global", "series"),
                     ("roadiecollections", "ctmdb.topRated", "collections"), ("roadiecollections", "ctmdb.newReleases", "collections")]
HDR_TAGS = ["HDR+DV", "DV", "HDR10+", "HDR10", "HDR"]
RANKED_REGEX = [{"pattern": r"\b(PROPER|REPACK)\b", "name": "Fix release", "score": 5}]


def _sort_language_first(criteria: list[dict]) -> list[dict]:
    """Language right after 'cached'/'library': a release in the wanted language beats
    a higher resolution in another one."""
    rest = [c for c in criteria if c.get("key") != "language"]
    lead = [c for c in rest if c.get("key") in ("cached", "library")]
    tail = [c for c in rest if c.get("key") not in ("cached", "library")]
    return lead + [{"key": "language", "direction": "desc"}] + tail


def forced_subtitles(content: dict) -> dict:
    """Releases with forced subtitles in a chosen language rank higher: the player shows them
    when a scene is in another language. Works where AIOStreams knows the release's tracks."""
    langs = "|".join(content["languages"])
    return {"expression": f"subtitleTrack(streams, 'forced', 'lang={langs}')", "score": 5, "enabled": True}


def has_fallback(indexers: list[dict]) -> bool:
    """True when the indexers are split into main and fallback ones."""
    roles = {bool(ix.get("fallback")) for ix in indexers}
    return roles == {True, False}


def _prowlarr_preset(iid: str, name: str, tag: str | None) -> dict:
    options = {"name": name, "timeout": 10000, "services": ["aiostreams"]}
    if tag:
        options["tags"] = tag
    return {"type": "prowlarr", "instanceId": iid, "enabled": True, "options": options}


def fallback_groups(content: dict) -> dict:
    """Ask the main indexers first; the fallback ones only when that found too little."""
    langs = [lang for lang in content["languages"] if lang != "English"]
    found = f"language(totalStreams, {', '.join(repr(lang) for lang in langs)})" if langs else "totalStreams"
    return {"enabled": True, "behaviour": "sequential", "onConditionFailure": "stop",
            "groupings": [{"addons": ["roadieprowlarr"], "condition": "true"},
                          {"addons": [FALLBACK_PRESET], "condition": f"count({found}) < {FALLBACK_BELOW}"}]}


def merge_groups(current: dict | None, new: dict | None) -> dict | None:
    """The wizard's groups replace the current ones; without a split, groups the wizard made
    earlier go and the user's own stay."""
    if new:
        return copy.deepcopy(new)
    ours = any(FALLBACK_PRESET in g.get("addons", []) for g in (current or {}).get("groupings") or [])
    return None if ours else copy.deepcopy(current)


def content_fields(content: dict) -> dict:
    """The PERSON_FIELDS for one 'content' answer; depends on nothing else."""
    resolutions = ["2160p", "1080p", "720p"]
    if not content["prefer_4k"]:
        resolutions = ["1080p", "2160p", "720p"]
    hdr = bool(content.get("hdr"))
    return {
        "preferredLanguages": list(content["languages"]) + (["Original"] if content["original_audio"] else []),
        "preferredResolutions": resolutions,
        "preferredVisualTags": list(HDR_TAGS) if hdr else [],
        # DV-only files show wrong colours on screens without Dolby Vision
        "excludedVisualTags": [] if hdr else ["DV Only"],
    }


def catalog_presets(content: dict, tmdb_key: str | None) -> list[dict]:
    """The wizard's catalog addons, in the language of the first content language.
    `tmdb_key` is unused: none of these presets needs one (checked live on v2.37)."""
    tmdb = TMDB_LANGUAGES.get(content["languages"][0], "en-US")
    collections = "en" if tmdb.startswith("zh") else tmdb[:2]

    def preset(kind, iid, name, **options):
        return {"type": kind, "instanceId": iid, "enabled": True,
                "options": {"name": name, "timeout": PRESET_TIMEOUT, **options}}
    return [preset("tmdb-addon", "roadietmdb", "The Movie Database", language=tmdb),
            preset("streaming-catalogs", "roadiestreaming", "Streaming Catalogs", catalogs=list(STREAMING_SERVICES)),
            preset("tmdb-collections", "roadiecollections", "TMDB Collections", language=collections)]


def catalog_mods(with_tmdb_addon: bool = True) -> list[dict]:
    """Rows of the wizard's catalog addons. With an own AIOMetadata the simple TMDB addon is
    gone; AIOMetadata's own settings say which of its rows go on the start page."""
    def mod(iid, cid, kind, **extra):
        return {"id": f"{iid}{_ADDON_SUFFIX}.{cid}", "type": kind, "enabled": True, **extra}
    keep = lambda c: with_tmdb_addon or c[0] != "roadietmdb"  # noqa: E731
    return ([mod(*c) for c in HOME_CATALOGS if keep(c)]
            + [mod(*c, onlyOnDiscover=True) for c in DISCOVER_CATALOGS if keep(c)])


def _owned(preset: dict) -> bool:
    iid = preset.get("instanceId", "")
    return iid in WIZARD_PRESETS


# The simple TMDB addon and the own AIOMetadata are one place in the addon list: one replaces the other.
TMDB_SLOT = ("roadietmdb", "roadiemetadata")


def tmdb_slot(presets: list[dict]) -> str | None:
    """Which of TMDB_SLOT a preset list holds (None: neither)."""
    return next((p.get("instanceId") for p in presets if p.get("instanceId") in TMDB_SLOT), None)


def merge_presets(current: list[dict], new: list[dict], readd: set[str] = frozenset()) -> list[dict]:
    """Update the wizard's presets that are still in `current`, in place and with the user's
    `enabled`; a wizard preset not in `new` goes (the TMDB_SLOT presets replace each other at the
    same place). Presets the user added stay as they are. A wizard preset missing from `current`
    (deleted on the website) only comes back, at the front, when its instanceId is in `readd`."""
    wanted = {p["instanceId"]: p for p in new}
    have = {p.get("instanceId") for p in current}
    if have & set(TMDB_SLOT):
        have |= set(TMDB_SLOT)
    merged = [p for p in new if p["instanceId"] in readd and p["instanceId"] not in have]
    for p in current:
        iid = p.get("instanceId")
        if not _owned(p):
            merged.append(p)
            continue
        if iid not in wanted and iid in TMDB_SLOT:
            iid = next(i for i in TMDB_SLOT if i != iid)
        if iid in wanted:
            merged.append({**wanted[iid], **({"enabled": p["enabled"]} if "enabled" in p else {})})
    return copy.deepcopy(merged)


def row_addon(row: dict) -> str:
    """The instanceId of the addon a catalog row belongs to (row id "<instanceId><4 hex>.<catalog>")."""
    return row["id"].split(".", 1)[0][:-len(_ADDON_SUFFIX)]


def merge_catalog_mods(current: list[dict], new: list[dict]) -> list[dict]:
    """Keep every existing row setting (the user's word wins); add the wizard's missing ones."""
    have = {(m.get("id"), m.get("type")) for m in current}
    return copy.deepcopy(list(current) + [m for m in new if (m["id"], m["type"]) not in have])


def build_user_config(answers, template: dict, playback_auth: str, metadata_preset: dict | None = None) -> dict:
    """metadata_preset: the own AIOMetadata (replaces the simple TMDB catalog addon)."""
    d = answers.data
    cfg = copy.deepcopy(template)
    content = d["content"]

    cfg.update(content_fields(content))
    sort = cfg.setdefault("sortCriteria", {"global": []})
    sort["global"] = _sort_language_first(sort.get("global", []))

    services = [{"id": "aiostreams", "enabled": True, "credentials": {"aiostreamsAuth": playback_auth}}]
    # URL and API key come from the instance (BUILTIN_PROWLARR_* env), not from this config
    split = has_fallback(d["indexers"])
    presets = [_prowlarr_preset("roadieprowlarr", "Prowlarr", MAIN_TAG if split else None),
               *([_prowlarr_preset(FALLBACK_PRESET, "Prowlarr (fallback indexers)", FALLBACK_TAG)] if split else []),
               *catalog_presets(content, d.get("tmdb_api_key"))]
    if metadata_preset:
        presets = [metadata_preset if p["instanceId"] == "roadietmdb" else p for p in presets]
    cfg["services"], cfg["presets"] = services, presets
    cfg["catalogModifications"] = catalog_mods(with_tmdb_addon=metadata_preset is None)
    cfg["resultLimits"] = dict(RESULT_LIMITS)
    cfg["failover"] = {"enabled": True, "contentTypes": ["usenet"]}
    cfg.pop("groups", None)
    if split:
        cfg["groups"] = fallback_groups(content)

    cfg.pop("tmdbApiKey", None)
    cfg.pop("tmdbAccessToken", None)
    key = d.get("tmdb_api_key")
    if key:
        cfg["tmdbAccessToken" if len(key) > 40 else "tmdbApiKey"] = key
    else:  # AIOStreams refuses these features without a TMDB key
        for feature in TMDB_FEATURES:
            cfg[feature] = {**cfg.get(feature, {}), "enabled": False}

    cfg["rankedStreamExpressions"] = copy.deepcopy(RANKED_EXPRESSIONS) + [forced_subtitles(content)]
    cfg["rankedRegexPatterns"] = copy.deepcopy(RANKED_REGEX)
    cfg["precacheNextEpisode"] = True
    return cfg


# Which config sections each answer part feeds ("change settings" writes only these).
_ANSWER_SECTIONS = {"content": ["languages", "quality", "ranking"], "indexers": ["streams"],
                    "tmdb_api_key": ["metadata"]}
CATALOG_PRESETS = {"roadietmdb", "roadiestreaming", "roadiecollections", "roadiemetadata"}


def sections_for(changed: set[str]) -> list[str]:
    """The SECTIONS to write for the changed answer parts ("*" = all), in SECTIONS order."""
    if "*" in changed:
        return list(SECTIONS)
    wanted = {s for part in changed for s in _ANSWER_SECTIONS.get(part, [])}
    return [s for s in SECTIONS if s in wanted]


def presets_for(changed: set[str]) -> dict:
    """Whether presets, catalog rows and groups are written, and which deleted wizard presets
    come back: Prowlarr when the indexers changed (no streams without it), the catalog addons
    when the catalogs or the TMDB key changed."""
    every = "*" in changed
    readd = set()
    if every or "indexers" in changed:
        readd |= {"roadieprowlarr", FALLBACK_PRESET}
    if every or changed & {"tmdb_api_key", "catalogs"}:
        readd |= CATALOG_PRESETS
    return {"write": every or bool(changed & {"indexers", "tmdb_api_key", "content", "catalogs"}), "readd": readd}


def merge_sections(current: dict, new: dict, sections: list[str]) -> dict:
    """Copy only the keys of the edited sections from new into current."""
    merged = copy.deepcopy(current)
    for section in sections:
        if section not in SECTIONS:
            raise ValueError(f"unknown section: {section}")
        for key in SECTIONS[section]:
            if key in new:
                merged[key] = copy.deepcopy(new[key])
            else:
                merged.pop(key, None)
    return merged
