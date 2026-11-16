"""Traefik static config + file-provider routes (no Docker socket, no dashboard)."""

from roadie.render import HEADER, RenderConfig, RenderedFile
from roadie.render.yaml import dump

RESOLVER = "letsencrypt"
# AIOStreams puts encoded data (file names, config) into URL paths; Traefik >= 3.6 rejects
# these by default. The null character stays blocked. Not narrowed (security audit L10): release
# file names in playback URLs can hold any of these characters, and the bypass is only for
# GET/HEAD/OPTIONS; tests/e2e/verify.sh checks that encoded paths still need the login.
ENCODED = {f"allowEncoded{x}": True for x in
           ("Slash", "BackSlash", "Semicolon", "Percent", "QuestionMark", "Hash")}


def static_dict(cfg: RenderConfig) -> dict:
    static = _static_dict(cfg)
    if cfg.test_mode:
        del static["certificatesResolvers"]
    return static


def _static_dict(cfg: RenderConfig) -> dict:
    return {
        "global": {"checkNewVersion": False, "sendAnonymousUsage": False},
        "log": {"level": "WARN"},
        "accessLog": {"filePath": "/data/access.log"},
        "ping": {"entryPoint": "ping"},
        "entryPoints": {
            "web": {"address": ":80", "http": {"redirections": {"entryPoint": {"to": "websecure", "scheme": "https"}}}},
            "websecure": {"address": ":443", "http": {
                "middlewares": ["security-headers@file"], "encodedCharacters": ENCODED}},
            "ping": {"address": "127.0.0.1:8082"},
        },
        "providers": {"file": {"filename": "/etc/traefik/routes.yml", "watch": True}},
        "certificatesResolvers": {RESOLVER: {"acme": {"storage": "/data/acme.json", "tlsChallenge": {}}}},
    }


def _router(cfg: RenderConfig, sub: str, service: str, auth: bool = True) -> dict:
    r = {"rule": f"Host(`{cfg.host(sub)}`)", "entryPoints": ["websecure"], "service": service,
         "tls": {} if cfg.test_mode else {"certResolver": RESOLVER}}
    if auth:
        r["middlewares"] = ["authelia"]
    return r


def dynamic_dict(cfg: RenderConfig) -> dict:
    routers = {
        "aiostreams": _router(cfg, "aiostreams", "aiostreams"),
        "authelia": _router(cfg, "auth", "authelia", auth=False),
        "prowlarr": _router(cfg, "prowlarr", "prowlarr"),
        "status": _router(cfg, "status", "status"),
    }
    services = {
        "aiostreams": {"loadBalancer": {"servers": [{"url": "http://aiostreams:3000"}]}},
        "authelia": {"loadBalancer": {"servers": [{"url": "http://authelia:9091"}]}},
        "prowlarr": {"loadBalancer": {"servers": [{"url": "http://prowlarr:9696"}]}},
        "status": {"loadBalancer": {"servers": [{"url": "http://status:8080"}]}},
    }
    if cfg.metadata:  # AIOMetadata's settings page; players never need it (posters come from TMDB)
        routers["metadata"] = _router(cfg, "metadata", "metadata")
        services["metadata"] = {"loadBalancer": {"servers": [{"url": "http://aiometadata:3232"}]}}
    middlewares = {
        "authelia": {"forwardAuth": {
            "address": "http://authelia:9091/api/authz/forward-auth",
            "trustForwardHeader": True,
            "authResponseHeaders": ["Remote-User", "Remote-Groups", "Remote-Email", "Remote-Name"],
        }},
        "security-headers": {"headers": {
            "stsSeconds": 31536000, "stsIncludeSubdomains": True, "contentTypeNosniff": True,
            "customFrameOptionsValue": "SAMEORIGIN", "referrerPolicy": "strict-origin-when-cross-origin",
        }},
    }
    return {"http": {"routers": routers, "middlewares": middlewares, "services": services}}


def render_traefik(cfg: RenderConfig) -> list[RenderedFile]:
    return [
        RenderedFile("config/traefik/traefik.yml", dump(static_dict(cfg), header=HEADER)),
        RenderedFile("config/traefik/routes.yml", dump(dynamic_dict(cfg), header=HEADER)),
    ]
