"""Property 33: dismissal removes a recommendation from view and is idempotent.

*For any* recommendation, after a dismissal the latest recommendation shown to that learner is
not the dismissed one, and dismissing it again leaves the state unchanged (the first
``dismissed_at`` is kept). Another learner's recommendation cannot be dismissed.

**Validates: Requirements 17.8**
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from edu_it_application.ai import (
    DismissRecommendation,
    EligibleItems,
    GetRecommendation,
    RecommendationNotFoundError,
)
from edu_it_domain.learning.models import QuestionOption
from edu_it_domain.ports.catalog import ConceptRecord, QuestionRecord, QuizRecord
from edu_it_domain.ports.path_recommendations import AdvisorAuditEntry, PathRecommendation
from edu_it_infrastructure.fakes import (
    In_Memory_CatalogRepository,
    In_Memory_MasteryRepository,
    In_Memory_PathRecommendationRepository,
    Test_Clock,
)
from hypothesis import given, settings
from hypothesis import strategies as st

_START = datetime(2025, 1, 1, tzinfo=UTC)
_ALICE = "usr_alice"
_BOB = "usr_bob"
_CONCEPTS = ("c0", "c1", "c2", "c3")


class _World:
    def __init__(self) -> None:
        catalog = In_Memory_CatalogRepository()
        options = (QuestionOption(0, True), QuestionOption(1, False))
        for concept in _CONCEPTS:
            catalog.add_concept(ConceptRecord(concept))
        catalog.add_quiz(
            QuizRecord(
                "quiz",
                "lesson",
                tuple(QuestionRecord(f"q-{c}", options, (c,)) for c in _CONCEPTS),
            )
        )
        self.repo = In_Memory_PathRecommendationRepository()
        self.clock = Test_Clock(_START + timedelta(days=1))
        self.get = GetRecommendation(
            recommendations=self.repo,
            eligible=EligibleItems(catalog, In_Memory_MasteryRepository()),
        )
        self.dismiss = DismissRecommendation(recommendations=self.repo, clock=self.clock)

    def add(self, user: str, index: int, ordering: tuple[str, ...]) -> str:
        """Persist the ``index``-th recommendation of ``user``; later ones are newer."""
        stable_id = f"rec-{user}-{index}"
        created = _START + timedelta(minutes=index)
        self.repo.record_run(
            PathRecommendation(
                stable_id, user, "rule-based", ordering, f"run-{stable_id}", created
            ),
            AdvisorAuditEntry(
                f"aud-{stable_id}", user, f"run-{stable_id}", "h_x", "rule-based", "failed", created
            ),
        )
        return stable_id


_ORDERINGS = st.lists(
    st.lists(st.sampled_from(_CONCEPTS), min_size=1, max_size=4, unique=True).map(tuple),
    min_size=1,
    max_size=5,
)


@settings(max_examples=60, deadline=None)
@given(
    orderings=_ORDERINGS,
    pick=st.integers(min_value=0, max_value=4),
    repeats=st.integers(min_value=1, max_value=4),
)
def test_dismissal_hides_the_recommendation_and_is_idempotent(
    orderings: list[tuple[str, ...]], pick: int, repeats: int
) -> None:
    world = _World()
    ids = [world.add(_ALICE, i, o) for i, o in enumerate(orderings)]
    target = ids[pick % len(ids)]
    others_before = {i: world.repo.get(i) for i in ids if i != target}

    first = world.dismiss.execute(_ALICE, target)
    shown = world.get.execute(_ALICE)
    assert shown is None or shown.stable_id != target
    after_first = world.repo.get(target)

    for _ in range(repeats):
        world.clock.advance(timedelta(hours=1))
        again = world.dismiss.execute(_ALICE, target)
        assert again == first
        assert world.repo.get(target) == after_first
        shown = world.get.execute(_ALICE)
        assert shown is None or shown.stable_id != target

    assert after_first is not None and after_first.dismissed_at == first.dismissed_at
    assert {i: world.repo.get(i) for i in ids if i != target} == others_before


@settings(max_examples=40, deadline=None)
@given(orderings=_ORDERINGS, pick=st.integers(min_value=0, max_value=4))
def test_another_learners_recommendation_cannot_be_dismissed(
    orderings: list[tuple[str, ...]], pick: int
) -> None:
    world = _World()
    ids = [world.add(_ALICE, i, o) for i, o in enumerate(orderings)]
    target = ids[pick % len(ids)]
    before = [world.repo.get(i) for i in ids]
    shown_before = world.get.execute(_ALICE)

    with pytest.raises(RecommendationNotFoundError):
        world.dismiss.execute(_BOB, target)

    assert [world.repo.get(i) for i in ids] == before
    assert world.get.execute(_ALICE) == shown_before
