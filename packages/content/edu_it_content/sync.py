"""The framework-free core of ``edu-it content sync`` (Req 14.1-14.4, 26.1).

``content sync`` is the content pipeline's idempotent write into the catalog.
This module holds its logic as a pure function over a :class:`ContentStore`
port, so it is unit-testable with the in-memory fake and carries no CLI or
framework dependency (``.kiro/steering/structure.md``). The CLI
(:mod:`edu_it_content.cli`) is the thin entry point that loads a content root,
constructs a store, and calls :func:`sync_content`.

What a sync does (design "``edu-it content sync``", D3 §10):

1. **Upsert by ``stable_id``** — every authored item is matched to the store by
   its immutable ``stable_id``. A never-seen id is inserted; a known id updates
   the existing row in place. A duplicate is never created (Req 14.1). An item
   an earlier sync archived is re-activated on the same ``stable_id`` rather
   than duplicated.
2. **Bump ``content_version`` only on a real change** — "changed" is decided by
   comparing the item's *canonical payload* (its schema dump minus the mutable
   provenance review/version fields) against what the store holds, so re-syncing
   the identical item is recognized as unchanged regardless of its version
   string (Req 14.1).
3. **No-op on an unchanged second run** — an item whose canonical payload and
   version already match the store is left untouched; a second sync over
   unchanged content writes nothing (Req 14.2). This is the precise statement
   Property 21 (task 11.2) asserts.
4. **Preserve completions** — the sync never touches completion records; a
   changed upsert resets only the *other* learner state tied to the item
   (bookmarks, in-progress attempt scores, notes), which the store adapter does
   on a changed upsert (Req 14.3, 26.1).
5. **Archive removed items** — an item the store has as ``ACTIVE`` but the
   authored set no longer contains is transitioned to ``ARCHIVED``, never
   hard-deleted, so progress referencing its ``stable_id`` stays valid
   (Req 14.4, 26.2). If active sessions still reference the removed item, the
   archive is **deferred** (the item stays ``ACTIVE``, reported in
   ``SyncReport.deferred``) and a later sync archives it once they complete.
   In-flight attempts stay pinned to the ``content_version`` they started under
   (Req 26.3).

The canonical payload excludes exactly the provenance fields that may change
without the content itself changing — ``content_version`` (identity, compared
separately) and the review trail ``reviewed_by`` / ``reviewed_at`` (set at
approval, task 16, not at authoring). Every other field, including the rest of
provenance, is part of the content and a change to it is a real edit.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime

from pydantic import BaseModel

from edu_it_content.content_store import ContentStatus, ContentStore, StoredItem
from edu_it_content.schemas import Provenance
from edu_it_content.validation import (
    _SCHEMA_BY_TYPE,
    LoadedContent,
    ValidationFailure,
    validate_content,
)

__all__ = [
    "Approval",
    "SyncReport",
    "sync_content",
]

#: Provenance fields excluded from an item's canonical payload: ``content_version``
#: is the identity hash (compared on its own) and the review trail is attached at
#: approval time, not authoring, so neither counts as a content change (D3 §1.2).
_NON_CONTENT_PROVENANCE_FIELDS: frozenset[str] = frozenset(
    {"content_version", "reviewed_by", "reviewed_at"}
)


@dataclass(frozen=True, slots=True)
class SyncReport:
    """The outcome of one :func:`sync_content` run, for CLI output and tests.

    Partitions the authored + stored items into what the sync did: items
    :attr:`inserted` for the first time, items :attr:`updated` in place because
    their content changed, items :attr:`unchanged` (left untouched — the no-op
    path, Req 14.2), and items :attr:`archived` because the authored set removed
    them (Req 14.4). Each field holds the affected ``stable_id`` values. A fully
    idempotent re-run has empty :attr:`inserted`, :attr:`updated`, and
    :attr:`archived`.
    """

    inserted: tuple[str, ...]
    updated: tuple[str, ...]
    unchanged: tuple[str, ...]
    archived: tuple[str, ...]
    deferred: tuple[str, ...] = ()
    """Removed items whose archive was deferred because sessions still reference
    them (Req 26.2); they stay ``ACTIVE`` and are archived by a later sync."""

    @property
    def changed(self) -> bool:
        """Whether the run changed anything (nothing inserted, updated, or archived)."""
        return bool(self.inserted or self.updated or self.archived)

    def summary(self) -> str:
        """Render a one-line human summary of the counts for CLI output."""
        return (
            f"{len(self.inserted)} inserted, {len(self.updated)} updated, "
            f"{len(self.unchanged)} unchanged, {len(self.archived)} archived"
            + (f", {len(self.deferred)} deferred" if self.deferred else "")
        )


@dataclass(frozen=True, slots=True)
class Approval:
    """An expert's approval of the seed content (Req 16.2, 16.3).

    :attr:`reviewer` is the approving user's stable id, supplied explicitly by
    the caller (never inferred); :attr:`reviewed_at` is when they approved. Absence
    of an :class:`Approval` is the rejection / not-yet-approved path (Req 16.4).
    """

    reviewer: str
    reviewed_at: datetime

    def __post_init__(self) -> None:
        """Reject a blank reviewer so an approval always attributes someone."""
        if not self.reviewer.strip():
            raise ValueError("approval requires a non-empty reviewer")


def _stamp_review(content: LoadedContent, approval: Approval) -> LoadedContent:
    """Return ``content`` with the approver recorded in every item's provenance."""
    stamped: dict[str, list[Mapping[str, object]]] = {}
    for type_key, items in content.raw_items.items():
        out: list[Mapping[str, object]] = []
        for raw in items:
            provenance = raw.get("provenance")
            if isinstance(provenance, Mapping):
                raw = {
                    **raw,
                    "provenance": {
                        **provenance,
                        "reviewed_by": approval.reviewer,
                        "reviewed_at": approval.reviewed_at.isoformat(),
                    },
                }
            out.append(raw)
        stamped[type_key] = out
    return replace(content, raw_items=stamped)


def sync_content(
    store: ContentStore, content: LoadedContent, *, approval: Approval | None = None
) -> SyncReport:
    """Idempotently upsert ``content`` into ``store`` keyed by ``stable_id``.

    Validates ``content`` first (reusing the ordered ``content validate`` checks)
    and raises :class:`ValueError` if it is invalid, so a sync never writes a
    malformed catalog. Then, for every authored item: inserts a new
    ``stable_id``, updates a changed one in place (bumping ``content_version``),
    or leaves an unchanged one untouched (Req 14.1, 14.2). Finally, every item
    the store has ``ACTIVE`` that the authored set no longer contains is archived
    rather than deleted (Req 14.4). Completion records are never touched
    (Req 14.3, 26.1).

    Returns a :class:`SyncReport` partitioning the ``stable_id`` values by what
    happened, so the CLI can report the run and a test can assert idempotence and
    completion preservation (Property 21, task 11.2).
    """
    if approval is not None:
        content = _stamp_review(content, approval)

    failures = validate_content(content)
    if failures:
        raise ValueError(_format_validation_failures(failures))

    authored = _canonicalize(content)

    inserted: list[str] = []
    updated: list[str] = []
    unchanged: list[str] = []

    for stable_id, (content_type, content_version, payload) in authored.items():
        new_item = StoredItem(
            stable_id=stable_id,
            content_type=content_type,
            content_version=content_version,
            status=ContentStatus.ACTIVE,
            payload=payload,
        )
        existing = store.get(stable_id)
        if existing is None:
            store.upsert(new_item)
            inserted.append(stable_id)
        elif _is_unchanged(existing, new_item):
            unchanged.append(stable_id)
        else:
            store.upsert(new_item)
            updated.append(stable_id)

    authored_ids = frozenset(authored)
    removed = [stable_id for stable_id in store.list_active_ids() if stable_id not in authored_ids]
    archived: list[str] = []
    deferred: list[str] = []
    for stable_id in removed:
        # Defer while in-flight sessions reference the item (Req 26.2); it stays
        # ACTIVE and a later sync archives it once they complete. Never deleted.
        if store.has_active_sessions(stable_id):
            deferred.append(stable_id)
        else:
            store.archive(stable_id)
            archived.append(stable_id)

    if approval is not None:
        reviewed_ids = {
            str(provenance["stable_id"])
            for items in content.raw_items.values()
            for raw in items
            if isinstance(provenance := raw.get("provenance"), Mapping)
        }
        # Approval only: attribute the reviewer in provenance (Req 16.2) and mark
        # the approved content as the Golden_Set (Req 16.3). Without an approval
        # neither happens (Req 16.4).
        for stable_id in authored:
            if stable_id in reviewed_ids:
                store.record_review(stable_id, approval.reviewer, approval.reviewed_at)
        store.mark_golden(frozenset(authored))

    return SyncReport(
        inserted=tuple(inserted),
        updated=tuple(updated),
        unchanged=tuple(unchanged),
        archived=tuple(sorted(archived)),
        deferred=tuple(sorted(deferred)),
    )


def _is_unchanged(existing: StoredItem, incoming: StoredItem) -> bool:
    """Return whether ``incoming`` matches ``existing`` in content and version.

    An item already ``ACTIVE`` whose canonical payload and ``content_version``
    both match the store is unchanged, so a re-sync leaves it untouched (Req
    14.2). An archived item is treated as changed so a re-authored id is
    re-activated rather than skipped.
    """
    return (
        existing.status is ContentStatus.ACTIVE
        and existing.payload == incoming.payload
        and existing.content_version == incoming.content_version
    )


def _canonicalize(content: LoadedContent) -> dict[str, tuple[str, str, str]]:
    """Index authored items by ``stable_id`` → (content_type, content_version, payload).

    Parses each raw item against its schema (via the shared validate pass, so
    this runs only on already-valid content), then derives its identity
    ``content_version`` and its canonical payload — the schema dump with the
    non-content provenance fields removed — so the sync's change detection is a
    stable, order-independent comparison. Raises :class:`ValueError` on a
    duplicate ``stable_id`` within one authored set, which a sync must not accept.
    """
    indexed: dict[str, tuple[str, str, str]] = {}
    for type_key, items in content.raw_items.items():
        model = _SCHEMA_BY_TYPE.get(type_key)
        if model is None:  # pragma: no cover - validate_content already rejected this
            continue
        for raw in items:
            parsed = model.model_validate(raw)
            stable_id, content_version = _identity(parsed)
            if stable_id in indexed:
                raise ValueError(f"duplicate stable_id {stable_id!r} in authored content")
            indexed[stable_id] = (type_key, content_version, _payload(parsed))
    return indexed


def _identity(item: BaseModel) -> tuple[str, str]:
    """Return an item's ``(stable_id, content_version)`` from top-level or provenance.

    Provenanced items (``track``, ``lesson`` …) carry both on their
    ``provenance`` record; sub-items (``lab_task`` …) carry a top-level
    ``stable_id`` and no version of their own, so their payload hash stands in as
    the version.
    """
    provenance = getattr(item, "provenance", None)
    if isinstance(provenance, Provenance):
        return provenance.stable_id, provenance.content_version
    stable_id = str(getattr(item, "stable_id", ""))
    return stable_id, _payload(item)


def _payload(item: BaseModel) -> str:
    """Render an item's canonical content as a stable JSON string.

    Dumps the model in JSON mode (so datetimes/enums serialize deterministically)
    with sorted keys, then strips the non-content provenance fields
    (:data:`_NON_CONTENT_PROVENANCE_FIELDS`) so a changed ``content_version`` or a
    newly-attached reviewer is not mistaken for a content edit (D3 §1.2).
    """
    dumped = item.model_dump(mode="json")
    provenance = dumped.get("provenance")
    if isinstance(provenance, dict):
        dumped["provenance"] = {
            key: value
            for key, value in provenance.items()
            if key not in _NON_CONTENT_PROVENANCE_FIELDS
        }
    return json.dumps(dumped, sort_keys=True, ensure_ascii=False)


def _format_validation_failures(failures: Sequence[ValidationFailure]) -> str:
    """Render validation failures into a single message for the raised error."""
    lines = [failure.message() for failure in failures]
    joined = "; ".join(lines)
    return f"cannot sync invalid content ({len(failures)} failure(s)): {joined}"
