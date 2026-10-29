"""Property test for quiz content-version pinning (task 17.3).

Feature: learning-core, Property 24: An active attempt stays pinned to its content version

Drives the real ``AttemptQuizUseCase`` over the in-memory fakes and the
controllable ``Test_Clock``. Whatever sequence of edits the quiz goes through,
an attempt that names the version it started under is judged against that
version, records it, leaves earlier attempts untouched, and every superseded
(archived) version stays resolvable.

**Validates: Requirements 25.1, 25.2, 25.3, 26.3**
"""

from __future__ import annotations

from datetime import UTC, datetime

from edu_it_application.learning import AttemptQuizRequest, AttemptQuizUseCase
from edu_it_domain.config import COOLDOWN_INTERVAL
from edu_it_domain.learning.models import QuestionOption
from edu_it_domain.ports.catalog import QuestionRecord, QuizRecord
from edu_it_infrastructure.fakes import (
    In_Memory_AttemptRepository,
    In_Memory_CatalogRepository,
    In_Memory_IdempotencyStore,
    In_Memory_MasteryRepository,
    In_Memory_ReviewRepository,
    Sequential_IdGenerator,
    Test_Clock,
)
from hypothesis import given, settings
from hypothesis import strategies as st

_T0 = datetime(2024, 6, 1, 9, 0, 0, tzinfo=UTC)
_USER = "usr-lea"
_QUIZ = "qz-perms"
_Q = "qn-1"
_CONCEPT = "con-perms"
_OPTIONS = (1, 2)


def _quiz(version: str, correct_option: int) -> QuizRecord:
    question = QuestionRecord(
        stable_id=_Q,
        options=tuple(
            QuestionOption(option_id=o, is_correct=o == correct_option) for o in _OPTIONS
        ),
        concept_ids=(_CONCEPT,),
    )
    return QuizRecord(
        stable_id=_QUIZ, lesson_id="les-1", questions=(question,), content_version=version
    )


@st.composite
def _scenarios(draw: st.DrawFn) -> tuple[list[int], int, int, bool]:
    """Correct option per version (v1..vN), pinned index, learner pick, prior attempt?"""
    corrects = draw(st.lists(st.sampled_from(_OPTIONS), min_size=1, max_size=5))
    pinned = draw(st.integers(min_value=0, max_value=len(corrects) - 1))
    pick = draw(st.sampled_from(_OPTIONS))
    prior = draw(st.booleans())
    return corrects, pinned, pick, prior


# Feature: learning-core, Property 24: An active attempt stays pinned to its content version
# **Validates: Requirements 25.1, 25.2, 25.3, 26.3**
@settings(max_examples=100, deadline=None)
@given(scenario=_scenarios())
def test_property_24_active_attempt_stays_pinned_to_its_content_version(
    scenario: tuple[list[int], int, int, bool],
) -> None:
    corrects, pinned, pick, with_prior = scenario
    clock = Test_Clock(start=_T0)
    catalog = In_Memory_CatalogRepository()
    attempts = In_Memory_AttemptRepository()
    use_case = AttemptQuizUseCase(
        catalog=catalog,
        attempts=attempts,
        mastery=In_Memory_MasteryRepository(),
        reviews=In_Memory_ReviewRepository(),
        idempotency=In_Memory_IdempotencyStore(),
        clock=clock,
        ids=Sequential_IdGenerator("id"),
    )

    catalog.add_quiz(_quiz("v1", corrects[0]))
    prior_row = None
    if with_prior:
        prior_row = use_case.execute(
            AttemptQuizRequest(
                user_id=_USER, quiz_id=_QUIZ, selections={_Q: pick}, idempotency_key="prior"
            )
        ).attempt
        clock.advance(COOLDOWN_INTERVAL)
    # The quiz is edited mid-attempt, possibly many times (Req 25.1).
    for index, correct in enumerate(corrects[1:], start=2):
        catalog.revise_quiz(_quiz(f"v{index}", correct))

    version = f"v{pinned + 1}"
    result = use_case.execute(
        AttemptQuizRequest(
            user_id=_USER,
            quiz_id=_QUIZ,
            selections={_Q: pick},
            idempotency_key="pinned",
            content_version=version,
        )
    )

    # Judged against the pinned version, not the current one (Req 25.1).
    assert result.attempt.content_version == version
    assert result.attempt.score == (100 if pick == corrects[pinned] else 0)

    # Existing progress rows are never rewritten (Req 25.3).
    if prior_row is not None:
        stored = {a.stable_id: a for a in attempts.list_quiz_attempts(_USER, _QUIZ)}
        assert stored[prior_row.stable_id].content_version == "v1"
        assert stored[prior_row.stable_id].score == prior_row.score

    # Every superseded version stays resolvable; none is deleted (Req 26.3).
    for index, correct in enumerate(corrects, start=1):
        archived = catalog.get_quiz_at_version(_QUIZ, f"v{index}")
        assert archived is not None
        assert archived.content_version == f"v{index}"
        assert [o.option_id for o in archived.questions[0].options if o.is_correct] == [correct]

    # A new attempt (no version) uses the current revision (Req 25.2).
    clock.advance(COOLDOWN_INTERVAL)
    fresh = use_case.execute(
        AttemptQuizRequest(
            user_id=_USER, quiz_id=_QUIZ, selections={_Q: pick}, idempotency_key="fresh"
        )
    )
    assert fresh.attempt.content_version == f"v{len(corrects)}"
    assert fresh.attempt.score == (100 if pick == corrects[-1] else 0)
