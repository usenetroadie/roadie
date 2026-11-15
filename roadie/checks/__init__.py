"""Live checks of user input against the real services. Never raise for network errors."""

from dataclasses import dataclass, field


@dataclass
class CheckResult:
    ok: bool
    key: str  # i18n message key
    params: dict = field(default_factory=dict)  # never contains URLs or secrets


def network_failure(err) -> CheckResult:
    return CheckResult(False, "check.network", {"kind": err.kind})
