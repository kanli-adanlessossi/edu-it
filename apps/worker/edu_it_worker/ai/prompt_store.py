"""Versioned prompt store (ai-content Req 4.1, 4.2, 4.4, 4.5, 4.6).

A Prompt lives in ``prompts/<prompt_id>__v<version>.md``: a front-matter block
(``output_schema``, ``input_kind``, ``includes``) followed by the prompt text.
The store loads, parses and hashes those files. A text change is a new version
file (enforced in CI by ``tools/check_prompt_immutability.py``), so a
``(prompt_id, prompt_version)`` pair always names the same text and the same
hash. A prompt lists the prompts it embeds as ``includes: glossary@1``; the
Glossary_Prompt is versioned by the same rules, so changing it needs a new
glossary version and a new version of each prompt that includes it.

Front matter is a deliberately tiny subset of YAML (``key: value`` lines, with
``includes`` a comma-separated list of ``<prompt_id>@<version>``) so the store
needs no parser dependency. Stdlib only; no network.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final

__all__ = [
    "GLOSSARY_PROMPT_ID",
    "NO_INPUT_KIND",
    "NO_OUTPUT_SCHEMA",
    "PROMPTS_ROOT_ENV",
    "Prompt",
    "PromptFormatError",
    "PromptNotFoundError",
    "PromptStore",
    "default_prompts_dir",
    "glossary_pairs",
]

#: ``prompt_id`` of the Glossary_Prompt (Req 4.4, 4.6).
GLOSSARY_PROMPT_ID: Final = "glossary"

#: Front-matter value for a prompt that is only embedded, never sent on its own.
NO_OUTPUT_SCHEMA: Final = "none"
NO_INPUT_KIND: Final = "none"

_ID_RE: Final = re.compile(r"^[a-z][a-z0-9_]*$")
_VERSION_RE: Final = re.compile(r"^[1-9][0-9]*$")
_FILE_RE: Final = re.compile(r"^(?P<id>[a-z][a-z0-9_]*)__v(?P<version>[1-9][0-9]*)\.md$")
_FRONT_KEYS: Final = frozenset({"output_schema", "input_kind", "includes"})
_FENCE: Final = "---"


class PromptNotFoundError(LookupError):
    """No file exists for the requested ``prompt_id`` and ``prompt_version``."""


class PromptFormatError(ValueError):
    """A prompt file or reference is malformed."""


@dataclass(frozen=True, slots=True)
class Prompt:
    """One parsed prompt file."""

    prompt_id: str
    prompt_version: str
    output_schema: str
    input_kind: str
    includes: tuple[tuple[str, str], ...]
    """Included prompts as ``(prompt_id, prompt_version)``, in declared order."""

    body: str
    """The prompt text after the front matter."""

    sha256: str
    """Hex SHA-256 of the file's bytes (front matter included)."""


#: Environment variable that overrides the prompts directory (set in the worker image, where the
#: package is installed into a virtual environment and the source-tree layout does not hold).
PROMPTS_ROOT_ENV: Final = "EDU_IT_PROMPTS_ROOT"


def default_prompts_dir() -> Path:
    """Return the ``prompts/`` dir: ``$EDU_IT_PROMPTS_ROOT`` or the repository's ``prompts/``."""
    override = os.environ.get(PROMPTS_ROOT_ENV)
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[4] / "prompts"


def _parse_ref(ref: str) -> tuple[str, str]:
    prompt_id, sep, version = ref.strip().partition("@")
    if not sep or not _ID_RE.fullmatch(prompt_id) or not _VERSION_RE.fullmatch(version):
        msg = f"invalid include {ref!r}; expected <prompt_id>@<version>"
        raise PromptFormatError(msg)
    return prompt_id, version


def _parse(prompt_id: str, version: str, raw: bytes) -> Prompt:
    text = raw.decode("utf-8").replace("\r\n", "\n")
    lines = text.split("\n")
    where = f"{prompt_id}__v{version}.md"
    if not lines or lines[0].strip() != _FENCE:
        msg = f"{where}: missing front matter"
        raise PromptFormatError(msg)
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == _FENCE)
    except StopIteration:
        msg = f"{where}: unterminated front matter"
        raise PromptFormatError(msg) from None

    front: dict[str, str] = {}
    for line in lines[1:end]:
        if not line.strip():
            continue
        key, sep, value = line.partition(":")
        key = key.strip()
        if not sep or key not in _FRONT_KEYS or key in front:
            msg = f"{where}: bad front-matter line {line!r}"
            raise PromptFormatError(msg)
        front[key] = value.strip()
    missing = sorted(_FRONT_KEYS - front.keys())
    if missing:
        msg = f"{where}: missing front-matter key(s) {', '.join(missing)}"
        raise PromptFormatError(msg)

    includes = tuple(_parse_ref(part) for part in front["includes"].split(",") if part.strip())
    if (prompt_id, version) in includes:
        msg = f"{where}: a prompt cannot include itself"
        raise PromptFormatError(msg)
    if not front["output_schema"] or not front["input_kind"]:
        msg = f"{where}: output_schema and input_kind must not be empty"
        raise PromptFormatError(msg)

    return Prompt(
        prompt_id=prompt_id,
        prompt_version=version,
        output_schema=front["output_schema"],
        input_kind=front["input_kind"],
        includes=includes,
        body="\n".join(lines[end + 1 :]).strip("\n") + "\n",
        sha256=hashlib.sha256(raw).hexdigest(),
    )


class PromptStore:
    """Loads prompt files from a directory."""

    def __init__(self, root: Path | None = None) -> None:
        self._root = root if root is not None else default_prompts_dir()

    def load(self, prompt_id: str, prompt_version: str) -> Prompt:
        """Return the prompt for the pair, or raise ``PromptNotFoundError``."""
        if not _ID_RE.fullmatch(prompt_id) or not _VERSION_RE.fullmatch(prompt_version):
            msg = f"invalid prompt reference {prompt_id!r} v{prompt_version!r}"
            raise PromptFormatError(msg)
        path = self._root / f"{prompt_id}__v{prompt_version}.md"
        if not path.is_file():
            msg = f"no prompt file {path.name}"
            raise PromptNotFoundError(msg)
        return _parse(prompt_id, prompt_version, path.read_bytes())

    def available(self) -> tuple[tuple[str, str], ...]:
        """Return every ``(prompt_id, prompt_version)`` on disk, sorted."""
        found = []
        for path in self._root.glob("*.md"):
            match = _FILE_RE.match(path.name)
            if match:
                found.append((match["id"], match["version"]))
        return tuple(sorted(found, key=lambda p: (p[0], int(p[1]))))

    def render(self, prompt_id: str, prompt_version: str) -> str:
        """Return the full prompt text: included prompts first, then the body.

        Includes are expanded depth-first in declared order, each once. A cycle
        raises ``PromptFormatError``.
        """
        parts: list[str] = []
        seen: set[tuple[str, str]] = set()
        self._expand((prompt_id, prompt_version), parts, seen, ())
        return "\n".join(parts)

    def _expand(
        self,
        ref: tuple[str, str],
        parts: list[str],
        seen: set[tuple[str, str]],
        stack: tuple[tuple[str, str], ...],
    ) -> None:
        if ref in stack:
            msg = f"include cycle at {ref[0]}@{ref[1]}"
            raise PromptFormatError(msg)
        if ref in seen:
            return
        prompt = self.load(*ref)
        for child in prompt.includes:
            self._expand(child, parts, seen, (*stack, ref))
        seen.add(ref)
        parts.append(prompt.body)


def glossary_pairs(glossary: Prompt) -> tuple[tuple[str, str], ...]:
    """Return the ``(term_en, term_fr)`` rows of a Glossary_Prompt's table."""
    pairs = []
    for line in glossary.body.splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != 2 or cells[0] == "term_en" or set(cells[0]) <= {"-", ":"}:
            continue
        pairs.append((cells[0], cells[1]))
    return tuple(pairs)
