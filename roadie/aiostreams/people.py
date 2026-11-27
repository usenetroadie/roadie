"""People: every extra person gets an own AIOStreams config that inherits from the main
config (AIOStreams `parentConfig`), so each person has an own addon URL and history.

No UI here; AIOStreams errors are raised as AioError and handled by the callers."""

import copy
import enum
import re
import unicodedata

from roadie.aiostreams.api import AioClient, AioError
from roadie.aiostreams.builder import (PERSON_FIELDS, SECTIONS, WIZARD_PRESETS, content_fields,
                                       merge_catalog_mods, merge_groups, merge_presets, merge_sections,
                                       presets_for, row_addon, sections_for, tmdb_slot)
from roadie.i18n import t

MAIN = "main"
DROPPED = "_people_dropped"  # state marker: people an answers file left out
_MAIN_SECRETS = ("aiostreams_config_uuid", "aiostreams_config_password", "aiostreams_config_encrypted")
_GERMAN = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})


def people_of(data: dict) -> list[dict]:
    """The people list of an answers dict; installs without one have only the main config."""
    return data.get("people") or [{"id": MAIN, "name": t("people.default_main"), "mode": MAIN}]


def slug(name: str, taken: set[str]) -> str:
    """A person id ([a-z0-9-]{1,32}) from a name, not in `taken`."""
    text = unicodedata.normalize("NFKD", (name or "").lower().translate(_GERMAN))
    base = re.sub(r"[^a-z0-9]+", "-", text.encode("ascii", "ignore").decode()).strip("-")[:29].strip("-")
    base = base or "person"
    candidate, n = base, 1
    while candidate in taken or candidate == MAIN:  # "main" is reserved for the main config
        n += 1
        candidate = f"{base}-{n}"
    return candidate


def secret_names(pid: str) -> tuple[str, str, str]:
    """SecretStore names (uuid, password, encrypted password) of a person's config."""
    if pid == MAIN:
        return _MAIN_SECRETS
    return f"person.{pid}.uuid", f"person.{pid}.password", f"person.{pid}.encrypted"


def child_config(base: dict, main_uuid: str, main_password: str, person: dict) -> dict:
    cfg = copy.deepcopy(base)
    parent = {"uuid": main_uuid, "password": main_password}
    if person["mode"] == "own":
        cfg.update(content_fields(person["content"]))
        parent["mergeStrategies"] = {"fieldOverrides": {f: "override" for f in PERSON_FIELDS}}
    cfg["parentConfig"] = parent
    return cfg


def update_keeping_own(api: AioClient, uuid: str, password: str, config: dict, sections: list[str] | None = None,
                       readd: set[str] | None = None, write_presets: bool = True,
                       write_parent: bool = True) -> None:
    """PUT `config` over an existing config, but only the given wizard `sections` (default: all)
    and, with `write_presets`, its own presets, catalog rows and groups: whatever the user changed
    elsewhere on the AIOStreams website stays. Wizard presets the user deleted only come back when
    their instanceId is in `readd` (default: all of them).

    Without `write_presets` the TMDB slot is still kept in line with `config`: AIOMetadata comes
    and goes with the server (RAM), not only with an answer. Then only that addon and its catalog
    rows change.

    Without `write_parent` an existing parentConfig stays as it is (the user may have changed
    its merge strategies); a missing one is still set."""
    current = api.get_user(uuid, password)
    merged = merge_sections(current, config, list(SECTIONS) if sections is None else sections)
    slot_now, slot_new = tmdb_slot(current.get("presets") or []), tmdb_slot(config.get("presets") or [])
    if write_presets:
        _merge_addons(merged, current, config, set(WIZARD_PRESETS) if readd is None else readd)
    elif slot_now and slot_new and slot_now != slot_new:
        # the other wizard presets as they are now, only the slot from `config`
        swap = [p for p in config["presets"] if p["instanceId"] == slot_new]
        swap += [p for p in current["presets"] if _ours(p) and p["instanceId"] != slot_now]
        _merge_rows(merged, current, config, merge_presets(current["presets"], swap), {slot_new})
    if "parentConfig" in config and (write_parent or "parentConfig" not in current):
        merged["parentConfig"] = copy.deepcopy(config["parentConfig"])
    if merged != current:  # a plain resume changes nothing: save the rate-limited call
        api.update_user(uuid, password, merged)


def _ours(preset: dict) -> bool:
    return preset.get("instanceId") in WIZARD_PRESETS


def _merge_rows(merged: dict, current: dict, config: dict, presets: list[dict],
                only: set[str] | None = None) -> None:
    """Set `presets`; catalog rows of wizard addons that are gone now go too, and the wizard's
    rows for its addons that are there are added when missing (the user's rows stay).
    With `only`, new rows are added for these addons only."""
    merged["presets"] = presets
    if "catalogModifications" not in config and "catalogModifications" not in current:
        return
    present = {p.get("instanceId") for p in presets}
    dropped = {p.get("instanceId") for p in current.get("presets") or [] if _ours(p)} - present
    rows = [m for m in current.get("catalogModifications") or [] if row_addon(m) not in dropped]
    new_rows = [m for m in config.get("catalogModifications") or []
                if row_addon(m) in present and (only is None or row_addon(m) in only)]
    merged["catalogModifications"] = merge_catalog_mods(rows, new_rows)


def _merge_addons(merged: dict, current: dict, config: dict, readd: set[str]) -> None:
    """Presets, catalog rows and groups per entry: addons and rows the user added stay."""
    if "presets" in config or "presets" in current:
        _merge_rows(merged, current, config,
                    merge_presets(current.get("presets") or [], config.get("presets") or [], readd))
    groups = merge_groups(current.get("groups"), config.get("groups"))
    if groups:
        merged["groups"] = groups
    else:
        merged.pop("groups", None)
    if "resultLimits" in config and "resultLimits" not in current:  # cleared on the website ({}) counts as the user's own
        merged["resultLimits"] = copy.deepcopy(config["resultLimits"])


def config_parts(changed: set[str] | None) -> tuple[list[str], dict]:
    """(sections, presets_for flags) for the changed answer parts; None = everything."""
    changed = {"*"} if changed is None else changed
    return sections_for(changed), presets_for(changed)


def sync(api: AioClient, secrets, data: dict, base_config: dict, changed: set[str] | None = None) -> None:
    """Create missing person configs and update existing ones, only in the parts the changed
    answers feed (None = everything; see builder.sections_for). The main config must exist.
    Each person's secrets are saved right after their create, so a later failure loses nothing."""
    main_uuid, main_password = secrets.get(_MAIN_SECRETS[0]), secrets.get(_MAIN_SECRETS[1])
    sections, flags = config_parts(changed)
    write_parent = changed is None or bool({"*", "people"} & changed)
    if changed and "people" in changed:  # a person's mode or own content may have changed
        sections = [s for s in SECTIONS if s in sections or set(SECTIONS[s]) & set(PERSON_FIELDS)]
    for person in people_of(data):
        if person["mode"] == MAIN:
            continue
        uuid_name, password_name, encrypted_name = secret_names(person["id"])
        password = secrets.generate(password_name, 24)
        config = child_config(base_config, main_uuid, main_password, person)
        uuid = secrets.get(uuid_name)
        if uuid:
            update_keeping_own(api, uuid, password, config, sections, flags["readd"], flags["write"], write_parent)
        else:  # also in a resumed first setup (no changed answers): a missing config is created
            uuid, encrypted = api.create_user(config, password)
            secrets.set(uuid_name, uuid)
            secrets.set(encrypted_name, encrypted)


class Removal(enum.Enum):
    """Result of deleting a person's config; true only when it is gone."""
    DELETED = "deleted"
    FAILED = "failed"  # AIOStreams did not answer or refused
    PASSWORD = "password"  # the stored password no longer fits a config that still works

    def __bool__(self) -> bool:
        return self is Removal.DELETED


def delete_config(api: AioClient, secrets, pid: str) -> Removal:
    """DELETE a person's config (their addon URL stops working), then forget its secrets.
    A config that is already gone counts as deleted. On failure nothing is forgotten.

    AIOStreams 2.37 answers USER_INVALID_DETAILS (400) both for an unknown uuid and for a wrong
    password; the config only counts as gone when its addon address no longer works."""
    names = secret_names(pid)
    uuid, password, encrypted = (secrets.get(n) for n in names)
    if uuid:
        try:
            api.delete_user(uuid, password)
        except AioError as e:
            if e.code == "USER_INVALID_DETAILS":
                if api.manifest_ok(uuid, encrypted or ""):
                    return Removal.PASSWORD
            elif not (e.status == 404 or "NOT_FOUND" in e.code):
                return Removal.FAILED
    for name in names:
        secrets.delete(name)
    return Removal.DELETED


REMOVE_FAILED_TEXT = {Removal.FAILED: "people.remove_failed", Removal.PASSWORD: "people.remove_failed_auth"}


def remove(api: AioClient, secrets, state, pid: str) -> Removal:
    """Delete a person's config, then forget them. On failure nothing changes (the result is
    false). The main config cannot be removed."""
    if pid == MAIN or pid not in {p["id"] for p in people_of(state.answers)}:
        return Removal.FAILED
    result = delete_config(api, secrets, pid)
    if result:
        state.answers["people"] = [p for p in people_of(state.answers) if p["id"] != pid]
        state.save()
    return result


def remove_dropped(api: AioClient, secrets, state, data: dict) -> list[tuple[dict, Removal]]:
    """Delete the configs of people an answers file left out (Answers.store notes them in
    state marker DROPPED). Who cannot be deleted is put back into the people list of `data`
    and the state, so they stay visible and removable; they are returned with the reason."""
    kept = []
    for person in state.answers.pop(DROPPED, []):
        result = delete_config(api, secrets, person["id"])
        if not result:
            kept.append((person, result))
    if kept:
        data["people"] = people_of(data) + [person for person, _ in kept]
        state.answers["people"] = copy.deepcopy(data["people"])
    state.save()
    return kept
