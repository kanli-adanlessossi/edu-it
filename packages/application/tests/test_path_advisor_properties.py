"""Property 31: Persisted recommendations hold only Eligible_Items, with fallback and audit row.

Feature: ai-content, Property 31

For any model reply (valid, non-eligible id, repeated ids, empty, malformed) and any provider
failure (unavailable, Budget reached, other error), ``RunPathAdvisor`` persists a
recommendation whose ids are all Eligible_Items; every failure ends in the rule-based order with
source ``rule-based``; each run writes exactly one recommendation and one audit row; and the audit
row holds no model text and no learner identity.

**Validates: Requirements 17.3, 17.4, 17.5, 17.6, 17.9, 22.3**
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from edu_it_application.ai import AdvisorOutcome, EligibleItems, RunPathAdvisor
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
from edu_it_infrastructure.fakes import (
    Fake_LLMProvider,
    In_Memory_CatalogRepository,
    In_Memory_MasteryRepository,
    In_Memory_PathRecommendationRepository,
    In_Memory_ReviewRepository,
    Sequential_IdGenerator,
    Test_Clock,
)
from hypothesis import given, settings
from hypothesis import strategies as st

_NOW = datetime(2025, 1, 1, tzinfo=UTC)
_USER = "usr_0123456789abcdef"
_KEY = "k" * 32
_CONCEPTS = ("basics", "perms", "ghost")
_ELIGIBLE = ("basics", "perms")  # "ghost" has no servable question; "perms" is unlocked
_OTHER_IDS = ("ghost", "unknown", "other")
_SECRET = "SECRET-MODEL-TEXT"


class _Failing:
    """An ``LLMProvider`` that always raises ``error``."""

    def __init__(self, error: Exception) -> None:
        self._error = error

    def complete(
        self, prompt_version: PromptRef, input: LLMInput, schema: OutputSchema, max_retries: int
    ) -> StructuredOutput:
        raise self._error


def _providers() -> st.SearchStrategy[tuple[object, bool]]:
    """``(provider, is_failure_or_invalid)`` pairs: ``False`` means a valid eligible order."""
    ids = st.sampled_from((*_ELIGIBLE, *_OTHER_IDS))
    valid = st.permutations(_ELIGIBLE).flatmap(
        lambda p: st.integers(1, len(p)).map(lambda n: list(p[:n]))
    )
    ok = valid.map(lambda o: (Fake_LLMProvider({"advisor": json.dumps({"ordering": o})}), False))
    bad_ids = st.lists(ids, max_size=6).filter(
        lambda o: not o or len(set(o)) != len(o) or any(i not in _ELIGIBLE for i in o)
    )
    bad = bad_ids.map(lambda o: (Fake_LLMProvider({"advisor": json.dumps({"ordering": o})}), True))
    malformed = st.sampled_from(
        (
            _SECRET,
            json.dumps({"ordering": _SECRET}),
            json.dumps({"other": [_SECRET]}),
            json.dumps([_SECRET]),
        )
    ).map(lambda text: (Fake_LLMProvider({"advisor": text}), True))
    errors = st.sampled_from(
        (
            ProviderUnavailableError(_SECRET),
            BudgetReachedError(_SECRET),
            RuntimeError(_SECRET),
        )
    ).map(lambda e: (_Failing(e), True))
    return st.one_of(ok, bad, malformed, errors)


def _world(llm: object) -> tuple[RunPathAdvisor, In_Memory_PathRecommendationRepository]:
    catalog = In_Memory_CatalogRepository()
    catalog.add_concept(ConceptRecord("basics"))
    catalog.add_concept(ConceptRecord("perms", requires=("basics",)))
    options = (QuestionOption(0, True), QuestionOption(1, False))
    catalog.add_quiz(
        QuizRecord(
            "quiz",
            "lesson",
            (
                QuestionRecord("q1", options, ("basics",)),
                QuestionRecord("q2", options, ("perms",)),
            ),
        )
    )
    mastery = In_Memory_MasteryRepository()
    mastery.save(Mastery(_USER, "basics", 40, MasteryState.PRACTICED))
    repo = In_Memory_PathRecommendationRepository()
    run = RunPathAdvisor(
        llm=llm,  # type: ignore[arg-type]
        eligible=EligibleItems(catalog, mastery),
        catalog=catalog,
        mastery=mastery,
        reviews=In_Memory_ReviewRepository(),
        recommendations=repo,
        clock=Test_Clock(_NOW),
        ids=Sequential_IdGenerator("id"),
        handle_key=_KEY,
    )
    return run, repo


@settings(max_examples=100, deadline=None)
@given(pair=_providers(), runs=st.integers(1, 3))
def test_property_31_persisted_recommendations_are_eligible_with_fallback_and_audit(
    pair: tuple[object, bool], runs: int
) -> None:
    """Feature: ai-content, Property 31.

    **Validates: Requirements 17.3, 17.4, 17.5, 17.6, 17.9, 22.3**
    """
    llm, must_fall_back = pair
    run, repo = _world(llm)
    results = [run.execute(_USER, _CONCEPTS) for _ in range(runs)]
    audits = repo.audit_entries(_USER)

    # Exactly one audit row (and one recommendation) per run.
    assert [a.run_id for a in audits] == [r.run_id for r in results]
    for result, audit in zip(results, audits, strict=True):
        stored = repo.get(result.recommendation_id)
        assert stored is not None and stored.run_id == result.run_id

        # Persisted ids are always Eligible_Items, without duplicates.
        assert set(stored.ordering) <= set(_ELIGIBLE)
        assert len(set(stored.ordering)) == len(stored.ordering)
        assert stored.ordering == result.ordering

        if must_fall_back:
            assert stored.source == "rule-based"
            assert stored.ordering == ("basics", "perms")
            assert result.outcome in (AdvisorOutcome.DISCARDED, AdvisorOutcome.FAILED)
        else:
            assert stored.source == "llm" and result.outcome is AdvisorOutcome.ACCEPTED
        assert audit.source == stored.source and audit.outcome == result.outcome.value

        # No model text and no learner identity in the audit row.
        text = " ".join([audit.handle, audit.reason or "", *audit.returned_ids])
        assert _SECRET not in text
        assert _USER not in text
        assert audit.handle.startswith("h_") and _USER not in audit.handle
