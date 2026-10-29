"""The content-store port the idempotent ``content sync`` writes through (Req 14, 26).

``content sync`` (:mod:`edu_it_content.sync`) is the content pipeline's write
path into the catalog: it upserts authored items by their immutable
``stable_id``, archives removed items, and must never clobber learner completion
records (Req 14.1-14.4, 26.1). To keep that sync logic framework-free and
unit-testable — the same layering the domain follows
(``.kiro/steering/structure.md``) — the persistence it needs sits behind the
:class:`ContentStore` port declared here, with an in-memory fake
(:class:`In_Memory_ContentStore`) standing in for the PostgreSQL-backed catalog
in tests and local runs.

The port is a framework-free :class:`typing.Protocol` (``@runtime_checkable``,
standard-library + content-schema imports only), mirroring the domain
repository ports: the sync depends on the protocol, and infrastructure supplies
the real adapter later (task 20) while :class:`In_Memory_ContentStore` backs the
CLI and the tests now.

Stored shape. The store holds one :class:`StoredItem` per ``stable_id``: its
content type, the authored ``content_version`` hash, a :class:`ContentStatus`
(``ACTIVE`` or ``ARCHIVED``), and the canonical payload it was last synced from.
The payload is the item's content **excluding** the mutable provenance
review/version fields, so "the item's content changed" (Req 14.1) is a precise,
hashable comparison the sync and its idempotence check can rely on.

Completion records (``lesson_completion`` and the other completion rows, Req
14.3, 26.1) are modeled here only as an opaque, sync-untouched set: the store
exposes :meth:`completion_keys` so a test can assert the sync preserved them,
but no :class:`ContentStore` method ever writes or clears a completion. That is
the data-level statement of "existing completions are kept" — the sync has no
API by which it could remove one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable

__all__ = [
    "ContentStatus",
    "ContentStore",
    "In_Memory_ContentStore",
    "StoredItem",
]


class ContentStatus(StrEnum):
    """Whether a stored catalog item is live or archived (Req 14.4, 26.2).

    ``ACTIVE`` is a normally-synced, servable item; ``ARCHIVED`` is an item a
    later sync removed from the authored set. Removal transitions an item to
    ``ARCHIVED`` rather than hard-deleting it, so historical progress that
    references its ``stable_id`` stays valid (Req 14.4, `docs/DATA-MODEL.md` D3
    §10 item 5).
    """

    ACTIVE = "active"
    ARCHIVED = "archived"


@dataclass(frozen=True, slots=True)
class StoredItem:
    """One catalog item as the store holds it, keyed by ``stable_id`` (D3 §1.2, §6).

    Mirrors the ``stable_id`` + ``content_version`` identity model (D3 §1.2): a
    :attr:`stable_id` that never changes across edits and syncs, and a
    :attr:`content_version` hash the author bumps when the content changes. The
    :attr:`payload` is the item's canonical content (its schema dump without the
    mutable provenance review/version fields) so the sync can tell an unchanged
    re-sync from a real edit by comparing payloads, not version strings alone.
    :attr:`status` records whether the item is live or archived (Req 14.4).
    """

    stable_id: str
    """The item's immutable identity anchor (D3 §1.2)."""

    content_type: str
    """The catalog type key the item belongs to (``track``, ``lesson``, …)."""

    content_version: str
    """The authored hash that changes whenever the item's content changes."""

    status: ContentStatus
    """Whether the item is live (``ACTIVE``) or archived (``ARCHIVED``)."""

    payload: str
    """The item's canonical content used to detect a real edit (see module docstring)."""


@runtime_checkable
class ContentStore(Protocol):
    """Port the idempotent ``content sync`` writes the catalog through (Req 14, 26).

    Every item is addressed by its immutable ``stable_id`` (D3 §1.2); the store
    never exposes an internal numeric key. The contract is deliberately narrow:
    read the current item, list the live items (so the sync can find what an
    authored set removed), upsert an item in place, and archive a removed item.
    There is intentionally **no** delete and **no** completion-mutating method —
    an item is archived, never hard-deleted (Req 14.4), and completion records
    are preserved because the port offers no way to touch them (Req 14.3, 26.1).
    """

    def get(self, stable_id: str) -> StoredItem | None:
        """Return the stored item with ``stable_id``, or ``None`` when absent.

        Returns the item whatever its :attr:`StoredItem.status`, so the sync can
        re-activate an item that an earlier sync archived rather than inserting a
        duplicate under the same ``stable_id`` (Req 14.1).
        """
        ...

    def list_active_ids(self) -> frozenset[str]:
        """Return the ``stable_id`` of every currently ``ACTIVE`` item.

        The sync diffs this against the authored set to find items the author
        removed, so each can be archived rather than hard-deleted (Req 14.4).
        Archived items are excluded because they are already removed from the
        live catalog.
        """
        ...

    def upsert(self, item: StoredItem) -> None:
        """Insert ``item`` if new, or update the existing row for its ``stable_id``.

        Keyed by :attr:`StoredItem.stable_id`: a never-seen id inserts; a known
        id updates in place and never creates a duplicate (Req 14.1). When the
        upsert changes an item's content, resetting the non-completion learner
        state tied to that item (bookmarks, in-progress attempt scores, notes) is
        the production adapter's concern (Req 14.3); the sync signals a changed
        upsert so the adapter can do so, while completion records are left
        untouched by contract.
        """
        ...

    def archive(self, stable_id: str) -> None:
        """Transition the item with ``stable_id`` to ``ARCHIVED`` (Req 14.4, 26.2).

        Removal never hard-deletes: the row stays so progress referencing the
        ``stable_id`` keeps a valid target (D3 §10 item 5). Archiving an
        already-archived or unknown id is a no-op, keeping sync idempotent.
        """
        ...

    def has_active_sessions(self, stable_id: str) -> bool:
        """Return whether any in-flight session still references ``stable_id`` (Req 26.2).

        The sync consults this before archiving a removed item: while it is
        ``True`` the archive is deferred so in-flight learners keep a valid
        reference, and a later sync archives the item once the sessions complete.
        """
        ...

    def record_review(self, stable_id: str, reviewed_by: str, reviewed_at: datetime) -> None:
        """Record the approving reviewer in the item's provenance (Req 16.2).

        Called by the sync only on approval. Review is not part of the canonical
        payload, so recording it never counts as a content change.
        """
        ...

    def mark_golden(self, stable_ids: frozenset[str]) -> None:
        """Mark ``stable_ids`` as the ``Golden_Set`` for Phase 4 evals (Req 16.3).

        Called by the sync only on approval. Idempotent; never called on rejection.
        """
        ...


class In_Memory_ContentStore(ContentStore):
    """A :class:`ContentStore` backed by in-process dictionaries (Req 14, 26.1).

    Substitutable for the PostgreSQL-backed catalog without changing the sync
    logic: it satisfies the :class:`ContentStore` protocol and enforces the same
    invariants — one row per ``stable_id``, archive-not-delete on removal, and
    completion records the sync cannot touch.

    The fake adds two test-only seams that the protocol does not require:

    - :meth:`seed_completion` / :meth:`completion_keys` model opaque completion
      records (``lesson_completion`` and friends) so a test can plant a
      completion before a sync and assert it survived (Req 14.3, 26.1). The sync
      never calls these; only tests do.
    - :meth:`changed_upserts` records the ``stable_id`` of every upsert that
      actually changed stored content, so a test can assert a second run over
      unchanged content wrote nothing (Req 14.2) and that a real edit reset the
      item's non-completion learner state.
    """

    __test__ = False

    def __init__(self) -> None:
        """Create an empty content store."""
        self._items: dict[str, StoredItem] = {}
        self._completions: set[tuple[str, str]] = set()
        self._changed_upserts: list[str] = []
        # session_id -> (stable_id, pinned content_version, still active)
        self._sessions: dict[str, tuple[str, str, bool]] = {}
        # (stable_id, content_version) -> payload, so pinned versions stay readable
        self._versions: dict[tuple[str, str], str] = {}
        self._reviews: dict[str, tuple[str, datetime]] = {}
        self._golden: set[str] = set()

    def get(self, stable_id: str) -> StoredItem | None:
        """Return the stored item with ``stable_id`` (any status), or ``None``."""
        return self._items.get(stable_id)

    def list_active_ids(self) -> frozenset[str]:
        """Return the ``stable_id`` of every ``ACTIVE`` item."""
        return frozenset(
            stable_id
            for stable_id, item in self._items.items()
            if item.status is ContentStatus.ACTIVE
        )

    def upsert(self, item: StoredItem) -> None:
        """Insert or update ``item`` in place, recording a real content change.

        Keyed by ``stable_id`` so a known id never duplicates (Req 14.1). When
        the stored payload or version actually changes (or the item is new), the
        id is recorded in :meth:`changed_upserts` to stand in for the production
        adapter resetting the item's non-completion learner state (Req 14.3);
        completion records are never modified here.
        """
        existing = self._items.get(item.stable_id)
        if (
            existing is None
            or existing.payload != item.payload
            or (existing.content_version != item.content_version)
        ):
            self._changed_upserts.append(item.stable_id)
        self._items[item.stable_id] = item
        self._versions[(item.stable_id, item.content_version)] = item.payload

    def has_active_sessions(self, stable_id: str) -> bool:
        """Return whether an in-flight session references ``stable_id`` (Req 26.2)."""
        return any(sid == stable_id and active for sid, _, active in self._sessions.values())

    def archive(self, stable_id: str) -> None:
        """Mark the item with ``stable_id`` ``ARCHIVED``; no-op if absent/already archived."""
        existing = self._items.get(stable_id)
        if existing is None or existing.status is ContentStatus.ARCHIVED:
            return
        self._items[stable_id] = StoredItem(
            stable_id=existing.stable_id,
            content_type=existing.content_type,
            content_version=existing.content_version,
            status=ContentStatus.ARCHIVED,
            payload=existing.payload,
        )

    def record_review(self, stable_id: str, reviewed_by: str, reviewed_at: datetime) -> None:
        """Record the approving reviewer for ``stable_id`` (Req 16.2)."""
        self._reviews[stable_id] = (reviewed_by, reviewed_at)

    def mark_golden(self, stable_ids: frozenset[str]) -> None:
        """Add ``stable_ids`` to the Golden_Set (Req 16.3)."""
        self._golden |= stable_ids

    # -- Test-only seams (not part of the ContentStore protocol) --------------

    def review_of(self, stable_id: str) -> tuple[str, datetime] | None:
        """Return ``(reviewed_by, reviewed_at)`` recorded for an item, or ``None``."""
        return self._reviews.get(stable_id)

    def golden_ids(self) -> frozenset[str]:
        """Return the ``stable_id`` of every item in the Golden_Set (test read)."""
        return frozenset(self._golden)

    def seed_completion(self, item_stable_id: str, completion_id: str) -> None:
        """Plant a completion record tied to ``item_stable_id`` (test fixture).

        Models a ``lesson_completion`` (or similar) row already written against
        an item, so a test can assert the sync preserves it across an edit or
        archive (Req 14.3, 26.1). The sync has no way to remove it.
        """
        self._completions.add((item_stable_id, completion_id))

    def completion_keys(self) -> frozenset[tuple[str, str]]:
        """Return every ``(item_stable_id, completion_id)`` completion record (test read)."""
        return frozenset(self._completions)

    def start_session(self, session_id: str, stable_id: str) -> str:
        """Start an in-flight session pinned to the item's current version (test fixture).

        Returns the pinned ``content_version``; later syncs never alter it
        (Req 26.3, 25.1).
        """
        item = self._items[stable_id]
        self._sessions[session_id] = (stable_id, item.content_version, True)
        return item.content_version

    def complete_session(self, session_id: str) -> None:
        """Mark a session complete so it no longer blocks archiving (test fixture)."""
        stable_id, version, _ = self._sessions[session_id]
        self._sessions[session_id] = (stable_id, version, False)

    def session_pinned_version(self, session_id: str) -> str:
        """Return the ``content_version`` a session started under (test read)."""
        return self._sessions[session_id][1]

    def payload_at_version(self, stable_id: str, content_version: str) -> str | None:
        """Return the payload stored for a past ``content_version`` (test read)."""
        return self._versions.get((stable_id, content_version))

    def changed_upserts(self) -> tuple[str, ...]:
        """Return the ``stable_id`` of each upsert that changed stored content, in order."""
        return tuple(self._changed_upserts)
