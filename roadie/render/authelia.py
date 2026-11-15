"""Authelia: login with password + second factor or passkey in front of every admin page.

Players (Stremio/Nuvio) cannot log in, so only the paths they need are bypassed, and only
for GET/HEAD/OPTIONS. Paths derived from AIOStreams v2.37.0 packages/server/src/app.ts.
"""

from roadie.render import HEADER, RenderConfig, RenderedFile
from roadie.render.yaml import dump

# checked first: AIOStreams' configuration pages stay behind the login
PROTECTED_PATHS = ["^/stremio/configure", "^/stremio/[^/]+/[^/]+/configure"]
# manifest, catalog, meta, stream, subtitles + playback endpoints used by players. AIOStreams
# also plays its built-in Usenet streams through /api/v1/debrid/playback/ (route name in AIOStreams).
BYPASS_PATHS = ["^/stremio/", "^/api/v1/(proxy|debrid|usenet|posters)/"]
SECRET_FILES = {
    "jwt_secret": ("authelia_jwt_secret", "AUTHELIA_IDENTITY_VALIDATION_RESET_PASSWORD_JWT_SECRET_FILE"),
    "session_secret": ("authelia_session_secret", "AUTHELIA_SESSION_SECRET_FILE"),
    "storage_encryption_key": ("authelia_storage_key", "AUTHELIA_STORAGE_ENCRYPTION_KEY_FILE"),
}


def config_dict(cfg: RenderConfig) -> dict:
    aio = cfg.host("aiostreams")
    protected = [aio, cfg.host("prowlarr")]
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
                {"domain": [aio], "methods": ["GET", "HEAD", "OPTIONS"], "resources": BYPASS_PATHS,
                 "policy": "bypass"},
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


def users_dict(cfg: RenderConfig, password_hash: str) -> dict:
    name = cfg.auth_username
    return {"users": {name: {"displayname": name, "password": password_hash,
                             "email": f"{name}@roadie.invalid", "groups": ["admins"]}}}


def render_authelia(cfg: RenderConfig, secrets: dict, password_hash: str) -> list[RenderedFile]:
    if not password_hash.startswith("$argon2id$"):
        raise ValueError("expected an argon2id hash")
    files = [
        RenderedFile("config/authelia/configuration.yml", dump(config_dict(cfg), header=HEADER), 0o644),
        RenderedFile("config/authelia/users.yml", dump(users_dict(cfg, password_hash), header=HEADER), 0o600),
    ]
    env_lines = []
    for filename, (secret_name, env_var) in SECRET_FILES.items():
        files.append(RenderedFile(f"secrets/authelia/{filename}", secrets[secret_name], 0o600))
        env_lines.append(f"{env_var}=/secrets/{filename}")
    files.append(RenderedFile("env/authelia.env", "\n".join(env_lines) + "\n", 0o600))
    return files
