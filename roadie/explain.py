"""`sudo roadie explain` (also `help`): the help pages for everyday questions, then what is
installed on this server, how it fits together, where everything lives and how to look after
each part yourself.

Written for beginners, but with the real names, paths, ports and commands of this server,
so reading it is a first step to running it without roadie. A line starting with "$ " is a
command: printed on its own line, ready to copy."""

from roadie import help as helppages
from roadie.i18n import t
from roadie.ops.common import Env
from roadie.ui import Prompter

# the help pages first (what a beginner needs most), then how the server works
TOPICS = ["next_steps", "trouble", "terms", "backup_download", "do_it_yourself", "more_catalogs",
          "overview", "services", "network", "files", "jobs", "docker", "security"]
# known services in the order of a request; the sub-domain of their web page (None: no page)
SERVICES = {"traefik": None, "authelia": "auth", "aiostreams": "aiostreams", "prowlarr": "prowlarr",
            "aiometadata": "metadata", "aiometadata-redis": None, "status": "status"}


def params(env: Env) -> dict:
    from roadie.ops import backup
    ports = env.answers.get("_ssh_ports") or [22]
    return {"domain": env.domain, "user": env.answers.get("admin_user") or "ubuntu",
            "ssh_ports": ", ".join(str(p) for p in ports), "stack": str(env.paths.stack_dir),
            "backup_dir": str(backup.backup_dir(env)),
            "reboot": (env.answers.get("maintenance") or {}).get("reboot_time") or "05:00"}


def _services(env: Env, p: dict) -> list[tuple[str, list[str]]]:
    running = env.stack_services() or [s for s in SERVICES if s not in ("aiometadata", "aiometadata-redis")]
    boxes = [(t("explain.services.title"), helppages.lines(t("explain.services.body", **p)))]
    for name in sorted(running, key=lambda s: list(SERVICES).index(s) if s in SERVICES else len(SERVICES)):
        if name not in SERVICES:  # added by the user to compose.yaml
            boxes.append((name, [t("explain.svc.unknown")]))
            continue
        lines = [t(f"explain.svc.{name}.what", **p), t("explain.svc.why") + " " + t(f"explain.svc.{name}.why", **p)]
        if SERVICES[name]:
            lines += [t("explain.svc.address"), "$ https://" + f"{SERVICES[name]}.{p['domain']}"]
        lines += [t(f"explain.svc.{name}.files", **p), t("explain.svc.logs"),
                  f"$ cd {p['stack']} && sudo docker compose logs --tail 50 {name}"]
        boxes.append((t(f"explain.svc.{name}.name"), helppages.lines("\n".join(lines))))
    return boxes


def title(topic: str) -> str:
    return t(f"help.{topic}.title") if topic in helppages.TOPICS else t(f"explain.{topic}.title")


def sections(env: Env, topic: str) -> list[tuple[str, list[str]]]:
    if topic in helppages.TOPICS:
        return [(title(topic), helppages.lines(helppages.body(env, topic, **helppages.params(env))))]
    p = params(env)
    if topic == "services":
        return _services(env, p)
    if topic == "jobs":  # the DuckDNS updater only runs with a DuckDNS address
        p["duckdns"] = "" if env.answers.get("domain") else "\n" + t("explain.jobs.duckdns")
    body = t(f"explain.{topic}.body", **p)
    boxes = [(t(f"explain.{topic}.title"), helppages.lines(body))]
    why = f"explain.{topic}.why"
    if t(why) != why:  # the reasons, in their own box below the facts
        boxes.append((t("explain.why"), helppages.lines(t(why, **p))))
    return boxes


def show(env: Env, ui: Prompter, topic: str) -> None:
    for title, lines in sections(env, topic):
        ui.box(title, lines)


def run(env: Env, ui: Prompter, topic: str | None = None) -> int:
    """No topic: pick one on a terminal; everything at once when the output goes to a file."""
    interactive = ui.tty and hasattr(ui.inp, "isatty") and ui.inp.isatty()
    if topic is None and not interactive:
        topic = "all"
    if topic == "all":
        for name in TOPICS:
            show(env, ui, name)
        return 0
    if topic is None:
        options = [(name, title(name)) for name in TOPICS]
        options += [("all", t("explain.all")), ("back", t("explain.back"))]
        topic = ui.choose(t("explain.pick"), options)
        if topic == "back":
            return 0
        return run(env, ui, topic)
    show(env, ui, topic)
    ui.hint(t("explain.more"))
    return 0
