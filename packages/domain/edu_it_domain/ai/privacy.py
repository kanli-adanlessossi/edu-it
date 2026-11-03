"""Personal-data scan for LLM inputs and recorded payloads (ai-content Req 3.1, 3.2, 3.4).

The scan is defense in depth behind the field allowlist. It rejects email
addresses, ``usr_`` identifiers, session-token-shaped strings and any seeded
personal-data sample. Findings never echo the offending value.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Final

from edu_it_domain.ai.inputs import disallowed_fields
from edu_it_domain.ports.llm_provider import LLMInput, PersonalDataInRequestError

__all__ = ["ensure_clean", "scan", "scan_payload"]

_EMAIL: Final = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+")
_USER_ID: Final = re.compile(r"\busr_[A-Za-z0-9_\-]+")
_SESSION_PREFIX: Final = re.compile(r"\bsess(?:ion)?_[A-Za-z0-9_\-]{8,}")
# A 32+ character URL-safe run mixing letters and digits looks like a token.
_TOKEN_SHAPED: Final = re.compile(
    r"(?<![A-Za-z0-9_\-])(?=[A-Za-z0-9_\-]*\d)(?=[A-Za-z0-9_\-]*[A-Za-z])[A-Za-z0-9_\-]{32,}"
)
_PATTERNS: Final = (
    ("email-address", _EMAIL),
    ("user-identifier", _USER_ID),
    ("session-token", _SESSION_PREFIX),
    ("session-token", _TOKEN_SHAPED),
)


def _strings(value: object, path: str) -> Iterable[tuple[str, str]]:
    """Yield ``(path, text)`` for every string in a nested structure."""
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, Mapping):
        for key in sorted(value, key=str):
            yield from _strings(value[key], f"{path}.{key}" if path else str(key))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            yield from _strings(item, f"{path}[{index}]")


def scan_payload(payload: object, seeded: Iterable[str] = ()) -> tuple[str, ...]:
    """Scan any nested payload; return ``"<path>: <reason>"`` findings, never values."""
    samples = tuple(s.casefold() for s in seeded if s)
    findings: list[str] = []
    for path, text in _strings(payload, ""):
        for reason, pattern in _PATTERNS:
            if pattern.search(text):
                findings.append(f"{path}: {reason}")
        folded = text.casefold()
        if any(sample in folded for sample in samples):
            findings.append(f"{path}: seeded-sample")
    return tuple(findings)


def scan(value: LLMInput, seeded: Iterable[str] = ()) -> tuple[str, ...]:
    """Return findings for ``value``: fields outside the allowlist plus value matches."""
    findings = [f"{name}: field-not-allowed" for name in disallowed_fields(value)]
    findings.extend(scan_payload(dict(value.fields), seeded))
    return tuple(findings)


def ensure_clean(value: LLMInput, seeded: Iterable[str] = ()) -> None:
    """Raise ``PersonalDataInRequestError`` when ``scan`` finds anything."""
    findings = scan(value, seeded)
    if findings:
        raise PersonalDataInRequestError("; ".join(findings))
