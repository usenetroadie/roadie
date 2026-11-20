"""The menu of `sudo roadie` after the setup, and the people menu.

A new menu entry is one MenuItem line in ITEMS."""

import time
from dataclasses import dataclass
from typing import Callable

from roadie import access, authcode, questions, system
from roadie.aiostreams import metadata, people
from roadie.aiostreams.api import AioClient, AioError
from roadie.aiostreams.builder import build_user_config
from roadie.answers import MAX_PEOPLE, Answers
from roadie.i18n import t
from roadie.ops.common import Env
from roadie.redact import Redactor
from roadie.render.envfiles import PLAYBACK
from roadie.ui import Prompter

Action = Callable[[Env, Prompter], int]


def _always(env: Env) -> bool:
    return True


def _status(env: Env, ui: Prompter) -> int:
    from roadie.ops import status
    return status.run(env)


RESTART_WAIT = 180  # seconds until every service must be healthy again
_sleep, _clock = time.sleep, time.monotonic  # replaced in tests


def _all_ok(env: Env) -> bool:
    services = env.services()
    return bool(services) and all(s.get("State") == "running" and s.get("Health") in ("healthy", "", None)
                                  for s in services)


def restart(env: Env, ui: Prompter) -> int:
    """Start stopped services and restart AIOStreams (it reads Prowlarr's indexers only at
    start), then wait until every service runs and is healthy."""
    ui.info(t("restart.running"))
    env.compose("up", "-d")
    env.compose("restart", "aiostreams")
    deadline = _clock() + RESTART_WAIT
    while True:
        if _all_ok(env):
            ui.ok(t("restart.ok"))
            return 0
        if _clock() >= deadline:
            ui.fail(t("restart.failed"))
            return 1
        _sleep(5)


def _doctor(env: Env, ui: Prompter) -> int:
    from roadie.ops import doctor
    return doctor.run(env, ui)


def _explain(env: Env, ui: Prompter) -> int:
    from roadie import explain
    return explain.run(env, ui)


def _backup(env: Env, ui: Prompter) -> int:
    from roadie.ops import backup
    return backup.run(env)


def _people(env: Env, ui: Prompter) -> int:
    return people_menu(env, ui, default_api)


def _uninstall(env: Env, ui: Prompter) -> int:
    from roadie.ops import uninstall
    return uninstall.run(env, ui, confirmed=False)


@dataclass
class MenuItem:
    key: str
    text_key: str
    run: Action | None  # None: only shown when the caller injects an action for `key`
    visible: Callable[[Env], bool] = _always
    pause: bool = True  # "Enter = back to the menu" afterwards; the help has its own way back


ITEMS: list[MenuItem] = [
    MenuItem("access", "menu.access", access.show),
    MenuItem("code", "menu.code", authcode.show),
    MenuItem("ssh", "menu.ssh", None, visible=lambda env: not env.state.answers.get("_ssh_hardened")),
    MenuItem("status", "menu.status", _status),
    MenuItem("restart", "menu.restart", restart),
    MenuItem("doctor", "menu.doctor", _doctor),
    MenuItem("people", "menu.people", _people),
    MenuItem("change", "menu.change", None),  # cli injects the setup run
    MenuItem("backup", "menu.backup", _backup),
    MenuItem("update", "menu.update", None),  # cli injects the update run
    MenuItem("explain", "menu.explain", lambda env, ui: _explain(env, ui), pause=False),
    MenuItem("uninstall", "menu.uninstall", _uninstall),  # last on purpose: not prominent
]


def visible_items(env: Env, actions: dict | None = None) -> list[tuple[MenuItem, Action]]:
    actions = actions or {}
    out = []
    for item in ITEMS:
        run = actions.get(item.key, item.run)
        if run is not None and item.visible(env):
            out.append((item, run))
    return out


def _pick(ui: Prompter, count: int) -> int:
    def valid(value: str):
        ok = value.isdigit() and 0 <= int(value) <= count
        return None if ok else t("ui.invalid_choice", range=f"0-{count}")
    return int(ui.ask(t("menu.prompt"), validate=valid))


def run(env: Env, ui: Prompter, actions: dict[str, Action] | None = None,
        reload: Callable[[], Env | None] | None = None) -> int:
    """`reload` reads state and secrets again before each round: an action such as
    'change settings' writes them through its own objects, and a stale Env would write old
    data back on its next save."""
    from roadie.ops import selfupdate
    notice = selfupdate.notice_lines(env.load_json("watch.json"))
    if notice:
        ui.alert(notice[0])
        ui.box(notice[1], notice[2:])
    try:
        while True:
            env = (reload() if reload else None) or env
            redact = Redactor(env.secrets.values())
            items = visible_items(env, actions)
            ui.heading(t("menu.title"))
            for n, (item, _) in enumerate(items, 1):
                ui.info(f" [{n}] {t(item.text_key)}")
            ui.info(f" [0] {t('menu.exit')}")
            choice = _pick(ui, len(items))
            if choice == 0:
                return 0
            item, action = items[choice - 1]
            try:
                code = action(env, ui)
            except (KeyboardInterrupt, EOFError):
                raise
            except Exception as exc:  # never show a traceback: it may contain secrets
                ui.fail(t("cli.unexpected", message=redact(f"{type(exc).__name__}: {exc}")[:600]))
            else:
                if not env.paths.state_file.exists():  # roadie was removed: nothing left to show
                    return code or 0
                if not item.pause:
                    continue
            ui.ask(t("menu.back"), default="", validate=lambda v: None)
    except EOFError:
        return 0
    except KeyboardInterrupt:
        ui.warn(t("cli.interrupted"))
        return 130


# people ------------------------------------------------------------------------------------

def default_api(env: Env) -> AioClient:
    """AIOStreams inside the server (its container address, like the setup uses)."""
    return AioClient(f"http://{system.container_ip(env.compose('ps', '-q', 'aiostreams').strip())}:3000")


def _mode_text(person: dict) -> str:
    return t(f"people.mode.{person['mode']}")


_ask_mode = questions.ask_person_mode


def _choose_person(ui: Prompter, persons: list[dict]) -> dict | None:
    if not persons:
        ui.info(t("people.none"))
        return None
    pid = ui.choose(t("people.pick"), [(p["id"], p["name"]) for p in persons])
    return next(p for p in persons if p["id"] == pid)


def _show_url(env: Env, ui: Prompter, person: dict) -> None:
    uuid_name, _, enc_name = people.secret_names(person["id"])
    uuid, enc = env.secrets.get(uuid_name), env.secrets.get(enc_name)
    if not uuid or not enc:
        ui.warn(t("people.no_url", name=person["name"]))
        return
    ui.info(t("people.url_for", name=person["name"]))
    ui.copyable(AioClient.addon_url(f"https://aiostreams.{env.domain}", uuid, enc))


def _sync(env: Env, ui: Prompter, api_factory) -> bool:
    """Write every person config to AIOStreams (creates missing ones)."""
    ui.info(t("people.saving"))
    try:
        api = api_factory(env)
        playback = f"{PLAYBACK}:{env.secrets.get('aiostreams_playback_password')}"
        uuid = env.metadata_uuid  # the same catalogs as the setup gives them
        base = build_user_config(Answers.restore(env.state, env.secrets), api.starter_template(), playback,
                                 metadata.preset(uuid) if uuid else None)
        people.sync(api, env.secrets, env.answers, base)
    except (AioError, OSError, system.CommandFailed):
        ui.fail(t("people.sync_failed"))
        return False
    ui.ok(t("people.saved"))
    return True


_name_check = questions.name_check


def people_menu(env: Env, ui: Prompter, api_factory: Callable[[Env], AioClient]) -> int:
    try:
        return _people_action(env, ui, api_factory)
    finally:  # addon-urls.txt always matches the people list
        try:
            access.write_url_files(env)
        except OSError:
            pass  # never hide the action's own result; the file is written again next time


def _people_action(env: Env, ui: Prompter, api_factory: Callable[[Env], AioClient]) -> int:
    persons = people.people_of(env.answers)
    ui.heading(t("menu.people"))
    for p in persons:
        ui.info(f"  {p['name']} ({_mode_text(p)})")
    choice = ui.choose(t("people.menu"), [("add", t("people.add")), ("change", t("people.change")),
                                          ("remove", t("people.remove")), ("url", t("people.url")),
                                          ("back", t("people.back"))])
    others = [p for p in persons if p["mode"] != people.MAIN]

    if choice == "add":
        if len(persons) >= MAX_PEOPLE:
            ui.warn(t("people.full", max=MAX_PEOPLE))
            return 1
        name = ui.ask(t("people.name"), validate=_name_check({p["name"].lower() for p in persons})).strip()
        person = {"id": people.slug(name, {p["id"] for p in persons}), "name": name}
        _ask_mode(ui, person)
        env.answers["people"] = [*persons, person]
        env.state.save()
        if not _sync(env, ui, api_factory):
            return 1
        _show_url(env, ui, person)
        return 0

    if choice == "change":
        person = _choose_person(ui, others)
        if person is None:
            return 0
        _ask_mode(ui, person)
        env.answers["people"] = persons
        env.state.save()
        return 0 if _sync(env, ui, api_factory) else 1

    if choice == "remove":
        person = _choose_person(ui, others)
        if person is None or not ui.confirm(t("people.remove_confirm", name=person["name"]), default=False):
            return 0
        try:
            removed = people.remove(api_factory(env), env.secrets, env.state, person["id"])
        except (OSError, system.CommandFailed):
            removed = people.Removal.FAILED
        if not removed:
            ui.fail(t(people.REMOVE_FAILED_TEXT[removed], name=person["name"]))
            return 1
        ui.ok(t("people.removed", name=person["name"]))
        return 0

    if choice == "url":
        person = _choose_person(ui, persons)
        if person is not None:
            _show_url(env, ui, person)
    return 0
