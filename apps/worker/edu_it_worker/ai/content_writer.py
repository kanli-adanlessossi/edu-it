"""Content writer for the Generator (ai-content Req 5.8, 5.10, 5.5).

Turns the items of one complete Generation_Unit into repository files:

- sorted, stable JSON (sorted keys, two-space indent, UTF-8, LF, one trailing
  newline, list items ordered by ``stable_id``) so diffs stay reviewable;
- the FR and EN MDX bodies of a lesson, written in the same call. A body in
  only one locale raises :class:`IncompleteUnitError`, so a half unit never
  reaches a commit (Req 5.10, 12.7).

The writer is pure: it returns ``{path: bytes}`` and touches no file or ref.
The Generator commits the whole mapping in one commit.

Provenance is stamped on every item (Req 5.8): ``origin`` is ``ai``;
``reviewed_by`` and ``reviewed_at`` are absent; ``content_version`` is a
SHA-256 of the canonical content (without the provenance block); the
generation date comes from the injected ``Clock``; the ``stable_id`` follows
the seed rule ``<kind>.<slug>`` and is made unique with the ``IdGenerator``.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC
from typing import Any, Final

from edu_it_domain.ai.generation import UNIT_LOCALES
from edu_it_domain.ports.clock import Clock
from edu_it_domain.ports.id_generator import IdGenerator

from edu_it_worker.ai.stages import content_dir, mdx_path

__all__ = [
    "ContentWriter",
    "GenerationInfo",
    "IncompleteUnitError",
    "canonical_json",
    "content_version",
    "slugify",
]

_SLUG_RE: Final = re.compile(r"[^a-z0-9]+")
_KIND_RE: Final = re.compile(r"^[a-z][a-z0-9_]*$")


class IncompleteUnitError(ValueError):
    """A Generation_Unit lacks a locale and must be discarded, not committed."""


@dataclass(frozen=True, slots=True)
class GenerationInfo:
    """The generation trail written into every item's Provenance."""

    model: str
    prompt_id: str
    prompt_version: str


def canonical_json(value: object) -> str:
    """Return sorted, stable JSON text ending in one newline."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def content_version(content: object) -> str:
    """Return ``sha256:<hex>`` of the canonical JSON of ``content``."""
    digest = hashlib.sha256(canonical_json(content).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def slugify(text: str) -> str:
    """Return a lowercase ASCII slug (``a-z``, ``0-9``, ``-``), possibly empty."""
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return _SLUG_RE.sub("-", ascii_text.lower()).strip("-")


def _identity(item: Mapping[str, Any]) -> str:
    prov = item.get("provenance")
    raw = prov.get("stable_id") if isinstance(prov, Mapping) else item.get("stable_id")
    if not isinstance(raw, str) or not raw:
        msg = "item has no stable_id"
        raise ValueError(msg)
    return raw


def _merge_items(
    existing_text: str | None, new_items: Sequence[Mapping[str, Any]]
) -> list[Mapping[str, Any]]:
    current: dict[str, Mapping[str, Any]] = {}
    if existing_text:
        loaded = json.loads(existing_text)
        if not isinstance(loaded, list):
            msg = "existing content file is not a JSON list"
            raise ValueError(msg)
        current = {_identity(item): item for item in loaded}
    current.update({_identity(item): item for item in new_items})
    return [current[key] for key in sorted(current)]


class ContentWriter:
    """Builds the files of a complete Generation_Unit."""

    def __init__(self, clock: Clock, ids: IdGenerator) -> None:
        self._clock = clock
        self._ids = ids

    def stable_id(self, kind: str, title_en: str, taken: Collection[str] = ()) -> str:
        """Mint ``<kind>.<slug>``, unique among ``taken``, using the ``IdGenerator``.

        The slug comes from the English title. The ``IdGenerator`` supplies the
        whole slug when the title has no usable characters, and a short suffix
        when the plain id is already taken.
        """
        if not _KIND_RE.fullmatch(kind):
            msg = f"invalid kind: {kind!r}"
            raise ValueError(msg)
        slug = slugify(title_en)
        if not slug:
            slug = slugify(self._ids.new_id()) or "item"
        candidate = f"{kind}.{slug}"
        while candidate in taken:
            suffix = slugify(self._ids.new_id()) or "x"
            candidate = f"{kind}.{slug}-{suffix}"
        return candidate

    def stamp(
        self,
        item: Mapping[str, Any],
        *,
        stable_id: str,
        info: GenerationInfo,
        bodies: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        """Return ``item`` with a fresh AI Provenance block (Req 5.8).

        ``bodies`` (locale to MDX text) takes part in ``content_version`` so a
        change to a lesson body changes the version. Any provenance already on
        ``item`` is replaced; ``reviewed_by`` and ``reviewed_at`` are never set.
        """
        content = {k: v for k, v in item.items() if k != "provenance"}
        hashed: dict[str, Any] = {"item": content}
        if bodies:
            hashed["bodies"] = dict(bodies)
        generated = self._clock.now().astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        provenance = {
            "stable_id": stable_id,
            "content_version": content_version(hashed),
            "origin": "ai",
            "model": info.model,
            "prompt_id": info.prompt_id,
            "prompt_version": info.prompt_version,
            "generation_date": generated,
        }
        return {**content, "provenance": provenance}

    def render(
        self,
        track: str,
        *,
        lists: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
        documents: Mapping[str, Mapping[str, Any]] | None = None,
        bodies: Mapping[str, Mapping[str, str]] | None = None,
        existing: Mapping[str, str] | None = None,
    ) -> dict[str, bytes]:
        """Return ``{repository path: bytes}`` for one complete unit.

        ``lists`` maps a file name (``lessons.json``) to the new items, merged
        into ``existing`` (path to current text) by ``stable_id``. ``documents``
        maps a file name to a single JSON object (the brief). ``bodies`` maps a
        lesson ``stable_id`` to ``{locale: MDX text}`` and needs every locale.
        """
        base = content_dir(track)
        present = existing or {}
        files: dict[str, bytes] = {}

        for lesson, by_locale in sorted((bodies or {}).items()):
            missing = [loc for loc in UNIT_LOCALES if not by_locale.get(loc, "").strip()]
            if missing:
                msg = f"unit {lesson} lacks locale(s): {', '.join(missing)}"
                raise IncompleteUnitError(msg)
            for locale in UNIT_LOCALES:
                text = by_locale[locale].replace("\r\n", "\n").rstrip("\n") + "\n"
                files[mdx_path(track, lesson, locale)] = text.encode("utf-8")

        for name, items in sorted((lists or {}).items()):
            path = f"{base}/{name}"
            merged = _merge_items(present.get(path), items)
            files[path] = canonical_json(merged).encode("utf-8")

        for name, document in sorted((documents or {}).items()):
            files[f"{base}/{name}"] = canonical_json(document).encode("utf-8")

        return files
