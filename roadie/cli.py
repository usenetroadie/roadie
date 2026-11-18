"""Command line entry point."""

import argparse
import os
import subprocess
import json
import sys
import time
from pathlib import Path

from roadie import __version__, banner, i18n, phases, preflight, questions
from roadie.answers import Answers, AnswersInvalid
from roadie.engine import Context, StepFailed, run_steps
from roadie.i18n import t
from roadie.paths import Busy, Paths, exclusive
from roadie.redact import Redactor
from roadie.secretstore import SecretStore
from roadie.state import State
from roadie.ui import Prompter

EXIT_OK, EXIT_FAILED, EXIT_INVALID, EXIT_INTERRUPTED = 0, 1, 2, 130
# commands that change the server run one at a time; the weekly backup waits for a running one
LOCKED = {None, "setup", "update", "backup", "restore", "uninstall", "restart", "people"}
BACKUP_LOCK_WAIT = 3600
ALIASES = {"zugang": "access", "personen": "people", "help": "explain", "hilfe": "explain", "neustart": "restart", "erklaeren": "explain",
           "deinstallieren": "uninstall"}


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="roadie", description=t("cli.description"))
    p.add_argument("--version", action="version", version=f"roadie {__version__}")
    p.add_argument("--lang", choices=i18n.LANGUAGES, help=t("cli.opt.lang"))
    p.add_argument("--root", default="/", help=argparse.SUPPRESS)
    p.add_argument("--skip-self-update", action="store_true", help=argparse.SUPPRESS)  # set after a self-update
    sub = p.add_subparsers(dest="command",
                           metavar="{setup,access,code,people,explain,demo,status,doctor,restart,backup,restore,update,diagnose,uninstall}")
    s = sub.add_parser("setup", help=t("cli.cmd.setup"))
    s.add_argument("--answers", metavar="FILE", help=t("cli.opt.answers"))
    s.add_argument("--dry-run", action="store_true", help=t("cli.opt.dry_run"))
    s.add_argument("--test-mode", action="store_true", help=argparse.SUPPRESS)  # self-signed cert, no DuckDNS
    sub.add_parser("access", aliases=["zugang"], help=t("cli.cmd.access"))
    sub.add_parser("code", help=t("cli.cmd.code"))
    sub.add_parser("people", aliases=["personen"], help=t("cli.cmd.people"))
    from roadie.explain import TOPICS as EXPLAIN_TOPICS
    ex = sub.add_parser("explain", aliases=["erklaeren", "help", "hilfe"], help=t("cli.cmd.explain"))
    ex.add_argument("topic", nargs="?", choices=[*EXPLAIN_TOPICS, "all"])
    st = sub.add_parser("status", help=t("cli.cmd.status"))
    st.add_argument("--motd", action="store_true", help=argparse.SUPPRESS)
    sub.add_parser("restart", aliases=["neustart"], help=t("cli.cmd.restart"))
    sub.add_parser("doctor", help=t("cli.cmd.doctor"))
    sub.add_parser("diagnose", help=t("cli.cmd.diagnose"))
    sub.add_parser("update", help=t("cli.cmd.update"))
    b = sub.add_parser("backup", help=t("cli.cmd.backup"))
    b.add_argument("--quiet", action="store_true", help=argparse.SUPPRESS)
    r = sub.add_parser("restore", help=t("cli.cmd.restore"))
    r.add_argument("archive", metavar="FILE")
    r.add_argument("--identity", metavar="KEYFILE", required=True, help=t("cli.opt.identity"))
    r.add_argument("--overwrite", action="store_true", help=t("cli.opt.overwrite"))
    sub.add_parser("demo", help=t("cli.cmd.demo"))
    u = sub.add_parser("uninstall", aliases=["deinstallieren"], help=t("cli.cmd.uninstall"))
    u.add_argument("--yes-delete-everything", "--ja-alles-loeschen", dest="yes_delete", action="store_true",
                   help=t("cli.opt.yes_delete"))
    w = sub.add_parser("watch")  # used by the systemd timer
    w.add_argument("--dry", action="store_true")
    rp = sub.add_parser("report")  # used by the systemd timer
    rp.add_argument("--dry", action="store_true")
    sub.add_parser("duckdns-update")  # used by the systemd timer
    return p


def _pre_lang(argv: list[str]) -> str | None:
    for i, arg in enumerate(argv):
        if arg == "--lang" and i + 1 < len(argv):
            return argv[i + 1]
        if arg.startswith("--lang="):
            return arg.split("=", 1)[1]
    return None


def main(argv: list[str] | None = None, gather=preflight.gather_facts, ui: Prompter | None = None,
         steps=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    lang = _pre_lang(argv)
    i18n.set_language(lang if lang in i18n.LANGUAGES else "en")
    args = _build_parser().parse_args(argv)
    args.command = ALIASES.get(args.command, args.command)
    args.argv = argv
    paths = Paths(Path(args.root))
    ui = ui or Prompter()
    if args.command != "demo" and paths.root == Path("/") and os.geteuid() != 0:
        ui.fail(t("cli.need_root"))  # everything else reads secrets or changes the system
        return EXIT_INVALID
    if args.command in (None, "setup", "demo"):
        banner.show(ui)  # only on a terminal: the menu, the setup, the demo
    if args.command is None and State(paths.state_file).is_done("summary"):
        return _menu(args, paths, ui, gather, lang is not None, steps or phases.steps)  # locks per action
    if args.command in LOCKED:
        wait = BACKUP_LOCK_WAIT if args.command == "backup" and args.quiet else 0
        return _locked(paths, ui, lambda: _dispatch(args, paths, ui, gather, lang, steps), wait)
    return _dispatch(args, paths, ui, gather, lang, steps)


def _locked(paths: Paths, ui: Prompter, run, wait: float = 0) -> int:
    try:
        with exclusive(paths.lock_file, wait):
            return run()
    except Busy:
        ui.fail(t("cli.busy"))
        return EXIT_INVALID


def _dispatch(args, paths: Paths, ui: Prompter, gather, lang, steps) -> int:
    if args.command in (None, "setup"):
        return _setup(args, paths, ui, gather, lang_given=lang is not None, steps=steps or phases.steps)
    if args.command == "duckdns-update":
        return _duckdns_update(paths)
    if args.command == "demo":
        return _demo(ui, lang_given=lang is not None)
    if args.command == "restore":
        from roadie.ops import backup
        return backup.restore(paths, args.archive, args.identity, overwrite=args.overwrite)
    if args.command == "uninstall":
        return _uninstall(paths, ui, args.yes_delete)
    return _maintenance(args, paths, ui, gather, steps or phases.steps)


def _menu(args, paths: Paths, ui: Prompter, gather, lang_given: bool, make_steps) -> int:
    from roadie import menu
    from roadie.ops import backup, common
    env = common.load_env(paths)
    if env is None:
        ui.fail(t("cli.not_set_up"))
        return EXIT_INVALID
    actions = {
        "change": lambda env, ui: _locked(paths, ui, lambda: _setup(args, paths, ui, gather, lang_given,
                                                                    make_steps, change=True)),
        "backup": lambda env, ui: _locked(paths, ui, lambda: backup.run(env)),
        "update": lambda env, ui: _locked(paths, ui, lambda: _update(env, paths, ui, gather, make_steps, args)),
        "restart": lambda env, ui: _locked(paths, ui, lambda: menu.restart(env, ui)),
        "people": lambda env, ui: _locked(paths, ui, lambda: menu.people_menu(env, ui, menu.default_api)),
    }
    return menu.run(env, ui, actions, reload=lambda: common.load_env(paths))


def _uninstall(paths: Paths, ui: Prompter, confirmed: bool) -> int:
    """Works also after a broken or half-finished setup (no answers saved yet)."""
    from roadie.ops import common, uninstall
    env = common.load_env(paths) or common.Env(paths, State(paths.state_file), SecretStore(paths.secrets_file))
    return _guarded(ui, Redactor(env.secrets.values()), lambda: uninstall.run(env, ui, confirmed))


def _guarded(ui: Prompter, redact: Redactor, run) -> int:
    """Never show a traceback: it may contain secrets."""
    try:
        return run()
    except (KeyboardInterrupt, EOFError):
        ui.warn(t("cli.interrupted"))
        return EXIT_INTERRUPTED
    except Exception as exc:
        ui.fail(t("cli.unexpected", message=redact(f"{type(exc).__name__}: {exc}")[:600]))
        return EXIT_FAILED


def _update(env, paths: Paths, ui: Prompter, gather, make_steps, args=None) -> int:
    from roadie.ops import backup, selfupdate, update
    if args is not None and not args.skip_self_update:
        code = selfupdate.run(paths, ui, args.argv)  # on success this process is replaced by the new version
        if code is not None:
            return code
    changes = update.plan(env)
    if not changes:  # still run the steps: a new wizard version may set things up differently
        ui.ok(t("update.none"))
    else:
        ui.heading(t("update.heading"))
        for service, old, new in changes:
            ui.info(f"  {service}: {old.rsplit(':', 1)[1]} -> {new.rsplit(':', 1)[1]}")
        if backup.recipient(env) and backup.run(env, quiet=True) != 0:
            ui.fail(t("update.backup_failed"))
            return EXIT_FAILED
    answers = Answers.restore(env.state, env.secrets)
    redact = Redactor(answers.secret_values() + env.secrets.values())
    code = _run_setup(answers, paths, ui, gather, redact, False, make_steps, update=True)
    if code == EXIT_OK:
        for _, old, _ in changes:
            subprocess.run(["docker", "image", "rm", old], capture_output=True)
        ui.ok(t("update.done"))
    return code


def _maintenance(args, paths: Paths, ui: Prompter, gather, make_steps) -> int:
    from roadie.ops import common
    env = common.load_env(paths)
    if env is None:
        ui.fail(t("cli.not_set_up"))
        return EXIT_INVALID
    if args.lang:  # chosen for this run: wins over the language of the setup
        i18n.set_language(args.lang)
    return _guarded(ui, Redactor(env.secrets.values()), lambda: _command(args, env, paths, ui, gather, make_steps))


def _command(args, env, paths: Paths, ui: Prompter, gather, make_steps) -> int:
    from roadie import access, authcode, menu
    from roadie.ops import backup, diagnose, doctor, status, watch
    if args.command == "access":
        return access.show(env, ui)
    if args.command == "code":
        return authcode.show(env, ui)
    if args.command == "people":
        return menu.people_menu(env, ui, menu.default_api)
    if args.command == "explain":
        from roadie import explain
        return explain.run(env, ui, args.topic)
    if args.command == "restart":
        return menu.restart(env, ui)
    if args.command == "status":
        return status.run(env, motd=args.motd)
    if args.command == "diagnose":
        return diagnose.run(env)
    if args.command == "doctor":
        return doctor.run(env, ui)
    if args.command == "report":
        return doctor.report(env, dry=args.dry)
    if args.command == "watch":
        return watch.run(env, dry=args.dry)
    if args.command == "backup":
        return backup.run(env, quiet=args.quiet)
    if args.command == "update":
        return _update(env, paths, ui, gather, make_steps, args)
    return EXIT_INVALID


class _DemoChecks:
    """Every live check succeeds, nothing is contacted."""

    def newznab_limits(self, url, key):
        return None, None  # the demo asks for the limits like an indexer that does not report them

    def server_ip(self):
        return "203.0.113.10"

    def __getattr__(self, name):
        from roadie.checks import CheckResult
        return lambda *args: CheckResult(True, "demo.check_ok")


def _demo(ui: Prompter, lang_given: bool = False) -> int:
    if lang_given:
        ui.heading(t("demo.banner"))
    else:  # the language is chosen only in the first question
        ui.heading("\n".join(i18n.load(lang)["demo.banner"] for lang in i18n.LANGUAGES))
    try:
        data = questions.ask_all(ui, _DemoChecks())
        answers = Answers.from_dict(data)
    except (KeyboardInterrupt, EOFError):
        ui.warn(t("cli.interrupted"))
        return EXIT_INTERRUPTED
    except AnswersInvalid as e:
        for err in e.errors:
            ui.fail(err)
        return EXIT_INVALID
    ui.heading(t("cli.dry_run_plan"))
    for step in phases.steps(answers):
        ui.info(f"  - {t(step.title_key)}")
    from roadie.answers import base_domain
    domain = f"aiostreams.{base_domain(answers.data)}"
    ui.heading(t("summary.heading"))
    ui.info(t("summary.addon_url"))
    ui.info(f"  https://{domain}/stremio/00000000-demo/ENCRYPTED-DEMO/manifest.json")
    ui.info(t("summary.login", url=f"https://{domain}", user=answers.data["auth"]["username"]))
    ui.heading(t("demo.banner"))
    return EXIT_OK


def _duckdns_update(paths: Paths) -> int:
    from roadie.checks.duckdns import check_duckdns
    sub = State(paths.state_file).answers.get("duckdns", {}).get("subdomain")
    token = SecretStore(paths.secrets_file).get("answer.duckdns.token")
    if not sub or not token:
        return EXIT_INVALID
    return EXIT_OK if check_duckdns(sub, token).ok else EXIT_FAILED


def _load_answers(path: str, ui: Prompter, lang_given: bool) -> Answers | None:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        ui.fail(t("cli.answers_unreadable", file=path))
        return None
    if not lang_given and data.get("language") in i18n.LANGUAGES:
        i18n.set_language(data["language"])
    try:
        return Answers.from_dict(data)
    except AnswersInvalid as e:
        ui.fail(t("cli.answers_invalid", count=len(e.errors)))
        for err in e.errors:
            ui.info(f"  - {err}")
        return None


def _interactive_answers(state: State, secrets: SecretStore, ui: Prompter, test_mode: bool = False,
                         change: bool = False, language: str | None = None) -> Answers | None:
    if state.answers and not change:
        choice = ui.choose(t("cli.menu"), [("continue", t("cli.menu.continue")),
                                           ("change", t("cli.menu.change"))], default="continue")
        if choice == "continue":
            return Answers.restore(state, secrets)
    if state.answers:  # change settings: review first, one part at a time, prefilled
        data = questions.change_settings(ui, Answers.restore(state, secrets).to_dict(include_secrets=True))
    else:
        data = questions.ask_all(ui, language=language)
    if test_mode:
        data["test_mode"] = True
    try:
        return Answers.from_dict(data)
    except AnswersInvalid as e:  # should not happen: every question validates its input
        for err in e.errors:
            ui.fail(err)
        return None


def _setup(args, paths: Paths, ui: Prompter, gather, lang_given: bool, steps, change: bool = False) -> int:
    dry_run = getattr(args, "dry_run", False)
    answers_file = getattr(args, "answers", None)
    answers = None
    if answers_file:
        answers = _load_answers(answers_file, ui, lang_given)
        if answers is None:
            return EXIT_INVALID
    redact = Redactor(answers.secret_values() if answers else ())
    return _guarded(ui, redact, lambda: _run_setup(answers, paths, ui, gather, redact, dry_run, steps,
                                                   test_mode=getattr(args, "test_mode", False), change=change,
                                                   ask_language=not lang_given))


def _run_setup(answers: Answers | None, paths: Paths, ui: Prompter, gather, redact: Redactor,
               dry_run: bool, make_steps, test_mode: bool = False, change: bool = False,
               update: bool = False, ask_language: bool = False) -> int:
    state, secrets = State(paths.state_file), SecretStore(paths.secrets_file)
    if answers is None and state.answers.get("language") in i18n.LANGUAGES:
        i18n.set_language(state.answers["language"])
    old_user = state.answers.get("admin_user")
    if answers is not None and old_user and answers.data.get("admin_user") != old_user:
        ui.fail(t("cli.admin_user_changed", old=old_user, new=answers.data.get("admin_user")))
        return EXIT_INVALID  # the admin account keeps SSH and sudo; it is never switched
    language = None
    if answers is None and not state.answers and ask_language:  # first interactive setup:
        language = questions.ask_language(ui)  # warnings of the server check already in this language

    ui.heading(t("phase.preflight"))
    facts = gather(paths)
    known = answers.data if answers else state.answers
    test_mode = test_mode or bool(known.get("test_mode"))  # automated tests use small machines
    findings = preflight.evaluate(facts, own_install=state.is_done("preflight"),
                                  min_disk_gb=8 if test_mode else preflight.MIN_DISK_GB)
    for f in findings:
        if f.level == "error":
            ui.fail(t(f.key, **f.params))
        elif f.level == "warn":
            ui.warn(t(f.key, **f.params))
    ui.ok(t("preflight.all_ok", count=sum(1 for f in findings if f.level == "ok")))
    if preflight.has_errors(findings) and not dry_run:
        return EXIT_INVALID

    interactive = answers is None
    if answers is None:
        answers = _interactive_answers(state, secrets, ui, test_mode, change, language)
        if answers is None:
            return EXIT_INVALID
    for value in answers.secret_values() + secrets.values():
        redact.add(value)

    steps = make_steps(answers)
    if dry_run:
        ui.heading(t("cli.dry_run_plan"))
        for step in steps:
            ui.info(f"  - {t(step.title_key)}")
        return EXIT_OK

    if make_steps is phases.steps and (os.geteuid() != 0 or paths.root != Path("/")):
        ui.fail(t("preflight.root"))  # the real steps change the system: root on the real system only
        return EXIT_INVALID
    answers.store(state, secrets)
    state.mark_done("preflight")
    state.save()
    ctx = Context(paths=paths, state=state, secrets=secrets, ui=ui, redact=redact, answers=answers)
    ctx.extra["interactive"] = interactive and not update
    ctx.extra["update"] = update  # an update never shows passwords (summary)
    phases.prepare(ctx, facts)
    try:
        run_steps(steps, ctx)
    except StepFailed as e:
        # for `sudo roadie diagnose`: which step failed and why (secrets already removed)
        state.answers["_last_failure"] = {"step": e.step_id, "message": redact(e.message)[:600],
                                          "at": time.strftime("%Y-%m-%d %H:%M")}
        state.save()
        ui.fail(t("cli.step_failed", step=e.step_id, message=e.message))
        ui.hint(t("cli.report_hint"))
        return EXIT_FAILED
    if state.answers.pop("_last_failure", None) is not None:
        state.save()
    open_points = ctx.extra.get("open_points") or []
    if not open_points:
        ui.ok(t("cli.done"))
        return EXIT_OK
    ui.warn(t("cli.done_open", count=len(open_points)))
    for title, key in open_points:
        ui.hint(f"{title}: {t(key)}")
    return EXIT_OK
