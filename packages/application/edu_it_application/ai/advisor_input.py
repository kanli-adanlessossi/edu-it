"""Eligible items and the ``AdvisorInput`` builder for the path advisor.

ai-content Requirements 17.1, 17.3 and 17.4.

``EligibleItems.for_learner`` returns the concepts a learner may be recommended:
the concept exists in the catalog, is servable (it has at least one question that
is not flagged, read through the ``ServableCatalog`` decorator) and is unlocked
(every concept it requires has reached the Practiced band). The advisor works on
concepts, the unit of mastery, review schedule and prerequisites.

``build_advisor_input`` turns the learner's state into an ``LLMInput`` of kind
``ADVISOR``. The learner is referenced only by a per-run Pseudonymous_Handle;
the input holds bands, states and concept ids, never a user id, name or email.
The mapping from handle to learner stays with the caller for the duration of the
run and is never part of the input.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from edu_it_domain import config as domain_config
from edu_it_domain.ai.inputs import InputKind
from edu_it_domain.ai.pseudonym import handle as make_handle
from edu_it_domain.learning.models import MasteryState
from edu_it_domain.learning.unlocking import prerequisite_met
from edu_it_domain.ports.llm_provider import LLMInput, PromptRef

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping

    from edu_it_domain.learning.models import Mastery, ReviewSchedule, StableId
    from edu_it_domain.ports.catalog import CatalogRepository
    from edu_it_domain.ports.clock import Clock
    from edu_it_domain.ports.mastery import MasteryRepository
    from edu_it_domain.ports.review import ReviewRepository

__all__ = [
    "ADVISOR_PROMPT",
    "AdvisorInput",
    "AdvisorOrderSchema",
    "EligibleItems",
    "build_advisor_input",
]

#: The pinned advisor prompt (``prompts/advisor__v1.md``).
ADVISOR_PROMPT: Final = PromptRef("advisor", "1")

_COMPLETED: Final = frozenset({MasteryState.PROFICIENT, MasteryState.MASTERED})


class EligibleItems:
    """Which concepts a learner may be recommended (Requirement 17.3)."""

    def __init__(
        self,
        catalog: CatalogRepository,
        mastery: MasteryRepository,
    ) -> None:
        """``catalog`` should be the ``ServableCatalog`` so flagged questions do not count."""
        self._catalog = catalog
        self._mastery = mastery

    def for_learner(
        self,
        user_id: StableId,
        concept_ids: Collection[StableId],
    ) -> tuple[StableId, ...]:
        """The sorted subset of ``concept_ids`` that exists, is servable and is unlocked."""
        tracked = {m.concept_id: m for m in self._mastery.list_for_user(user_id)}
        eligible: list[StableId] = []
        for concept_id in sorted(set(concept_ids)):
            concept = self._catalog.get_concept(concept_id)
            if concept is None:
                continue
            if not self._catalog.list_questions_for_concept(concept_id):
                continue
            if not all(prerequisite_met(tracked.get(req)) for req in concept.requires):
                continue
            eligible.append(concept_id)
        return tuple(eligible)


@dataclass(frozen=True, slots=True)
class AdvisorInput:
    """The pseudonymous payload of one advisor call, with the facts the rules need."""

    handle: str
    eligible_ids: tuple[str, ...]
    prerequisites: Mapping[str, tuple[str, ...]]
    """Direct requirements of each eligible concept (all of them, eligible or not)."""

    mastery_scores: Mapping[str, int]
    due: frozenset[str]
    llm_input: LLMInput


def _score(mastery: Mastery | None) -> int:
    return 0 if mastery is None else mastery.score


def _band(mastery: Mastery | None) -> str:
    return (MasteryState.UNSEEN if mastery is None else mastery.state).name.lower()


def _completion(mastery: Mastery | None) -> str:
    if mastery is None or mastery.state is MasteryState.UNSEEN:
        return "not_started"
    return "completed" if mastery.state in _COMPLETED else "in_progress"


def _review_state(schedule: ReviewSchedule | None, now_due: bool) -> str:
    if schedule is None:
        return "none"
    return "due" if now_due else "scheduled"


def build_advisor_input(
    *,
    user_id: StableId,
    run_id: str,
    handle_key: str | bytes,
    eligible_ids: Collection[StableId],
    catalog: CatalogRepository,
    mastery: MasteryRepository,
    reviews: ReviewRepository,
    clock: Clock,
    limit: int = domain_config.PATH_RECOMMENDATION_LENGTH,
) -> AdvisorInput:
    """Build the ``AdvisorInput`` of one learner for one run (Requirement 17.1).

    The handle is ``HMAC(handle_key, run_id:user_id)`` so it differs between runs.
    ``user_id`` is used only to read the learner's state and to derive the handle.
    """
    ids = tuple(sorted(set(eligible_ids)))
    tracked = {m.concept_id: m for m in mastery.list_for_user(user_id)}
    schedules = {s.concept_id: s for s in reviews.list_for_user(user_id)}
    now = clock.now()
    due = frozenset(c for c in ids if c in schedules and schedules[c].due_at <= now)
    prerequisites: dict[str, tuple[str, ...]] = {}
    for concept_id in ids:
        concept = catalog.get_concept(concept_id)
        prerequisites[concept_id] = tuple(sorted(concept.requires)) if concept else ()
    handle = make_handle(handle_key, run_id, user_id)
    fields = {
        "handle": handle,
        "concept_mastery": _dumps({c: _band(tracked.get(c)) for c in ids}),
        "completion": _dumps({c: _completion(tracked.get(c)) for c in ids}),
        "review_state": _dumps({c: _review_state(schedules.get(c), c in due) for c in ids}),
        "prerequisites": _dumps({c: list(prerequisites[c]) for c in ids}),
        "eligible_ids": _dumps(list(ids)),
        "limit": str(limit),
    }
    return AdvisorInput(
        handle=handle,
        eligible_ids=ids,
        prerequisites=prerequisites,
        mastery_scores={c: _score(tracked.get(c)) for c in ids},
        due=due,
        llm_input=LLMInput(InputKind.ADVISOR.value, fields),
    )


def _dumps(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class AdvisorOrderSchema:
    """Validates the raw JSON of an advisor reply: ``{"ordering": [<id>, ...]}``.

    Only the shape is checked here. Whether every id is eligible is decided by
    ``check_llm_order``, so an unknown id is a discarded order with a reason in
    the audit log, not a malformed output.
    """

    def validate(self, text: str) -> Mapping[str, object]:
        """Return ``{"ordering": [...]}`` or raise ``ValueError``."""
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError("advisor reply is not JSON") from exc
        if not isinstance(data, dict) or set(data) != {"ordering"}:
            raise ValueError("advisor reply must be an object with only an ordering field")
        ordering = data["ordering"]
        if not isinstance(ordering, list) or not all(
            isinstance(item, str) and item for item in ordering
        ):
            raise ValueError("ordering must be a list of non-empty strings")
        return {"ordering": list(ordering)}
