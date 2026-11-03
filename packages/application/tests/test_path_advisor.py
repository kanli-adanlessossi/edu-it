"""Unit tests for the path advisor use cases (ai-content task 22.2).

Run over the in-memory fakes: ``RunPathAdvisor`` ends every failure in the rule-based order and
writes one audit row per run; ``GetRecommendation`` filters again at read time;
``DismissRecommendation`` is idempotent and owner-only.

**Validates: Requirements 17.2, 17.4, 17.5, 17.6, 17.8**
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from edu_it_application.ai import (
    AdvisorOutcome,
    DismissRecommendation,
    EligibleItems,
    GetRecommendation,
    RecommendationNotFoundError,
    RunPathAdvisor,
)
from edu_it_domain.learning.models import Mastery, MasteryState, QuestionOption
from edu_it_domain.ports.catalog import ConceptRecord, QuestionRecord, QuizRecord
from edu_it_domain.ports.llm_provider import (
    BudgetReachedError,
    LLMInput,
    OutputSchema,
    PromptRef,
    ProviderUnavailableError,
    StructuredOutput,
)
from edu_it_domain.ports.path_recommendations import AdvisorAuditEntry, PathRecommendation
from edu_it_infrastructure.fakes import (
    Fake_LLMProvider,
    In_Memory_CatalogRepository,
    In_Memory_MasteryRepository,
    In_Memory_PathRecommendationRepository,
    In_Memory_ReviewRepository,
    Sequential_IdGenerator,
    Test_Clock,
)

_NOW = datetime(2025, 1, 1, tzinfo=UTC)
_USER = "usr_0123456789abcdef"
_KEY = "k" * 32
_CONCEPTS = ("basics", "perms", "ghost")


class _Failing:
    """An ``LLMProvider`` that always raises ``error``."""

    def __init__(self, error: Exception) -> None:
        self._error = error
        self.calls = 0

    def complete(
        self, prompt_version: PromptRef, input: LLMInput, schema: OutputSchema, max_retries: int
    ) -> StructuredOutput:
        self.calls += 1
        raise self._error


class _World:
    def __init__(self, llm: object) -> None:
        self.catalog = In_Memory_CatalogRepository()
        self.catalog.add_concept(ConceptRecord("basics"))
        self.catalog.add_concept(ConceptRecord("perms", requires=("basics",)))
        options = (QuestionOption(0, True), QuestionOption(1, False))
        self.catalog.add_quiz(
            QuizRecord(
                "quiz",
                "lesson",
                (
                    QuestionRecord("q1", options, ("basics",)),
                    QuestionRecord("q2", options, ("perms",)),
                ),
            )
        )
        self.mastery = In_Memory_MasteryRepository()
        self.mastery.save(Mastery(_USER, "basics", 40, MasteryState.PRACTICED))
        self.reviews = In_Memory_ReviewRepository()
        self.clock = Test_Clock(_NOW)
        self.repo = In_Memory_PathRecommendationRepository()
        self.eligible = EligibleItems(self.catalog, self.mastery)
        self.run = RunPathAdvisor(
            llm=llm,  # type: ignore[arg-type]
            eligible=self.eligible,
            catalog=self.catalog,
            mastery=self.mastery,
            reviews=self.reviews,
            recommendations=self.repo,
            clock=self.clock,
            ids=Sequential_IdGenerator("id"),
            handle_key=_KEY,
        )


def _reply(*ids: str) -> Fake_LLMProvider:
    return Fake_LLMProvider({"advisor": json.dumps({"ordering": list(ids)})})


def test_an_accepted_model_order_is_persisted_with_source_llm() -> None:
    world = _World(_reply("perms", "basics"))
    result = world.run.execute(_USER, _CONCEPTS)
    assert result.outcome is AdvisorOutcome.ACCEPTED
    assert (result.source, result.ordering) == ("llm", ("perms", "basics"))
    stored = world.repo.get(result.recommendation_id)
    assert stored is not None and stored.ordering == ("perms", "basics")
    assert stored.run_id == result.run_id and stored.dismissed_at is None
    (audit,) = world.repo.audit_entries(_USER)
    assert (audit.outcome, audit.source, audit.reason) == ("accepted", "llm", None)
    assert audit.returned_ids == ("perms", "basics")
    assert audit.handle.startswith("h_") and _USER not in audit.handle


@pytest.mark.parametrize(
    ("returned", "reason"),
    [
        (("basics", "ghost"), "ids not eligible: ghost"),
        (("basics", "basics"), "duplicate ids in order"),
        ((), "empty order"),
    ],
)
def test_an_invalid_model_order_is_discarded_for_the_rule_based_order(
    returned: tuple[str, ...], reason: str
) -> None:
    world = _World(_reply(*returned))
    result = world.run.execute(_USER, _CONCEPTS)
    assert result.outcome is AdvisorOutcome.DISCARDED
    assert (result.source, result.ordering) == ("rule-based", ("basics", "perms"))
    (audit,) = world.repo.audit_entries(_USER)
    assert audit.reason == reason and audit.returned_ids == returned
    assert audit.source == "rule-based"


@pytest.mark.parametrize(
    "llm",
    [
        Fake_LLMProvider({"advisor": "not json"}),  # malformed after every attempt
        _Failing(ProviderUnavailableError("down")),
        _Failing(BudgetReachedError("daily")),
        _Failing(RuntimeError("anything else")),
    ],
)
def test_every_failure_ends_in_the_rule_based_order_with_one_audit_row(llm: object) -> None:
    world = _World(llm)
    result = world.run.execute(_USER, _CONCEPTS)
    assert result.outcome is AdvisorOutcome.FAILED
    assert (result.source, result.ordering) == ("rule-based", ("basics", "perms"))
    (audit,) = world.repo.audit_entries(_USER)
    assert audit.outcome == "failed" and audit.returned_ids == ()
    assert audit.reason is not None


def test_each_run_writes_exactly_one_recommendation_and_one_audit_row() -> None:
    world = _World(_reply("basics"))
    first = world.run.execute(_USER, _CONCEPTS)
    second = world.run.execute(_USER, _CONCEPTS)
    assert first.run_id != second.run_id
    audits = world.repo.audit_entries(_USER)
    assert [a.run_id for a in audits] == [first.run_id, second.run_id]
    assert audits[0].handle != audits[1].handle  # a handle per run


def test_nothing_eligible_persists_an_empty_rule_based_order_without_a_model_call() -> None:
    failing = _Failing(RuntimeError("must not be called"))
    world = _World(failing)
    result = world.run.execute(_USER, ["ghost"])
    assert failing.calls == 0
    assert result.outcome is AdvisorOutcome.NO_ELIGIBLE
    assert (result.source, result.ordering) == ("rule-based", ())
    assert len(world.repo.audit_entries(_USER)) == 1


def test_get_returns_the_latest_undismissed_and_filters_at_read_time() -> None:
    world = _World(_reply("basics", "perms"))
    getter = GetRecommendation(recommendations=world.repo, eligible=world.eligible)
    assert getter.execute(_USER) is None
    saved = world.run.execute(_USER, _CONCEPTS)

    shown = getter.execute(_USER)
    assert shown is not None
    assert (shown.stable_id, shown.source, shown.ordering) == (
        saved.recommendation_id,
        "llm",
        ("basics", "perms"),
    )

    # Mastery of the prerequisite is lost: "perms" is locked again and must not surface.
    world.mastery.save(Mastery(_USER, "basics", 0, MasteryState.UNSEEN))
    filtered = getter.execute(_USER)
    assert filtered is not None and filtered.ordering == ("basics",)

    # A newer recommendation of which nothing is eligible any more: none is shown.
    later = _NOW + timedelta(hours=1)
    world.repo.record_run(
        PathRecommendation("rec-later", _USER, "llm", ("ghost",), "run-later", later),
        AdvisorAuditEntry("aud-later", _USER, "run-later", "h_later", "llm", "accepted", later),
    )
    assert getter.execute(_USER) is None


def test_dismissal_hides_the_recommendation_and_is_idempotent() -> None:
    world = _World(_reply("basics"))
    getter = GetRecommendation(recommendations=world.repo, eligible=world.eligible)
    dismiss = DismissRecommendation(recommendations=world.repo, clock=world.clock)
    saved = world.run.execute(_USER, _CONCEPTS)

    first = dismiss.execute(_USER, saved.recommendation_id)
    world.clock.advance(timedelta(hours=3))
    second = dismiss.execute(_USER, saved.recommendation_id)
    assert first == second and first.dismissed_at == _NOW
    assert getter.execute(_USER) is None


def test_dismissing_an_unknown_or_foreign_recommendation_is_not_found() -> None:
    world = _World(_reply("basics"))
    dismiss = DismissRecommendation(recommendations=world.repo, clock=world.clock)
    saved = world.run.execute(_USER, _CONCEPTS)
    with pytest.raises(RecommendationNotFoundError):
        dismiss.execute("usr_other", saved.recommendation_id)
    with pytest.raises(RecommendationNotFoundError):
        dismiss.execute(_USER, "missing")
    assert world.repo.latest_undismissed(_USER) is not None  # the foreign attempt changed nothing
