"""'My access': every address and secret a beginner needs again after the setup."""

from roadie.aiostreams.api import AioClient
from roadie.aiostreams.people import MAIN, people_of, secret_names
from roadie.i18n import t
from roadie.ops import backup as backup_ops
from roadie.ops.common import Env
from roadie.paths import atomic_write
from roadie.ui import Prompter

COPY = "\0"  # marks a line the renderer prints with Prompter.copyable


def _pair(label: str, value: str | None) -> list[str]:
    """Label line + copyable value; nothing at all for an empty value."""
    return [label, COPY + value] if value else []


def _domain(env: Env) -> str | None:
    from roadie.answers import base_domain
    return base_domain(env.answers)


def ssh_command(user: str, host: str, key_created: bool = False) -> str:
    """The login command; with the key file the wizard told the user to save when it made the key."""
    return f"ssh {'-i ~/.ssh/roadie ' if key_created else ''}{user}@{host}"


def addon_url(env: Env, person: dict) -> str | None:
    """A person's addon address; None while their config does not exist yet."""
    uuid_name, _, enc_name = secret_names(person["id"])
    uuid, enc, domain = env.secrets.get(uuid_name), env.secrets.get(enc_name), _domain(env)
    return AioClient.addon_url(f"https://aiostreams.{domain}", uuid, enc) if uuid and enc and domain else None


def write_url_files(env: Env) -> None:
    """addon-url.txt (main config, read by the watcher) and addon-urls.txt (name<TAB>URL per
    person who has a config). Rewritten after every change to the people."""
    urls = [(p, addon_url(env, p)) for p in people_of(env.answers)]
    main = next((url for p, url in urls if p["id"] == MAIN), None)
    if main:
        atomic_write(env.paths.etc_dir / "addon-url.txt", main + "\n")
    atomic_write(env.paths.etc_dir / "addon-urls.txt",
                 "".join(f"{p['name']}\t{url}\n" for p, url in urls if url))


ESSENTIALS = ("access.t.players", "access.t.settings")  # what the end of the first setup shows


def sections(env: Env, mask_login_password: bool = False, essentials_only: bool = False) -> list[tuple[str, list[str]]]:
    a = env.answers
    domain = _domain(env)
    aio = f"https://aiostreams.{domain}" if domain else None
    people = people_of(a)
    out: list[tuple[str, list[str]]] = []

    admin = a.get("admin_user")
    server = [t("access.server_text")]
    if domain:
        server.append(COPY + ssh_command(admin or "ubuntu", domain, bool(a.get("_ssh_key_created"))))
    if admin:
        server.append(t("access.server_hint"))
    out.append((t("access.t.server"), server))

    lines = [t("access.players_text")]
    for person in people:
        url = addon_url(env, person)
        lines += [person["name"] + ":", COPY + url] if url else [t("people.no_url", name=person["name"])]
    out.append((t("access.t.players"), lines))

    settings = [t("access.settings_text")] + ([COPY + aio] if aio else [])
    settings += _pair(t("access.login_name"), (a.get("auth") or {}).get("username"))
    login_password = env.secrets.get("answer.auth.password")
    if login_password and mask_login_password:  # the user chose it; only "My access" shows it again
        settings += [t("access.login_password"), t("access.password_masked")]
    else:
        settings += _pair(t("access.login_password"), login_password)
    settings.append(t("access.login_first_time"))
    out.append((t("access.t.settings"), settings))

    lines = [t("access.config_text")]
    for person in people:
        uuid_name, password_name, _ = secret_names(person["id"])
        lines.append(person["name"] + ":")
        lines += _pair(t("access.config_uuid"), env.secrets.get(uuid_name))
        lines += _pair(t("access.config_password"), env.secrets.get(password_name))
    out.append((t("access.t.config"), lines))

    if domain:
        out.append((t("access.t.prowlarr"), [t("access.prowlarr_text"), COPY + f"https://prowlarr.{env.domain}"]))
    if domain and "status" in env.stack_services():
        out.append((t("access.t.status"), [t("access.status_text"), COPY + f"https://status.{env.domain}"]))
    if domain and env.metadata_uuid:
        lines = [t("access.metadata_text"), COPY + f"https://metadata.{env.domain}/configure"]
        lines += _pair(t("access.config_uuid"), env.secrets.get("aiometadata_uuid"))
        lines += _pair(t("access.config_password"), env.secrets.get("aiometadata_config_password"))
        lines += _pair(t("access.metadata_addon_password"), env.secrets.get("aiometadata_addon_password"))
        out.append((t("access.t.metadata"), lines))

    server = (a.get("notify") or {}).get("ntfy_url")
    topic = env.secrets.get("ntfy_topic")
    if topic:
        lines = [t("access.push_text"), COPY + topic]
        if server and server.rstrip("/") != "https://ntfy.sh":
            lines += [t("access.push_server"), COPY + server]
        out.append((t("access.t.push"), lines))

    backup = env.load_json("backup.json")
    lines = [t("access.backup_text", dir=str(backup_ops.backup_dir(env)))]
    if backup.get("file"):
        lines += [t("access.backup_last", time=str(backup.get("time", "")).replace("T", " ")), COPY + backup["file"]]
    else:
        lines.append(t("access.no_backup"))
    lines.append(t("access.backup_key"))
    out.append((t("access.t.backup"), lines))
    if essentials_only:
        wanted = [t(k) for k in ESSENTIALS]
        out = [s for s in out if s[0] in wanted]
        out.sort(key=lambda s: wanted.index(s[0]))
    return out


def show(env: Env, ui: Prompter, mask_login_password: bool = False, essentials_only: bool = False) -> int:
    for title, lines in sections(env, mask_login_password, essentials_only):
        ui.box(title, lines)
    if essentials_only:
        ui.info(t("summary.details_later"))
        return 0
    ui.info(t("access.save_hint"))
    ui.hint(t("access.diy_hint"))
    return 0
