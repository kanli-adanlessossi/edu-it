"""Small helpers shared by the learning use cases (internal)."""

from __future__ import annotations

from typing import Final

from edu_it_domain.learning.models import (
    Mastery,
    MasteryState,
    ReviewSchedule,
    StableId,
)
from edu_it_domain.learning.review import schedule_on_practiced
from edu_it_domain.ports.clock import Clock
from edu_it_domain.ports.mastery import MasteryRepository
from edu_it_domain.ports.review import ReviewRepository

#: The states at or above Practiced -- the ones that belong in spaced review
#: (Req 5.4, 9.1). Mirrors the band order of :class:`MasteryState`.
_REVIEWABLE_STATES: Final[frozenset[MasteryState]] = frozenset(
    {MasteryState.PRACTICED, MasteryState.PROFICIENT, MasteryState.MASTERED}
)


def load_or_start_mastery(
    repo: MasteryRepository,
    user_id: StableId,
    concept_id: StableId,
) -> Mastery:
    """Return the learner's mastery, or a fresh Introduced one on first encounter (Req 5.2)."""
    existing = repo.get(user_id, concept_id)
    if existing is not None:
        return existing
    return Mastery(
        user_id=user_id,
        concept_id=concept_id,
        score=0,
        state=MasteryState.INTRODUCED,
    )


def has_passed_review(schedule: ReviewSchedule | None) -> bool:
    """Whether any review attempt on ``schedule`` passed -- a Mastered-gate input (Req 5.6)."""
    return schedule is not None and any(attempt.passed for attempt in schedule.history)


def ensure_review_scheduled(
    reviews: ReviewRepository,
    mastery: Mastery,
    *,
    clock: Clock,
) -> None:
    """Create the single review entry once ``mastery`` reaches Practiced (Req 5.4, 9.1, 9.2).

    A no-op below Practiced, and a no-op when an entry already exists -- the
    existing entry resumes rather than being reset or duplicated.
    """
    if mastery.state not in _REVIEWABLE_STATES:
        return
    if reviews.get(mastery.user_id, mastery.concept_id) is not None:
        return
    reviews.save(
        schedule_on_practiced(
            None,
            user_id=mastery.user_id,
            concept_id=mastery.concept_id,
            clock=clock,
        )
    )
