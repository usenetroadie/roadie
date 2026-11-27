"""Authelia: login with password + second factor or passkey in front of every admin page.

Players (Stremio/Nuvio) cannot log in, so only the paths they need are bypassed, and only
for GET/HEAD/OPTIONS (exception: the Jellyfin API, which checks every login itself). Paths derived from AIOStreams v2.38.1 packages/server/src/app.ts.
"""

from pathlib import Path

from roadie.render import HEADER, RenderConfig, RenderedFile
from roadie.render.yaml import dump

# checked first: AIOStreams' configuration pages stay behind the login
PROTECTED_PATHS = ["^/stremio/configure", "^/stremio/[^/]+/[^/]+/configure", "^/stremio/[^/]+/[^/]+/v/[^/]+/configure"]
# AIOStreams' Jellyfin API (apps log in with POST and open websockets, so no method list):
# every route there checks the login itself (401 without it).
JELLYFIN_PATHS = ["^/jellyfin([/?]|$)"]
# AIOMetadata's images: with RPDB and its poster proxy, or with its image cache, the poster
# addresses point at the metadata host, and players load them without a login.
# Paths from AIOMetadata 3.9.0 addon/index.ts; its settings pages stay behind the login.
METADATA_IMAGE_PATHS = ["^/(poster|logo|background)/", "^/poster-cache/", "^/api/image/(blur|banner-to-background)"]
# AIOMetadata's player routes (apps cannot log in, so an own configuration installed straight in
# an app works): manifest, catalog, meta, stream, subtitles, watch state, rating. Go regexp has no
# lookahead, so path segments must not start with a dot (no "..", no "configure" below them).
# The query tail may not contain "/": Authelia sees the decoded path, so "x%2Fmanifest.json%3F/configure"
# would look like a player route with a query while AIOMetadata routes the raw path to configure.
# Catalog and subtitle extras take several segments, so a search with an encoded "/" still works.
_SEG = r"[^/?.][^/?]*"  # file names and values starting with a dot fail closed on purpose
_QUERY = r"([?][^/]*)?$"
METADATA_PLAYER_PATHS = [
    r"^/stremio/" + _SEG + r"/(manifest\.json|(catalog|subtitles)/" + _SEG + "/" + _SEG + "(/" + _SEG
    + r")*\.json|(meta|stream)/" + _SEG + "/" + _SEG + "(/" + _SEG + r")?\.json|watch_state/pull\.json|rating)"
    + _QUERY]
# AIOStreams' player routes, same rules as above: addon (/stremio/<uuid>/<password>, optional
# /v/<variant>; /stremio/u/<alias> only redirects there), then the playback addresses its streams
# point at. Usenet streams play through /api/v1/usenet/stream/ and /api/v1/debrid/playback/ (route
# names in AIOStreams). File names and catalog searches may hold an encoded "/", which Authelia
# decodes into extra segments, so those parts take more segments (the app routes the raw path).
# A proxy stream without a file name ends in "/".
# /static/ holds AIOStreams' short error videos (e.g. downloading.mp4) that playback redirects to.
AIOSTREAMS_PLAYER_PATHS = [
    r"^/stremio/manifest\.json" + _QUERY,
    r"^/stremio/" + _SEG + "/" + _SEG + "(/v/" + _SEG + r")?/(manifest\.json|(catalog|subtitles)/" + _SEG
    + "/" + _SEG + "(/" + _SEG + r")*\.json|(meta|stream|addon_catalog)/" + _SEG + "/" + _SEG + r"\.json)" + _QUERY,
    r"^/api/v1/usenet/stream/" + _SEG + "(/" + _SEG + ")*" + _QUERY,
    r"^/api/v1/debrid/playback(/" + _SEG + ")+" + _QUERY,
    r"^/api/v1/proxy/[^/?.][^/?]{31,}(/" + _SEG + ")*/?" + _QUERY,  # encrypted ids are long, "generate" is not
    r"^/api/v1/posters/" + _SEG + _QUERY,
    r"^/static/[A-Za-z0-9_-]+\.mp4$",
]
SECRET_FILES = {
    "jwt_secret": ("authelia_jwt_secret", "AUTHELIA_IDENTITY_VALIDATION_RESET_PASSWORD_JWT_SECRET_FILE"),
    "session_secret": ("authelia_session_secret", "AUTHELIA_SESSION_SECRET_FILE"),
    "storage_encryption_key": ("authelia_storage_key", "AUTHELIA_STORAGE_ENCRYPTION_KEY_FILE"),
}


def config_dict(cfg: RenderConfig, own_rules=()) -> dict:
    aio = cfg.host("aiostreams")
    protected = [aio, cfg.host("prowlarr"), cfg.host("status")] + ([cfg.host("metadata")] if cfg.metadata else [])
    images = ([{"domain": [cfg.host("metadata")], "methods": ["GET", "HEAD", "OPTIONS"],
                "resources": METADATA_IMAGE_PATHS + METADATA_PLAYER_PATHS, "policy": "bypass"}] if cfg.metadata else [])
    return {
        "theme": "auto",
        "server": {
            "address": "tcp://:9091/",
            # cookie only: AIOStreams' page sends "Authorization: Basic uuid:password", which
            # Authelia would otherwise treat as a login and answer with a browser popup
            "endpoints": {"authz": {"forward-auth": {"implementation": "ForwardAuth",
                                                     "authn_strategies": [{"name": "CookieSession"}]}}},
        },
        "log": {"level": "info"},
        "totp": {"issuer": cfg.domain},
        "webauthn": {
            "disable": False,
            "display_name": "roadie",
            "enable_passkey_login": True,
            "experimental_enable_passkey_uv_two_factors": True,
            "selection_criteria": {"discoverability": "required", "user_verification": "required"},
        },
        "authentication_backend": {"file": {"path": "/config/users.yml"}},
        "access_control": {
            "default_policy": "deny",
            "rules": [
                {"domain": [aio], "resources": PROTECTED_PATHS, "policy": "two_factor"},
                {"domain": [aio], "methods": ["GET", "HEAD", "OPTIONS"], "resources": AIOSTREAMS_PLAYER_PATHS,
                 "policy": "bypass"},
                {"domain": [aio], "resources": JELLYFIN_PATHS, "policy": "bypass"},
                *images,
                *own_rules,
                {"domain": protected, "policy": "two_factor"},
            ],
        },
        "session": {
            "inactivity": "1h", "expiration": "12h", "remember_me": "1M",
            "cookies": [{"domain": cfg.domain, "authelia_url": f"https://{cfg.host('auth')}",
                         "default_redirection_url": f"https://{aio}"}],
        },
        "regulation": {"max_retries": 5, "find_time": "10m", "ban_time": "1h"},
        "storage": {"local": {"path": "/data/db.sqlite3"}},
        "notifier": {"filesystem": {"filename": "/data/notification.txt"}},
    }


POLICIES = {"bypass", "one_factor", "two_factor", "deny"}


def read_own_rules(path: Path) -> tuple[list[dict], str | None]:
    """The user's own access rules (config/authelia/own-rules.yml), put before the catch-all.
    Returns (rules, None); a missing file is fine; a broken one gives ([], reason)."""
    if not path.is_file():
        return [], None
    try:
        import yaml  # python3-yaml; imported here because roadie itself runs without it
    except ImportError:
        return [], "python3-yaml missing"
    try:
        rules = yaml.safe_load(path.read_text())
    except (yaml.YAMLError, OSError, UnicodeError) as e:
        return [], f"not valid YAML ({type(e).__name__})"
    if not isinstance(rules, list):
        return [], "not a list of rules (each rule starts with '- ')"
    for i, rule in enumerate(rules, 1):
        domain = rule.get("domain") if isinstance(rule, dict) else None
        if not (isinstance(rule, dict) and rule.get("policy") in POLICIES
                and ((isinstance(domain, str) and domain.strip())
                     or (isinstance(domain, list) and domain
                         and all(isinstance(d, str) and d.strip() for d in domain)))):
            return [], f"rule {i} needs a non-empty 'domain' (text or list of text) and a 'policy' (bypass, one_factor, two_factor or deny)"
    return rules, None


def users_dict(cfg: RenderConfig, password_hash: str) -> dict:
    name = cfg.auth_username
    return {"users": {name: {"displayname": name, "password": password_hash,
                             "email": f"{name}@roadie.invalid", "groups": ["admins"]}}}


def render_authelia(cfg: RenderConfig, secrets: dict, password_hash: str, own_rules=()) -> list[RenderedFile]:
    if not password_hash.startswith("$argon2id$"):
        raise ValueError("expected an argon2id hash")
    files = [
        RenderedFile("config/authelia/configuration.yml", dump(config_dict(cfg, own_rules), header=HEADER), 0o644),
        RenderedFile("config/authelia/users.yml", dump(users_dict(cfg, password_hash), header=HEADER), 0o600),
    ]
    env_lines = []
    for filename, (secret_name, env_var) in SECRET_FILES.items():
        files.append(RenderedFile(f"secrets/authelia/{filename}", secrets[secret_name], 0o600))
        env_lines.append(f"{env_var}=/secrets/{filename}")
    files.append(RenderedFile("env/authelia.env", "\n".join(env_lines) + "\n", 0o600))
    return files
