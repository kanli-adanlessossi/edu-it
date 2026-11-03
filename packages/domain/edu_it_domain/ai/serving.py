"""Approved-only serving rules (ai-content Requirements 14.1 to 14.3, 20.1, 20.3 to 20.5).

Pure functions. They decide which question a spaced review shows: an Approved
variant of the parent question, else the parent itself, else another servable
question of the concept, else nothing (``NoServableQuestionError``). Nothing here
touches a port, the clock or an ``LLMProvider``: the serving code has no model
dependency at all (Requirement 14.3), so no learner action can trigger a model call.

A served variant is presented under its parent's ``stable_id`` (decision D4), so
the 24 hour anti-farming rule and mastery keep working per question. The pick is
a hash of ``(user, parent, due_at)`` over the stable-sorted pool, so start and
submit agree without session state; ``variant_ref`` additionally pins an
in-flight review to the variant it started with.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from edu_it_domain.ai.lifecycle import is_servable
from edu_it_domain.learning.models import QuestionOption
from edu_it_domain.ports.catalog import QuestionRecord
from edu_it_domain.ports.item_flags import ItemKey, ItemKind
from edu_it_domain.ports.variants import RevisionState

if TYPE_CHECKING:
    from collections.abc import Collection, Sequence
    from datetime import datetime

    from edu_it_domain.ports.variants import Variant, VariantRevision

__all__ = [
    "NoServableQuestionError",
    "ResolvedQuestion",
    "ServableVariant",
    "make_variant_ref",
    "pick_variant",
    "resolve_question",
    "servable_variants",
    "split_variant_ref",
]

_REF_SEPARATOR = "@"


class NoServableQuestionError(LookupError):
    """The concept has no servable question (Requirements 20.4, 20.5)."""

    def __init__(self, concept_id: str) -> None:
        super().__init__(f"no servable question for concept {concept_id}")
        self.concept_id = concept_id


@dataclass(frozen=True, slots=True)
class ServableVariant:
    """An Approved variant revision, shaped as the question it is served as."""

    variant_stable_id: str
    content_version: str
    question: QuestionRecord
    """Under the parent's ``stable_id`` with the variant's options (decision D4)."""

    @property
    def ref(self) -> str:
        return make_variant_ref(self.variant_stable_id, self.content_version)


@dataclass(frozen=True, slots=True)
class ResolvedQuestion:
    """The question a review shows. ``variant_ref`` is set only for a variant."""

    question: QuestionRecord
    variant_ref: str | None = None


def make_variant_ref(variant_stable_id: str, content_version: str) -> str:
    """The opaque reference echoed by the submit request."""
    return f"{variant_stable_id}{_REF_SEPARATOR}{content_version}"


def split_variant_ref(variant_ref: str) -> tuple[str, str] | None:
    """Split a reference into ``(variant_stable_id, content_version)``; ``None`` if malformed."""
    variant_id, sep, version = variant_ref.rpartition(_REF_SEPARATOR)
    if not sep or not variant_id or not version:
        return None
    return variant_id, version


def _as_question(parent: QuestionRecord, revision: VariantRevision) -> QuestionRecord | None:
    """Build the served question from a revision body; ``None`` when unusable."""
    try:
        body = json.loads(revision.body_json)
        raw_options = body["options"]
        options = tuple(
            QuestionOption(option_id=index, is_correct=bool(option["is_correct"]))
            for index, option in enumerate(raw_options)
        )
    except (ValueError, KeyError, TypeError):
        return None
    if len(options) < 2 or not any(option.is_correct for option in options):
        return None
    return QuestionRecord(
        stable_id=parent.stable_id,
        options=options,
        concept_ids=parent.concept_ids,
    )


def servable_variants(
    parent: QuestionRecord,
    candidates: Sequence[tuple[Variant, VariantRevision]],
    flagged: Collection[ItemKey],
) -> tuple[ServableVariant, ...]:
    """The variants of ``parent`` that may be served, sorted by variant ``stable_id``.

    A candidate is kept when its variant is Approved or Served, belongs to the
    parent, its revision is Approved and the variant has no active flag
    (Requirements 14.1, 14.2). A pending edit is never a candidate (Requirement 16.1).
    """
    pool: list[ServableVariant] = []
    for variant, revision in candidates:
        if variant.parent_question_stable_id != parent.stable_id:
            continue
        if not is_servable(variant.state) or revision.state is not RevisionState.APPROVED:
            continue
        if ItemKey(ItemKind.VARIANT, variant.stable_id) in flagged:
            continue
        question = _as_question(parent, revision)
        if question is None:
            continue
        pool.append(
            ServableVariant(
                variant_stable_id=variant.stable_id,
                content_version=revision.content_version,
                question=question,
            ),
        )
    return tuple(sorted(pool, key=lambda item: item.variant_stable_id))


def _index(user_ref: str, parent_stable_id: str, due_at: datetime, size: int) -> int:
    seed = "\x1f".join((user_ref, parent_stable_id, due_at.isoformat()))
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % size


def pick_variant(
    pool: Sequence[ServableVariant],
    user_ref: str,
    parent_stable_id: str,
    due_at: datetime,
) -> ServableVariant | None:
    """Deterministically pick from the stable-sorted pool; ``None`` when empty."""
    if not pool:
        return None
    ordered = sorted(pool, key=lambda item: item.variant_stable_id)
    return ordered[_index(user_ref, parent_stable_id, due_at, len(ordered))]


def resolve_question(
    *,
    concept_id: str,
    parent: QuestionRecord | None,
    parent_flagged: bool,
    pool: Sequence[ServableVariant],
    alternatives: Sequence[QuestionRecord],
    user_ref: str,
    due_at: datetime,
    pinned: ServableVariant | None = None,
) -> ResolvedQuestion:
    """Choose the question a review shows.

    Order: a variant of the parent (the ``pinned`` one when the review already
    started with it, else the deterministic pick), then the parent, then another
    servable question of the concept, else ``NoServableQuestionError``. A flagged
    or missing parent serves neither itself nor its variants (Requirement 20.3).
    ``alternatives`` are servable questions of the concept already free of flags.
    """
    if parent is not None and not parent_flagged:
        chosen = (
            pinned if pinned is not None else pick_variant(pool, user_ref, parent.stable_id, due_at)
        )
        if chosen is not None:
            return ResolvedQuestion(chosen.question, chosen.ref)
        return ResolvedQuestion(parent)
    others = sorted(
        (q for q in alternatives if parent is None or q.stable_id != parent.stable_id),
        key=lambda q: q.stable_id,
    )
    if not others:
        raise NoServableQuestionError(concept_id)
    seed_parent = parent.stable_id if parent is not None else concept_id
    return ResolvedQuestion(others[_index(user_ref, seed_parent, due_at, len(others))])
