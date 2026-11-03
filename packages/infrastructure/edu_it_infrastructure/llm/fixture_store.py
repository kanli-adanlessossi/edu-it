"""Canonical-JSON fixture files (ai-content Req 2.2, 2.6, 3.2).

Layout: ``<root>/<prompt_id>/<key>.json``, where ``key`` is the domain
``fixture_key``. Each file holds one :class:`Fixture`: the recorded request
payload and its ordered attempts. A write is refused when the request payload
carries personal data, before any file is touched.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Final

from edu_it_domain.ai.fixtures import (
    Fixture,
    FixtureAttempt,
    decode,
    encode,
    fixture_key,
)
from edu_it_domain.ai.privacy import scan_payload
from edu_it_domain.ports.llm_provider import LLMInput, PersonalDataInRequestError, PromptRef

__all__ = ["FixtureStore"]

_SAFE_NAME: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._\-]*")


def _safe(name: str, what: str) -> str:
    if not _SAFE_NAME.fullmatch(name) or ".." in name:
        raise ValueError(f"unsafe {what} for a fixture path")
    return name


class FixtureStore:
    """Reads and writes fixtures under one root directory."""

    def __init__(self, root: Path, *, seeded_personal_data: Iterable[str] = ()) -> None:
        self._root = root
        self._seeded = tuple(seeded_personal_data)

    def path_for(self, prompt_id: str, key: str) -> Path:
        return self._root / _safe(prompt_id, "prompt_id") / f"{_safe(key, 'key')}.json"

    def read(self, prompt: PromptRef, input: LLMInput) -> Fixture | None:
        """Return the fixture for ``(prompt, input)``, or ``None`` when none is recorded."""
        key = fixture_key(prompt, input)
        path = self.path_for(prompt.prompt_id, key)
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        return decode(text)

    def write(self, fixture: Fixture) -> Path:
        """Write ``fixture`` after the privacy scan; raise ``PersonalDataInRequestError``."""
        findings = scan_payload(dict(fixture.request), self._seeded)
        if findings:
            raise PersonalDataInRequestError("; ".join(findings))
        path = self.path_for(fixture.prompt_id, fixture.key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(encode(fixture), encoding="utf-8", newline="\n")
        os.replace(tmp, path)
        return path

    def record_attempt(
        self, prompt: PromptRef, input: LLMInput, attempt: FixtureAttempt
    ) -> Fixture:
        """Append ``attempt`` to the fixture for ``(prompt, input)``, creating it if needed."""
        key = fixture_key(prompt, input)
        existing = self.read(prompt, input)
        attempts = (*(existing.attempts if existing else ()), attempt)
        fixture = Fixture(
            key=key,
            prompt_id=prompt.prompt_id,
            prompt_version=prompt.prompt_version,
            request={"kind": input.kind, "fields": dict(input.fields)},
            attempts=attempts,
        )
        self.write(fixture)
        return fixture

    def iter_fixtures(self) -> Iterable[Fixture]:
        """Yield every stored fixture (for the stored-fixture scan, Req 3.4)."""
        if not self._root.is_dir():
            return
        for path in sorted(self._root.glob("*/*.json")):
            yield decode(path.read_text(encoding="utf-8"))
