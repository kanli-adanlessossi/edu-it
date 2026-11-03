"""The path advisor use cases (ai-content Requirements 17.2, 17.4, 17.5, 17.6, 17.8).

``RunPathAdvisor`` runs ahead of time in the worker for one learner:

1. The Eligible_Items are computed (exists, servable, unlocked).
2. The model is asked for an order of them through an ``ADVISOR`` input that carries a
   per-run Pseudonymous_Handle and no identity.
3. The reply is persisted only when every returned id is an Eligible_Item. Any other
   result (an id that is not eligible, a repeated id, an empty or oversize order, malformed
   output, an unavailable provider, a reached Budget, any other error) ends in the
   ``rule_based_order`` with ``source`` set to ``rule-based``.

Every run persists exactly one recommendation and exactly one audit row, in one operation.
The audit row holds the handle, source, outcome, the reason and the identifiers the model
returned, never model text.

``GetRecommendation`` returns the learner's latest undismissed recommendation, filtered again
at read time against current eligibility, so an item withdrawn since the run never surfaces.
``DismissRecommendation`` records a dismissal once and is idempotent.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from edu_it_domain import config as domain_config
from edu_it_domain.ai.ordering import check_llm_order, rule_based_order
from edu_it_domain.ports.path_recommendations import (
    SOURCE_LLM,
    SOURCE_RULE_BASED,
    AdvisorAuditEntry,
    PathRecommendation,
)

from edu_it_application.ai.advisor_input import (
    ADVISOR_PROMPT,
    AdvisorOrderSchema,
    build_advisor_input,
)
from edu_it_application.ai.errors import RecommendationNotFoundError

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping
    from datetime import datetime

    from edu_it_domain.learning.models import StableId
    from edu_it_domain.ports.catalog import CatalogRepository
    from edu_it_domain.ports.clock import Clock
    from edu_it_domain.ports.id_generator import IdGenerator
    from edu_it_domain.ports.llm_provider import LLMProvider
    from edu_it_domain.ports.mastery import MasteryRepository
    from edu_it_domain.ports.path_recommendations import PathRecommendationRepository
    from edu_it_domain.ports.review import ReviewRepository

    from edu_it_application.ai.advisor_input import EligibleItems

__all__ = [
    "AdvisorOutcome",
    "AdvisorRunResult",
    "DismissRecommendation",
    "DismissResult",
    "GetRecommendation",
    "Recommendation",
    "RunPathAdvisor",
]

_LOG = logging.getLogger(__name__)


class AdvisorOutcome(StrEnum):
    """How one advisor run ended; stored as the ``outcome`` of the audit row."""

    ACCEPTED = "accepted"
    """The model order was valid and persisted (``source`` ``llm``)."""

    DISCARDED = "discarded"
    """The model order was not valid; the rule-based order was persisted."""

    FAILED = "failed"
    """The call failed, timed out, was malformed or was stopped by a Budget."""

    NO_ELIGIBLE = "no_eligible"
    """Nothing was eligible, so there was nothing to order and no model call."""


@dataclass(frozen=True, slots=True)
class AdvisorRunResult:
    """What one advisor run persisted."""

    recommendation_id: str
    run_id: str
    source: str
    outcome: AdvisorOutcome
    ordering: tuple[str, ...]
    reason: str | None = None


class RunPathAdvisor:
    """Compute and persist the recommendation of one learner."""

    def __init__(
        self,
        *,
        llm: LLMProvider,
        eligible: EligibleItems,
        catalog: CatalogRepository,
        mastery: MasteryRepository,
        reviews: ReviewRepository,
        recommendations: PathRecommendationRepository,
        clock: Clock,
        ids: IdGenerator,
        handle_key: str | bytes,
        config: object = domain_config,
    ) -> None:
        """``catalog`` should be the ``ServableCatalog``; ``handle_key`` is the advisor key."""
        self._llm = llm
        self._eligible = eligible
        self._catalog = catalog
        self._mastery = mastery
        self._reviews = reviews
        self._recommendations = recommendations
        self._clock = clock
        self._ids = ids
        self._handle_key = handle_key
        self._config = config

    def execute(
        self,
        user_stable_id: StableId,
        concept_ids: Collection[StableId],
        *,
        run_id: str | None = None,
    ) -> AdvisorRunResult:
        """Run the advisor for one learner over the candidate ``concept_ids``.

        Never raises for a model, budget or order problem: every such failure persists the
        rule-based order. Storage errors propagate.
        """
        limit: int = self._config.PATH_RECOMMENDATION_LENGTH  # type: ignore[attr-defined]
        run = run_id if run_id else self._ids.new_id()
        eligible_ids = self._eligible.for_learner(user_stable_id, concept_ids)
        advice = build_advisor_input(
            user_id=user_stable_id,
            run_id=run,
            handle_key=self._handle_key,
            eligible_ids=eligible_ids,
            catalog=self._catalog,
            mastery=self._mastery,
            reviews=self._reviews,
            clock=self._clock,
            limit=limit,
        )

        returned: tuple[str, ...] = ()
        reason: str | None = None
        accepted: tuple[str, ...] | None = None
        if not advice.eligible_ids:
            outcome = AdvisorOutcome.NO_ELIGIBLE
        else:
            try:
                reply = self._llm.complete(
                    ADVISOR_PROMPT,
                    advice.llm_input,
                    AdvisorOrderSchema(),
                    domain_config.LLM_MAX_RETRIES,
                )
                returned = tuple(str(item) for item in _ordering(reply.output))
            except Exception as error:  # every failure ends in the rule-based order (17.5)
                outcome = AdvisorOutcome.FAILED
                reason = type(error).__name__
                _LOG.warning("path advisor call failed: %s", reason)
            else:
                verdict = check_llm_order(returned, advice.eligible_ids, limit=limit)
                if verdict.accepted and verdict.ordering:
                    outcome = AdvisorOutcome.ACCEPTED
                    accepted = verdict.ordering
                else:
                    outcome = AdvisorOutcome.DISCARDED
                    reason = verdict.reason or "empty order"

        if accepted is not None:
            source, ordering = SOURCE_LLM, accepted
        else:
            source = SOURCE_RULE_BASED
            ordering = rule_based_order(
                advice.eligible_ids,
                advice.prerequisites,
                advice.mastery_scores,
                advice.due,
                limit=limit,
            )

        now = self._clock.now()
        recommendation = PathRecommendation(
            stable_id=self._ids.new_id(),
            user_id=user_stable_id,
            source=source,
            ordering=ordering,
            run_id=run,
            created_at=now,
        )
        audit = AdvisorAuditEntry(
            stable_id=self._ids.new_id(),
            user_id=user_stable_id,
            run_id=run,
            handle=advice.handle,
            source=source,
            outcome=outcome.value,
            recorded_at=now,
            reason=reason,
            returned_ids=returned,
        )
        self._recommendations.record_run(recommendation, audit)
        return AdvisorRunResult(recommendation.stable_id, run, source, outcome, ordering, reason)


def _ordering(output: Mapping[str, object]) -> list[object]:
    """The ``ordering`` list of a validated reply; ``ValueError`` if it is missing."""
    value = output.get("ordering")
    if not isinstance(value, list):
        raise ValueError("advisor reply has no ordering")
    return value


@dataclass(frozen=True, slots=True)
class Recommendation:
    """A recommendation as shown to the learner."""

    stable_id: str
    source: str
    ordering: tuple[str, ...]
    created_at: datetime


class GetRecommendation:
    """The learner's latest undismissed recommendation, filtered at read time."""

    def __init__(
        self, *, recommendations: PathRecommendationRepository, eligible: EligibleItems
    ) -> None:
        self._recommendations = recommendations
        self._eligible = eligible

    def execute(self, user_stable_id: StableId) -> Recommendation | None:
        """``None`` when there is none, or nothing in it is eligible any more (204)."""
        stored = self._recommendations.latest_undismissed(user_stable_id)
        if stored is None:
            return None
        still = frozenset(self._eligible.for_learner(user_stable_id, stored.ordering))
        ordering = tuple(item for item in stored.ordering if item in still)
        if not ordering:
            return None
        return Recommendation(stored.stable_id, stored.source, ordering, stored.created_at)


@dataclass(frozen=True, slots=True)
class DismissResult:
    """The state of a dismissed recommendation."""

    stable_id: str
    dismissed_at: datetime


class DismissRecommendation:
    """Record a learner's dismissal; repeating it changes nothing."""

    def __init__(self, *, recommendations: PathRecommendationRepository, clock: Clock) -> None:
        self._recommendations = recommendations
        self._clock = clock

    def execute(self, user_stable_id: StableId, recommendation_id: str) -> DismissResult:
        """Dismiss the learner's recommendation; ``RecommendationNotFoundError`` if not theirs."""
        dismissed = self._recommendations.dismiss(
            recommendation_id, user_stable_id, self._clock.now()
        )
        if dismissed is None or dismissed.dismissed_at is None:
            raise RecommendationNotFoundError(recommendation_id)
        return DismissResult(dismissed.stable_id, dismissed.dismissed_at)
