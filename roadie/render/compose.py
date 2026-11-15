"""docker compose project. Only Traefik publishes ports; no Docker socket anywhere."""

from roadie.render import HEADER, RenderConfig, RenderedFile
from roadie.render.yaml import dump

NETWORK = "roadie"
LOGGING = {"driver": "json-file", "options": {"max-size": "10m", "max-file": "3"}}
AIOSTREAMS_HEALTH = ("fetch('http://127.0.0.1:3000/api/v1/health')"
                     ".then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))")


def _service(cfg: RenderConfig, image: str, mem: str, health: dict, **extra) -> dict:
    svc = {"image": cfg.images[image], "restart": "always", "mem_limit": mem, "logging": LOGGING,
           "networks": [NETWORK], "healthcheck": health}
    svc.update(extra)
    return svc


def _health(test: list[str]) -> dict:
    return {"test": test, "interval": "30s", "timeout": "10s", "retries": 3, "start_period": "60s"}


def _aiostreams_mem(mem_mb: int) -> str:
    return "1536m" if mem_mb >= 3800 else "768m"


def compose_dict(cfg: RenderConfig) -> dict:
    ports = ["0.0.0.0:80:80", "0.0.0.0:443:443"]
    if cfg.ipv6:
        ports += ["[::]:80:80", "[::]:443:443"]
    env = {"TZ": cfg.tz}
    services = {
        "traefik": _service(
            cfg, "traefik", "256m", _health(["CMD", "traefik", "healthcheck", "--ping"]),
            ports=ports, environment=env,
            volumes=["./config/traefik:/etc/traefik:ro", "./data/traefik:/data"],
        ),
        "authelia": _service(
            cfg, "authelia", "256m", _health(["CMD", "/app/healthcheck.sh"]),
            environment=env, env_file=["env/authelia.env"],
            volumes=["./config/authelia:/config", "./secrets/authelia:/secrets:ro", "./data/authelia:/data"],
        ),
        "aiostreams": _service(
            cfg, "aiostreams", _aiostreams_mem(cfg.mem_mb),
            _health(["CMD", "/nodejs/bin/node", "-e", AIOSTREAMS_HEALTH]),
            environment=env, env_file=["env/aiostreams.env"], volumes=["./data/aiostreams:/app/data"],
        ),
        "prowlarr": _service(
            cfg, "prowlarr", "512m", _health(["CMD", "curl", "-fsS", "http://127.0.0.1:9696/ping"]),
            environment={**env, "PUID": "1000", "PGID": "1000", "UMASK": "002"},
            env_file=["env/prowlarr.env"], volumes=["./data/prowlarr:/config"],
        ),
    }
    return {"name": "roadie", "services": services, "networks": {NETWORK: {"name": NETWORK}}}


def render_compose(cfg: RenderConfig) -> list[RenderedFile]:
    return [RenderedFile("compose.yaml", dump(compose_dict(cfg), header=HEADER), 0o644)]
