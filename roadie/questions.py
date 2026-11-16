"""Interactive questionnaire. Every answer is normalised and, where possible, checked live
against the real service before the next question. Returns a dict for Answers.from_dict()."""

import copy
import os
import re
import subprocess
import tempfile
from pathlib import Path

from roadie import i18n
from roadie.aiostreams.people import MAIN, slug
from roadie.answers import (MAX_PEOPLE, _data, admin_user_problem, content_languages, normalise_domain, normalise_duckdns,
                                  normalise_newznab_url, normalise_ssh_key, normalise_time)
from roadie.checks import domain as domain_check, duckdns, newznab, nntp, tmdb
from roadie.i18n import t
from roadie.ui import Prompter


UI_LANGUAGE_NAMES = {"en": "English", "de": "German"}  # interface language -> AIOStreams language
FEW_CONNECTIONS = 4
AGE_MARKER = "_age_recipient"  # state marker: the wizard created the backup key already
PREFILL = "_prefill"  # only while asking: "change settings" offers the current answers as defaults


class LiveChecks:
    """Real service checks; tests pass a fake with the same methods."""

    def duckdns(self, sub, token):
        return duckdns.check_duckdns(sub, token)

    def domain(self, name):
        return domain_check.check_domain(name)

    def server_ip(self):
        return domain_check.public_ipv4()

    def nntp(self, host, port, tls, user, password):
        return nntp.check_nntp(host, port, tls, user, password)

    def newznab(self, url, key):
        return newznab.check_newznab(url, key)

    def newznab_limits(self, url, key):
        return newznab.detect_limits(url, key)

    def tmdb(self, key):
        return tmdb.check_tmdb(key)


def _validator(fn):
    def check(value):
        try:
            fn(value)
        except ValueError as e:
            return str(e)
        return None
    return check


def _int_or_none(value: str, lo: int, hi: int):
    if not value:
        return None
    return None if value.isdigit() and lo <= int(value) <= hi else t("answers.err.range", min=lo, max=hi)


def _current(data: dict, key: str):
    """The stored answer to prefill with in "change settings", else None."""
    return copy.deepcopy(data.get(key)) if data.get(PREFILL) else None


def _ask_secret(ui: Prompter, label: str, old: str | None = None, validate=None) -> str:
    """Hidden input; with a current value Enter keeps it (the value is never shown)."""
    value = ui.ask_secret(label, validate, keep=bool(old))
    return old if value is None else value


def _checked(ui: Prompter, ask, check):
    """Ask (returns tuple of values) until check(*values) passes."""
    while True:
        values = ask()
        result = check(*values)
        if result.ok:
            ui.ok(t(result.key, **result.params))
            return values
        ui.fail(t(result.key, **result.params))


def ask_language(ui: Prompter, default: str | None = None) -> str:
    language = ui.choose(t("q.language"), [("en", "English"), ("de", "Deutsch")], default=default)
    i18n.set_language(language)
    return language


def ask_basics(ui: Prompter, data: dict, checks=None) -> None:
    """`data["language"]` already set (chosen before the server check): not asked again."""
    if data.pop("_language_given", False):
        i18n.set_language(data["language"])
    else:
        data["language"] = ask_language(ui, _current(data, "language"))


def start_page(ui: Prompter) -> None:
    """What the user needs before the questions start (spec 5.1)."""
    items = ["start.duckdns", "start.usenet", "start.indexer", "start.password_manager", "start.ntfy", "start.time"]
    ui.box(t("start.title"), [f"- {t(k)}" for k in items])
    ui.hint(t("start.cancel"))
    ui.ask(t("start.ready"), default="", validate=lambda v: None)


def ask_server(ui: Prompter, data: dict) -> None:
    """In "change settings" the admin user stays (the account exists already); a new SSH key is
    added to the server, the old keys keep working (nobody gets locked out)."""
    options = [("have", t("q.server.ssh_have.yes")), ("create", t("q.server.ssh_have.no"))]
    if _current(data, "admin_user"):
        ui.info(t("q.server.admin_user_fixed", user=data["admin_user"]))
        options.insert(0, ("keep", t("q.server.ssh_have.keep")))
        choice = ui.choose(t("q.server.ssh_change"), options, default="keep")
        if choice == "keep":
            return
        ui.hint(t("q.server.ssh_added"))
    else:
        data["admin_user"] = ui.ask(t("q.server.admin_user"), default="streamadmin",
                                    validate=admin_user_problem)
        ui.hint(t("q.server.login_as", user=data["admin_user"]))
        ui.info(t("q.server.ssh_intro"))
        choice = ui.choose(t("q.server.ssh_have"), options)
    have = choice == "have"
    data["_ssh_key_created"] = not have  # the SSH check then shows "ssh -i ~/.ssh/roadie ..."
    if have:
        ui.info(t("q.server.ssh_help"))
        data["ssh_public_key"] = normalise_ssh_key(ui.ask(t("q.server.ssh_key"), validate=_validator(normalise_ssh_key)))
    else:
        data["ssh_public_key"] = _create_ssh_key(ui, data["admin_user"])


def _create_ssh_key(ui: Prompter, user: str) -> str:
    """Create a key pair, show the private key once, keep only the public key.
    The private key is never written anywhere but a temporary folder that is removed at once."""
    with tempfile.TemporaryDirectory() as tmp:
        key_file = os.path.join(tmp, "key")
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", f"roadie-{user}", "-f", key_file],
                       check=True, capture_output=True, timeout=30)
        private = Path(key_file).read_text()
        public = Path(key_file + ".pub").read_text().strip()
    ui.alert(t("q.server.ssh_created_heading"))
    ui.warn(t("q.server.ssh_created_warning"))
    while True:
        for line in private.strip().splitlines():
            ui.copyable(line)
        ui.info(t("q.server.ssh_created_howto", user=user))
        if ui.confirm(t("q.server.ssh_created_saved"), default=False):
            ui.offer_clear()
            return normalise_ssh_key(public)

def ask_domain(ui: Prompter, data: dict, checks) -> None:
    old_own = _current(data, "domain")
    kinds = ("duckdns", "own")
    kind = ui.choose(t("q.domain.kind"), [(k, t(f"q.domain.kind.{k}")) for k in kinds],
                     default="own" if old_own else "duckdns", hints=[t(f"q.domain.kind.{k}.hint") for k in kinds])
    if kind == "own":
        ask_own_domain(ui, data, checks, old_own)
    else:
        ask_duckdns(ui, data, checks)


def ask_own_domain(ui: Prompter, data: dict, checks, old: str | None) -> None:
    ui.info(t("q.domain.own_help", ip=checks.server_ip() or "?"))
    for name in domain_check.names(t("q.domain.own_placeholder"), domain_check.HOSTS + ("metadata",)):
        ui.hint(f"  {name}")
    ui.info(t("q.domain.own_wildcard"))
    (name,) = _checked(ui, lambda: (normalise_domain(ui.ask(t("q.domain.own_name"), default=old,
                                                             validate=_validator(normalise_domain))),),
                       checks.domain)
    if old != name and (old or data.get("duckdns")) and data.get(PREFILL):
        ui.warn(t("q.domain.change_warning"))
    data.pop("duckdns", None)
    data["domain"] = name


def ask_duckdns(ui: Prompter, data: dict, checks) -> None:
    ui.info(t("q.domain.help"))
    old = _current(data, "duckdns") or {}

    def ask():
        sub = normalise_duckdns(ui.ask(t("q.domain.subdomain"), default=old.get("subdomain"),
                                       validate=_validator(normalise_duckdns)))
        return sub, _ask_secret(ui, t("q.domain.token"), old.get("token"))

    sub, token = _checked(ui, ask, checks.duckdns)
    if (old and sub != old.get("subdomain")) or (data.get("domain") and data.get(PREFILL)):
        ui.warn(t("q.domain.change_warning"))
    data.pop("domain", None)
    data["duckdns"] = {"subdomain": sub, "token": token}


def ask_usenet(ui: Prompter, data: dict, checks) -> None:
    known = nntp.known_providers()
    ui.info(t("q.usenet.list_source"))
    old = (_current(data, "usenet") or {}).get("providers") or []
    providers = []
    while True:
        role = "primary" if not providers else "backup"
        ui.info(t(f"q.usenet.role_{role}"))
        prev = old[len(providers)] if len(providers) < len(old) else {}

        def ask():
            pid = ui.choose(t("q.usenet.provider"), [(p["id"], p["name"]) for p in known]
                            + [("other", t("q.usenet.not_listed"))],
                            default=prev and prev.get("provider", "other"))
            p = next((x for x in known if x["id"] == pid), {})
            if p.get("host"):
                host, port, tls = p["host"], p["port"], p["tls"]
            else:
                if p:
                    ui.info(t("q.usenet.host_help", name=p["name"]))
                host = ui.ask(t("q.usenet.host"), default=prev.get("host"))
                port = int(ui.ask(t("q.usenet.port"), default=str(prev.get("port", 563)),
                                  validate=lambda v: _int_or_none(v, 1, 65535)))
                tls = ui.confirm(t("q.usenet.tls"), default=prev.get("tls", True))
            user = ui.ask(t("q.usenet.username"), default=prev.get("username"))
            same = host == prev.get("host")
            return pid, host, port, tls, user, _ask_secret(ui, t("q.usenet.password"),
                                                       prev.get("password") if same else None)

        pid, host, port, tls, user, password = _checked(ui, ask, lambda pid, *rest: checks.nntp(*rest))
        ui.info(t("check.nntp.connections_hint"))
        conns = int(ui.ask(t("q.usenet.connections"), default=str(prev.get("connections", 20)),
                           validate=lambda v: _int_or_none(v, 1, 100)))
        if conns < FEW_CONNECTIONS:
            ui.warn(t("q.usenet.few_connections"))
        entry = {"host": host, "port": port, "tls": tls, "username": user, "password": password,
                 "connections": conns, "role": role}
        if pid != "other":
            entry["provider"] = pid
        providers.append(entry)
        for backup, primary, level, network in nntp.same_backbone_warnings(providers):
            if backup in (host, next((x["name"] for x in known if x["id"] == pid), None)):
                key = "check.nntp.same_backbone" if level == "network" else "check.nntp.same_owner"
                ui.warn(t(key, backup=backup, primary=primary, backbone=network))
        if not ui.confirm(t("q.usenet.another"), default=len(providers) < len(old)):
            break
    data["usenet"] = {"providers": providers}


def ask_indexers(ui: Prompter, data: dict, checks) -> None:
    ui.heading(t("q.indexers.heading"))
    ui.info(t("q.indexers.help"))
    old = _current(data, "indexers") or []
    indexers = []
    while True:
        prev = old[len(indexers)] if len(indexers) < len(old) else {}
        name = ui.ask(t("q.indexers.name"), default=prev.get("name"))

        def ask():
            url = normalise_newznab_url(ui.ask(t("q.indexers.url"), default=prev.get("url"),
                                               validate=_validator(normalise_newznab_url)))
            same = url == prev.get("url")  # never send an old key to another address
            return url, _ask_secret(ui, t("q.indexers.key"), prev.get("api_key") if same else None)

        url, key = _checked(ui, ask, checks.newznab)
        api, grab = checks.newznab_limits(url, key)
        if api or grab:
            ui.ok(t("q.indexers.limits_found", api=api or t("q.indexers.unlimited"),
                    grab=grab or t("q.indexers.unlimited")))
        else:
            ui.info(t("q.indexers.limits_help"))
            if prev.get("daily_api_limit") or prev.get("daily_grab_limit"):
                ui.hint(t("q.indexers.limit_zero"))
            api = _ask_limit(ui, "q.indexers.limit", prev.get("daily_api_limit"))
            grab = _ask_limit(ui, "q.indexers.grab_limit", prev.get("daily_grab_limit"))
        indexers.append({"name": name, "url": url, "api_key": key,
                         "daily_api_limit": api, "daily_grab_limit": grab})
        if not ui.confirm(t("q.indexers.another"), default=len(indexers) < len(old)):
            break
    data["indexers"] = indexers


def _ask_limit(ui: Prompter, key: str, old: int | None = None) -> int | None:
    """With a stored limit, 0 removes it (Enter would keep it)."""
    value = ui.ask(t(key), default=str(old or ""), validate=lambda v: _int_or_none(v, 0 if old else 1, 1_000_000))
    return (int(value) or None) if value else None


def ask_tmdb(ui: Prompter, data: dict, checks) -> None:
    old = _current(data, "tmdb_api_key")
    data["tmdb_api_key"] = None
    ui.info(t("q.tmdb.help"))
    for k in ("q.tmdb.step1", "q.tmdb.step2", "q.tmdb.step3"):
        ui.hint(t(k))
    if ui.confirm(t("q.tmdb.have"), default=bool(old)):
        (key,) = _checked(ui, lambda: (_ask_secret(ui, t("q.tmdb.key"), old),), checks.tmdb)
        data["tmdb_api_key"] = key


def language_name(lang: str) -> str:
    """AIOStreams language name shown in the interface language."""
    return _data("language_names.json").get(i18n.language(), {}).get(lang, lang)


def language_order() -> list[str]:
    """Content languages with the interface language first, then English, then the rest as stored."""
    first = list(dict.fromkeys([UI_LANGUAGE_NAMES.get(i18n.language(), "English"), "English"]))
    return first + [lang for lang in content_languages() if lang not in first]


def ask_content(ui: Prompter, data: dict, heading: bool = True) -> None:
    if heading:
        ui.heading(t("q.content.heading"))
    old = _current(data, "content") or {}
    chosen = ui.choose_many(t("q.content.languages"), [(lang, language_name(lang)) for lang in language_order()],
                            default=old.get("languages"))
    original = ui.confirm(t("q.content.original_audio"), default=old.get("original_audio", True))
    if len(chosen) == 1 and not original:
        ui.warn(t("q.content.narrow"))
    ui.info(t("q.content.quality_help"))
    quality = old and ("4k" if old.get("prefer_4k") else "1080p")
    prefer_4k = ui.choose(t("q.content.quality"), [("4k", t("q.content.quality.4k")),
                                                   ("1080p", t("q.content.quality.1080p"))], default=quality) == "4k"
    hdr = prefer_4k and ui.confirm(t("q.content.hdr"), default=old.get("hdr", False))
    data["content"] = {"languages": chosen, "original_audio": original, "prefer_4k": prefer_4k,
                       "hdr": hdr, "max_size_gb": old.get("max_size_gb")}
    data["catalogs"] = _current(data, "catalogs") or []


def name_check(taken: set[str]):
    """Validator for a person's name; `taken` holds lower-case names already used."""
    def check(value: str):
        name = value.strip()
        if not 1 <= len(name) <= 40 or not name.isprintable():
            return t("answers.err.person_name", max=40)
        return t("people.name_taken") if name.lower() in taken else None
    return check


def ask_person_mode(ui: Prompter, person: dict) -> None:
    """Inherit everything from the main config, or own languages and quality."""
    mode = ui.choose(t("people.mode_q", name=person["name"]),
                     [("inherit", t("people.mode_inherit")), ("own", t("people.mode_own"))], default="inherit")
    person["mode"] = mode
    person.pop("content", None)
    if mode == "own":
        tmp: dict = {}
        ask_content(ui, tmp)
        person["content"] = tmp["content"]


def ask_people(ui: Prompter, data: dict) -> None:
    """Who is it for? Starts from scratch (spec 4). In "change settings" people are only shown:
    they are managed in the people menu."""
    if data.get(PREFILL) and data.get("people"):
        ui.info(t("q.people.kept"))
        for p in data["people"]:
            ui.hint(f"{p['name']} ({t('people.mode.' + p['mode'])})")
        return
    main = {"id": MAIN, "name": data.get("admin_user") or t("people.default_main"), "mode": "main"}
    if ui.choose(t("q.people"), [("one", t("q.people.one")), ("many", t("q.people.many"))]) == "one":
        data["people"] = [main]
        return
    ui.hint(t("q.people.main_help"))
    main["name"] = ui.ask(t("q.people.main_name"), default=main["name"], validate=name_check(set())).strip()
    persons = [main]
    while len(persons) < MAX_PEOPLE:
        check = name_check({p["name"].lower() for p in persons})
        name = ui.ask(t("q.people.next_name"), default="",
                      validate=lambda v: check(v) if v.strip() else None).strip()
        if not name:
            break
        person = {"id": slug(name, {p["id"] for p in persons}), "name": name}
        ask_person_mode(ui, person)
        persons.append(person)
    data["people"] = persons


def password_problem(password: str, login: str) -> str | None:
    """Error text for a too short or too easy login password, else None."""
    if len(password) < 12:
        return t("answers.err.password_short", min=12)
    name_inside = len(login) >= 3 and login.lower() in password.lower()  # "ab" is in too many words
    if name_inside or password.isdigit() or password.isalpha():
        return t("answers.err.password_weak")
    return None


def ask_auth(ui: Prompter, data: dict) -> None:
    ui.info(t("q.auth.help"))
    old = _current(data, "auth") or {}
    user = ui.ask(t("q.auth.username"), default=old.get("username") or "admin")
    keep = bool(old.get("password"))
    while True:
        pw = ui.ask_secret(t("q.auth.password"), validate=lambda v: password_problem(v, user), keep=keep)
        if pw is None:
            problem = password_problem(old["password"], user)  # a new login name can make it too easy
            if problem is None:
                pw = old["password"]
                break
            ui.fail(problem)
            keep = False
            continue
        if ui.ask_secret(t("q.auth.password_again")) == pw:
            break
        ui.fail(t("q.auth.mismatch"))
    data["auth"] = {"username": user, "password": pw}


def ask_operations(ui: Prompter, data: dict) -> None:
    reboot = (_current(data, "maintenance") or {}).get("reboot_time") or "05:00"
    old_hc = (_current(data, "notify") or {}).get("healthchecks_url")
    old_rec = (_current(data, "backup") or {}).get("age_recipient")
    data["maintenance"], data["notify"] = {"reboot_time": "05:00"}, {"healthchecks_url": None}
    ui.info(t("q.ops.advanced_help"))
    if ui.confirm(t("q.ops.advanced"), default=reboot != "05:00" or bool(old_hc)):
        data["maintenance"]["reboot_time"] = normalise_time(
            ui.ask(t("q.ops.reboot_time"), default=reboot, validate=_validator(normalise_time)))
        url_ok = lambda v: None if not v or v == "-" or v.startswith("https://") else t("answers.err.url")  # noqa: E731
        hc = (_ask_secret(ui, t("q.ops.healthchecks_change"), old_hc, url_ok) if old_hc  # a secret: hidden once set
              else ui.ask(t("q.ops.healthchecks"), default="", validate=url_ok))
        data["notify"]["healthchecks_url"] = None if hc in ("", "-") else hc
    ui.info(t("q.ops.age_help"))
    rec = ui.ask(t("q.ops.age"), default=old_rec or "",
                 validate=lambda v: None if not v or v.startswith("age1") else t("answers.err.age"))
    data["backup"] = {"age_recipient": rec or None}


def _ask_usenet_part(ui: Prompter, data: dict, checks) -> None:
    ask_usenet(ui, data, checks)
    ask_indexers(ui, data, checks)


# Ordered parts of the questionnaire: (key, fn(ui, data, checks)). "basics" (the language) comes
# first and is not numbered.
PARTS = [
    ("basics", ask_basics),
    ("server", lambda ui, data, checks: ask_server(ui, data)),
    ("domain", ask_domain),
    ("usenet", _ask_usenet_part),
    ("tmdb", ask_tmdb),
    ("content", lambda ui, data, checks: ask_content(ui, data, heading=False)),
    ("people", lambda ui, data, checks: ask_people(ui, data)),
    ("auth", lambda ui, data, checks: ask_auth(ui, data)),
    ("operations", lambda ui, data, checks: ask_operations(ui, data)),
]
PART_KEYS = [k for k, _ in PARTS]


def _ask_part(ui: Prompter, data: dict, checks, key: str) -> None:
    numbered = PART_KEYS[1:]
    if key in numbered:
        ui.section(numbered.index(key) + 1, len(numbered), t(f"part.{key}"))
    else:
        ui.heading(t(f"part.{key}"))
    dict(PARTS)[key](ui, data, checks)


def _secret(value) -> str:
    return t("review.secret") if value else t("review.none")


def _yes_no(value: bool) -> str:
    return t("review.yes") if value else t("review.no")


def _person_line(person: dict) -> str:
    line = f"{person['name']} ({t('people.mode.' + person['mode'])})"
    c = person.get("content")
    if person["mode"] != "own" or not c:
        return line
    langs = [language_name(x) for x in c["languages"]] + ([t("review.original")] if c["original_audio"] else [])
    quality = t("review.quality_first", value="4K" if c["prefer_4k"] else "1080p")
    return f"{line}: {' + '.join(langs)}, {quality}, {t('review.hdr', value=_yes_no(c['hdr']))}"


def _summary(key: str, data: dict) -> list[str]:
    """Review lines of one part; secret values only as "••• (set)"."""
    if key == "basics":
        return [t("review.language", value="Deutsch" if data["language"] == "de" else "English")]
    if key == "server":
        return [t("review.admin_user", value=data["admin_user"]),
                t("review.ssh_key", value=data["ssh_public_key"][:24] + "…")]
    if key == "domain":
        if data.get("domain"):
            return [t("review.address", value=data["domain"]), t("review.own_domain")]
        return [t("review.address", value=f"{data['duckdns']['subdomain']}.duckdns.org"),
                t("review.token", value=_secret(data["duckdns"]["token"]))]
    if key == "usenet":
        return ([t("review.provider", host=p["host"], user=p["username"], connections=p["connections"],
                   password=_secret(p["password"])) for p in data["usenet"]["providers"]]
                + [t("review.indexer", name=x["name"], url=x["url"], secret=_secret(x["api_key"]))
                   for x in data["indexers"]])
    if key == "tmdb":
        return [t("review.tmdb", value=_secret(data.get("tmdb_api_key")))]
    if key == "content":
        c = data["content"]
        return [t("review.languages", value=", ".join(language_name(x) for x in c["languages"])),
                t("review.original_audio", value=_yes_no(c["original_audio"])),
                t("review.quality", value="4K" if c["prefer_4k"] else "1080p"),
                t("review.hdr", value=_yes_no(c["hdr"]))]
    if key == "people":
        return [_person_line(p) for p in data["people"]]
    if key == "auth":
        return [t("review.login", value=data["auth"]["username"]),
                t("review.password", value=_secret(data["auth"]["password"]))]
    rec = data["backup"]["age_recipient"]
    created = t("review.backup_present") if data.get(AGE_MARKER) else t("review.backup_new")
    return [t("review.reboot", value=data["maintenance"]["reboot_time"]),
            t("review.healthchecks", value=_secret(data["notify"]["healthchecks_url"])),
            t("review.backup_key", value=rec[:16] + "…" if rec else created)]


def review(ui: Prompter, data: dict) -> str | None:
    """Show every answer (no secrets); None = all fine, else the key of the part to ask again."""
    keys = PART_KEYS
    while True:
        ui.heading(t("review.heading"))
        for key in keys:  # no numbers here: they would not match "Part x of y" (basics is unnumbered)
            ui.info(t(f"part.{key}"))
            for line in _summary(key, data):
                ui.hint(line)
        if ui.choose(t("review.ok_q"), [("ok", t("review.ok")), ("change", t("review.change"))]) == "ok":
            return None
        key = ui.choose(t("review.which"), [(k, t(f"part.{k}")) for k in keys] + [("back", t("review.back"))])
        if key != "back":
            return key


def ask_all(ui: Prompter, checks=None, language: str | None = None) -> dict:
    """`language`: already chosen (before the server check), so not asked again; the review can change it."""
    checks = checks or LiveChecks()
    data: dict = {}
    if language:
        data.update(language=language, _language_given=True)
    ask_basics(ui, data)
    start_page(ui)
    for key in PART_KEYS[1:]:
        _ask_part(ui, data, checks, key)
    return _review_until_ok(ui, data, checks)


def change_settings(ui: Prompter, data: dict, checks=None) -> dict:
    """"Change settings" (spec 4): the review of the stored answers (secrets included) first;
    only the chosen part is asked again, prefilled with its current values."""
    # markers stay in the state file; the backup key one is only shown in the review
    data = {k: v for k, v in copy.deepcopy(data).items() if not k.startswith("_") or k == AGE_MARKER}
    data[PREFILL] = True
    data = _review_until_ok(ui, data, checks or LiveChecks())
    data.pop(PREFILL)
    data.pop(AGE_MARKER, None)
    return data


def _review_until_ok(ui: Prompter, data: dict, checks) -> dict:
    while (key := review(ui, data)) is not None:
        _ask_part(ui, data, checks, key)
    return data
