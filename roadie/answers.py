"""Answer schema: normalisation and validation shared by interactive and answer-file mode.

Answers are kept as a normalised dict. Secret fields are listed in SECRET_FIELDS and are
dropped by to_dict() so they never reach the state file.
"""

import base64
import binascii
import copy
import json
import pwd
import re
import struct
from importlib import resources
from urllib.parse import urlparse

from roadie.i18n import t

MAX_PEOPLE = 20
SSH_KEY_TYPES = (
    "ssh-ed25519", "ssh-rsa", "ecdsa-sha2-nistp256", "ecdsa-sha2-nistp384",
    "ecdsa-sha2-nistp521", "sk-ssh-ed25519@openssh.com", "sk-ecdsa-sha2-nistp256@openssh.com",
)
# (path, key) pairs; "[]" means: every item of a list
SECRET_FIELDS = (
    ("duckdns", "token"), ("usenet.providers[]", "password"),
    ("indexers[]", "api_key"), ("", "tmdb_api_key"), ("auth", "password"),
    ("notify", "healthchecks_url"),
)


def _data(name: str) -> dict:
    return json.loads(resources.files("roadie").joinpath(f"data/{name}").read_text("utf-8"))


def content_languages() -> list[str]:
    return _data("languages.json")["languages"]


def usenet_provider_ids() -> set[str]:
    return {p["id"] for p in _data("usenet_providers.json")["providers"]}


# --- normalisers (raise ValueError with a translated message) -------------------------

def normalise_duckdns(name: str) -> str:
    n = (name or "").strip().lower()
    n = re.sub(r"^https?://", "", n).rstrip("/")
    n = re.sub(r"\.duckdns\.org$", "", n)
    if not re.fullmatch(r"[a-z0-9-]{1,63}", n):
        raise ValueError(t("answers.err.duckdns_name"))
    return n


def normalise_ssh_key(text: str) -> str:
    text = (text or "").replace("\r", "").strip()
    if "PRIVATE KEY" in text:
        raise ValueError(t("answers.err.ssh_private"))
    parts = text.split()
    if len(parts) < 2 or parts[0] not in SSH_KEY_TYPES:
        raise ValueError(t("answers.err.ssh_format"))
    try:
        blob = base64.b64decode(parts[1], validate=True)
        (n,) = struct.unpack(">I", blob[:4])
        embedded = blob[4:4 + n].decode("ascii")
    except (binascii.Error, struct.error, UnicodeDecodeError, ValueError):
        raise ValueError(t("answers.err.ssh_format")) from None
    if embedded != parts[0]:
        raise ValueError(t("answers.err.ssh_format"))
    return " ".join(parts)


def normalise_newznab_url(url: str) -> str:
    u = (url or "").strip().rstrip("/")
    u = re.sub(r"/api$", "", u)
    if any(ord(ch) <= 0x20 or ord(ch) == 0x7f for ch in u):  # spaces, line breaks, control characters
        raise ValueError(t("answers.err.url"))
    parsed = urlparse(u)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError(t("answers.err.url"))
    return u


def normalise_time(value: str) -> str:
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", value or "")
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise ValueError(t("answers.err.time"))
    return f"{int(m.group(1)):02d}:{m.group(2)}"


def admin_user_problem(user: str) -> str | None:
    """Translated reason why `user` cannot be the admin account, or None. An existing service
    account (uid < 1000 or no login shell) must never get passwordless sudo."""
    if user == "root" or not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", user or ""):
        return t("answers.err.username")
    try:
        entry = pwd.getpwnam(user)
    except KeyError:
        return None  # a new account is created
    if entry.pw_uid < 1000 or entry.pw_shell.rsplit("/", 1)[-1] in ("nologin", "false"):
        return t("answers.err.system_user", user=user)
    return None


# --- validation -------------------------------------------------------------------------

class _Checker:
    def __init__(self, data: dict):
        self.data = data
        self.errors: list[str] = []

    def err(self, path: str, msg: str) -> None:
        self.errors.append(f"{path}: {msg}")

    def get(self, path: str, required: bool = True):
        node = self.data
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                if required:
                    self.err(path, t("answers.err.missing"))
                return None
            node = node[part]
        return node

    def norm(self, path: str, fn, required: bool = True):
        """Apply a normaliser to a field in place."""
        value = self.get(path, required)
        if value is None:
            return None
        try:
            new = fn(value)
        except ValueError as e:
            self.err(path, str(e))
            return None
        *parents, last = path.split(".")
        node = self.data
        for part in parents:
            node = node[part]
        node[last] = new
        return new


def _check_content(c: _Checker, content, path: str) -> None:
    if not isinstance(content, dict):
        c.err(path, t("answers.err.missing"))
        return
    langs = content.get("languages") or []
    allowed = content_languages()
    if not isinstance(langs, list) or not langs or any(lang not in allowed for lang in langs):
        c.err(f"{path}.languages", t("answers.err.languages"))
    for key in ("original_audio", "prefer_4k", "hdr"):
        if not isinstance(content.get(key), bool):
            c.err(f"{path}.{key}", t("answers.err.bool"))


def _check_people(c: _Checker, people) -> None:
    if not isinstance(people, list) or not 1 <= len(people) <= MAX_PEOPLE:
        c.err("people", t("answers.err.people_count", max=MAX_PEOPLE))
        return
    modes = [p.get("mode") if isinstance(p, dict) else None for p in people]
    if modes[0] != "main" or modes.count("main") != 1:
        c.err("people", t("answers.err.one_main"))
    seen = set()
    for i, p in enumerate(people):
        path = f"people[{i}]"
        if not isinstance(p, dict):
            c.err(path, t("answers.err.missing"))
            continue
        pid = p.get("id")
        if not isinstance(pid, str) or not re.fullmatch(r"[a-z0-9-]{1,32}", pid):
            c.err(f"{path}.id", t("answers.err.person_id"))
        elif (pid == "main") != (p.get("mode") == "main"):
            c.err(f"{path}.id", t("answers.err.main_id"))
        elif pid in seen:
            c.err(f"{path}.id", t("answers.err.duplicate"))
        seen.add(pid)
        name = p.get("name")
        if isinstance(name, str):
            name = p["name"] = name.strip()
        if not isinstance(name, str) or not 1 <= len(name) <= 40 or not name.isprintable():
            c.err(f"{path}.name", t("answers.err.person_name", max=40))
        if p.get("mode") not in ("main", "inherit", "own"):
            c.err(f"{path}.mode", t("answers.err.one_of", options="main, inherit, own"))
        if p.get("mode") == "own":
            _check_content(c, p.get("content"), f"{path}.content")
        elif "content" in p:
            c.err(f"{path}.content", t("answers.err.content_own_only"))


def _check(data: dict) -> _Checker:
    c = _Checker(data)
    if c.get("language") not in (None, "en", "de"):
        c.err("language", t("answers.err.one_of", options="en, de"))

    user = c.get("admin_user")
    problem = admin_user_problem(user) if user is not None else None
    if problem:
        c.err("admin_user", problem)

    c.norm("ssh_public_key", normalise_ssh_key)
    c.norm("duckdns.subdomain", normalise_duckdns)
    token = c.get("duckdns.token")
    if token is not None and not re.fullmatch(r"[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}", token.strip().lower()):
        c.err("duckdns.token", t("answers.err.duckdns_token"))

    providers = (data.get("usenet") or {}).get("providers") or []
    if not providers:
        c.err("usenet.providers", t("answers.err.need_provider"))
    elif sum(1 for p in providers if p.get("role") == "primary") != 1:
        c.err("usenet.providers", t("answers.err.one_primary"))
    for i, p in enumerate(providers):
        path = f"usenet.providers[{i}]"
        p.setdefault("port", 563)
        p.setdefault("tls", True)
        if not p.get("host"):
            c.err(f"{path}.host", t("answers.err.missing"))
        if not isinstance(p.get("port"), int) or not 1 <= p["port"] <= 65535:
            c.err(f"{path}.port", t("answers.err.range", min=1, max=65535))
        if not isinstance(p.get("connections"), int) or not 1 <= p["connections"] <= 100:
            c.err(f"{path}.connections", t("answers.err.range", min=1, max=100))
        if p.get("role") not in ("primary", "backup"):
            c.err(f"{path}.role", t("answers.err.one_of", options="primary, backup"))
        for key in ("username", "password"):
            if not p.get(key):
                c.err(f"{path}.{key}", t("answers.err.missing"))
        if "provider" in p and p["provider"] not in usenet_provider_ids():
            c.err(f"{path}.provider", t("answers.err.unknown_provider"))

    indexers = data.get("indexers") or []
    if not indexers:
        c.err("indexers", t("answers.err.need_indexer"))
    for i, ix in enumerate(indexers):
        path = f"indexers[{i}]"
        if not ix.get("name"):
            c.err(f"{path}.name", t("answers.err.missing"))
        if not ix.get("api_key"):
            c.err(f"{path}.api_key", t("answers.err.missing"))
        try:
            ix["url"] = normalise_newznab_url(ix.get("url", ""))
        except ValueError as e:
            c.err(f"{path}.url", str(e))
        for key in ("daily_api_limit", "daily_grab_limit"):
            v = ix.get(key)
            if v is not None and (not isinstance(v, int) or v < 1):
                c.err(f"{path}.{key}", t("answers.err.range", min=1, max="∞"))

    if c.get("content") is not None:
        _check_content(c, data["content"], "content")
    if "people" in data:
        _check_people(c, data["people"])

    auth_user = c.get("auth.username")
    if auth_user is not None and not re.fullmatch(r"[a-zA-Z0-9_.-]{2,32}", auth_user):
        c.err("auth.username", t("answers.err.username"))
    pw = c.get("auth.password")
    if pw is not None and len(pw) < 12:
        c.err("auth.password", t("answers.err.password_short", min=12))

    if c.get("maintenance.reboot_time", required=False) is None:
        data.setdefault("maintenance", {})["reboot_time"] = "05:00"
    c.norm("maintenance.reboot_time", normalise_time)

    for key in ("healthchecks_url", "ntfy_url"):  # https only: the URLs carry the push topic / ping id
        url = c.get(f"notify.{key}", required=False)
        if url and (not isinstance(url, str) or not url.startswith("https://")
                    or any(ord(ch) <= 0x20 or ord(ch) == 0x7f for ch in url)):
            c.err(f"notify.{key}", t("answers.err.url"))

    if not isinstance(data.get("test_mode", False), bool):
        c.err("test_mode", t("answers.err.bool"))
    rec = c.get("backup.age_recipient", required=False)  # None: wizard creates a key pair
    if rec is not None:
        if re.fullmatch(r"age1[02-9ac-hj-np-z]{58}", rec.strip()):
            data["backup"]["age_recipient"] = rec.strip()
        else:
            c.err("backup.age_recipient", t("answers.err.age"))
    return c


def validate(data: dict) -> list[str]:
    """Return every problem as 'field.path: message' (empty list = valid). Does not modify input."""
    return _check(copy.deepcopy(data)).errors


class AnswersInvalid(ValueError):
    def __init__(self, errors: list[str]):
        super().__init__("\n".join(errors))
        self.errors = errors


class Answers:
    def __init__(self, data: dict):
        self.data = data

    @classmethod
    def from_dict(cls, data: dict) -> "Answers":
        checker = _check(copy.deepcopy(data))
        if checker.errors:
            raise AnswersInvalid(checker.errors)
        return cls(checker.data)

    def _secret_slots(self, include_empty: bool = False):
        """Yield (container, key, name) for every secret field; name is stable, e.g.
        'answer.indexers[0].api_key', and used as the SecretStore entry name."""
        for path, key in SECRET_FIELDS:
            nodes = [(self.data, "answer")]
            for part in filter(None, path.split(".")):
                many = part.endswith("[]")
                name = part[:-2] if many else part
                nxt = []
                for node, prefix in nodes:
                    v = node.get(name) if isinstance(node, dict) else None
                    if v is None:
                        continue
                    if many:
                        nxt.extend((item, f"{prefix}.{name}[{i}]") for i, item in enumerate(v))
                    else:
                        nxt.append((v, f"{prefix}.{name}"))
                nodes = nxt
            for node, prefix in nodes:
                if isinstance(node, dict) and (node.get(key) or (include_empty and key in node)):
                    yield node, key, f"{prefix}.{key}"

    def store(self, state, secrets) -> None:
        """Secrets go to the SecretStore, everything else to the state file.

        People: answers without a people list keep the stored people (they are added to
        self.data). People an explicit list leaves out are noted in the state marker
        "_people_dropped"; the AIOStreams step deletes their configs."""
        current = {}
        for node, key, name in self._secret_slots():
            current[name] = node[key]
            secrets.set(name, node[key])
        for name in secrets.names():  # an answer removed now must not come back on restore
            if name.startswith("answer.") and name not in current:
                secrets.delete(name)
        markers = {k: v for k, v in state.answers.items() if k.startswith("_")}  # wizard's own progress notes
        if "_ssh_key_created" in self.data:  # answered again: the new answer counts
            markers.pop("_ssh_key_created", None)
        old_people = state.answers.get("people") or []
        if "people" not in self.data and old_people:
            self.data["people"] = copy.deepcopy(old_people)
        ids = {p["id"] for p in self.data.get("people") or []}
        dropped = markers.get("_people_dropped", []) + [p for p in old_people if p["id"] not in ids]
        dropped = [p for p in {p["id"]: p for p in dropped}.values() if p["id"] not in ids]
        markers.pop("_people_dropped", None)
        if dropped:
            markers["_people_dropped"] = dropped
        state.answers = {**self.to_dict(), **markers}
        state.save()

    @classmethod
    def restore(cls, state, secrets) -> "Answers":
        answers = cls(copy.deepcopy(state.answers))
        for node, key, name in answers._secret_slots(include_empty=True):
            node[key] = secrets.get(name)
        return answers

    def secret_values(self) -> list[str]:
        return [n[k] for n, k, _ in self._secret_slots()]

    def to_dict(self, include_secrets: bool = False) -> dict:
        if include_secrets:
            return copy.deepcopy(self.data)
        clone = Answers(copy.deepcopy(self.data))
        for n, k, _ in list(clone._secret_slots()):
            n[k] = None
        return clone.data
