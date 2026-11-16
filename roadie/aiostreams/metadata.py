"""Own AIOMetadata (catalogs and details from TMDB): the wizard creates one configuration
through its API, once. Afterwards it belongs to the user (settings page metadata.<domain>)."""

import json
import urllib.error
import urllib.request

from roadie.aiostreams.builder import TMDB_LANGUAGES

INSTANCE_ID = "roadiemetadata"  # the AIOStreams preset that points at it
# AIOStreams reaches it inside Docker; players never do (posters come from TMDB directly)
MANIFEST = "http://aiometadata:3232/stremio/{uuid}/manifest.json"
TIMEOUT_MS = 10000
# (catalog id, type, on the start page) - all TMDB, so the server's one TMDB key is enough
CATALOGS = [
    ("tmdb.trending", "movie", True), ("tmdb.trending", "series", True),
    ("tmdb.top", "movie", True), ("tmdb.top", "series", True),
    ("tmdb.airing_today", "series", False),
    ("tmdb.top_rated", "movie", False), ("tmdb.top_rated", "series", False),
    ("tmdb.year", "movie", False), ("tmdb.year", "series", False),
    ("tmdb.language", "movie", False), ("tmdb.language", "series", False),
]
NAMES = {"tmdb.trending": "Trending", "tmdb.top": "Popular", "tmdb.airing_today": "Airing Today",
         "tmdb.top_rated": "Top Rated", "tmdb.year": "By Year", "tmdb.language": "By Language"}
ART = {"poster": "meta", "background": "meta", "logo": "meta"}


def metadata_config(content: dict) -> dict:
    """Movies and series from TMDB (TVDB would need another key), in the first content
    language; no poster proxy, so players load posters from TMDB and not from this server."""
    return {
        "language": TMDB_LANGUAGES.get(content["languages"][0], "en-US"),
        "includeAdult": False,
        "castCount": 10,
        "providers": {"movie": "tmdb", "series": "tmdb", "anime": "mal", "anime_id_provider": "imdb",
                      "forceAnimeForDetectedImdb": False},
        "artProviders": {"movie": dict(ART), "series": dict(ART), "anime": dict(ART),
                         "englishArtOnly": False, "originalLangFallback": True},
        "tvdbSeasonType": "default",
        "apiKeys": {},  # the server's BUILT_IN_TMDB_API_KEY is used
        "posterRatingProvider": "none",
        "usePosterProxy": False,
        "searchEnabled": True,
        "search": {
            "enabled": True,
            "ai_enabled": False,
            "providers": {"movie": "tmdb.search", "series": "tmdb.search",
                          "anime_movie": "mal.search.movie", "anime_series": "mal.search.series",
                          "people_search_movie": "tmdb.people.search", "people_search_series": "tmdb.people.search"},
        },
        "catalogSetupComplete": True,
        "catalogs": [{"id": cid, "name": NAMES[cid], "type": kind, "source": "tmdb", "enabled": True,
                      "showInHome": home, "enableRatingPosters": False, "randomizePerPage": False}
                     for cid, kind, home in CATALOGS],
    }


def preset(uuid: str) -> dict:
    """The AIOStreams preset (type custom) for this configuration."""
    return {"type": "custom", "instanceId": INSTANCE_ID, "enabled": True,
            "options": {"name": "AIOMetadata", "manifestUrl": MANIFEST.format(uuid=uuid), "timeout": TIMEOUT_MS}}


def create(base_url: str, config: dict, password: str, addon_password: str, timeout: float = 60) -> str:
    """POST /api/config/save; returns the new configuration's UUID."""
    body = json.dumps({"config": config, "password": password, "addonPassword": addon_password}).encode()
    req = urllib.request.Request(f"{base_url.rstrip('/')}/api/config/save", data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            answer = json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        detail = e.read(2000).decode("utf-8", "replace")
        raise RuntimeError(f"AIOMetadata answered {e.code}: {detail}") from None
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise RuntimeError(f"AIOMetadata did not answer: {type(e).__name__}") from None
    uuid = answer.get("userUUID")
    if not isinstance(uuid, str) or not uuid:
        raise RuntimeError("AIOMetadata did not return a configuration id")
    return uuid
