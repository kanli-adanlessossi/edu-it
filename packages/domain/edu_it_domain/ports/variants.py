"""The ``VariantRepository`` port (ai-content Req 13.3, 15.4, 15.5, 15.10, 23.4).

A variant is a generated alternative to a parent question. Its reviewable text
lives in ``variant_revision`` rows keyed by ``(variant, content_version)``; the
variant row names the revision the serving code uses
(``approved_content_version``). A reviewer edit of an already approved or served
variant adds a revision without changing the variant state (Req 16).

Everything is addressed by ``stable_id`` (and ``content_version`` for revisions);
internal keys never cross this port. Neither table holds personal data;
``reviewed_by`` is a reviewer's ``stable_id``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from edu_it_domain.ai.lifecycle import VariantState

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

__all__ = [
    "NewRevision",
    "NewVariant",
    "RevisionState",
    "Variant",
    "VariantRepository",
    "VariantRevision",
]


class RevisionState(StrEnum):
    """State of one variant revision."""

    PENDING_REVIEW = "PendingReview"
    APPROVED = "Approved"
    REJECTED = "Rejected"


@dataclass(frozen=True, slots=True)
class NewVariant:
    """Fields needed to store a variant. ``created_at`` comes from the clock."""

    stable_id: str
    parent_question_stable_id: str
    track_stable_id: str
    state: VariantState = VariantState.GENERATED
    generation_job_stable_id: str | None = None


@dataclass(frozen=True, slots=True)
class Variant:
    """A stored variant."""

    stable_id: str
    parent_question_stable_id: str
    track_stable_id: str
    state: VariantState
    created_at: datetime
    approved_content_version: str | None = None
    withdrawn_reason: str | None = None
    generation_job_stable_id: str | None = None


@dataclass(frozen=True, slots=True)
class NewRevision:
    """Fields needed to store a revision. ``created_at`` comes from the clock."""

    variant_stable_id: str
    content_version: str
    body_json: str
    state: RevisionState = RevisionState.PENDING_REVIEW
    origin: str | None = None
    model: str | None = None
    prompt_id: str | None = None
    prompt_version: str | None = None
    generation_date: datetime | None = None
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None
    findings_json: str | None = None


@dataclass(frozen=True, slots=True)
class VariantRevision:
    """A stored revision."""

    variant_stable_id: str
    content_version: str
    body_json: str
    state: RevisionState
    created_at: datetime
    origin: str | None = None
    model: str | None = None
    prompt_id: str | None = None
    prompt_version: str | None = None
    generation_date: datetime | None = None
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None
    findings_json: str | None = None


@runtime_checkable
class VariantRepository(Protocol):
    """Port storing variants and their revisions."""

    def add(self, variant: NewVariant) -> None:
        """Store a variant. Raises ``ValueError`` for a duplicate ``stable_id``; nothing is
        written in that case. The SQL adapter also rejects an unknown parent question or job."""
        ...

    def get(self, stable_id: str) -> Variant | None:
        """Return the variant, or ``None`` when unknown."""
        ...

    def save(self, variant: Variant) -> None:
        """Store the state, ``approved_content_version`` and ``withdrawn_reason``.

        A changed state must be allowed by ``lifecycle.TRANSITIONS``, otherwise
        ``InvalidTransitionError`` is raised and nothing is written. Raises
        ``ValueError`` for an unknown variant.
        """
        ...

    def count_approved(self, parent_question_stable_id: str) -> int:
        """Number of variants of the parent in a servable state (Approved or Served)."""
        ...

    def parents_below(self, threshold: int) -> Sequence[str]:
        """Parents known to the repository (at least one variant of any state)
        with fewer than ``threshold`` servable variants, sorted by ``stable_id``."""
        ...

    def list_servable(self, parent_question_stable_id: str) -> Sequence[VariantRevision]:
        """The revision named by ``approved_content_version`` of every servable
        variant of the parent, sorted by variant ``stable_id``. A pending edit is
        never returned (Req 16.1)."""
        ...

    def add_revision(self, revision: NewRevision) -> None:
        """Store a revision. Raises ``ValueError`` for an unknown variant or when
        ``(variant, content_version)`` already exists; nothing is written. The SQL adapter
        also rejects an unknown reviewer."""
        ...

    def get_revision(self, variant_stable_id: str, content_version: str) -> VariantRevision | None:
        """Return one revision, or ``None`` when unknown."""
        ...
