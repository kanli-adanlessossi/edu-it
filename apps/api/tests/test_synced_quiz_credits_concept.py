"""Task 13.11: a perfect quiz attempt against the synced catalog credits its concept.

Syncs the seed content into SQLite with ``Postgres_ContentStore``, reads the quiz back
through the real ``Postgres_CatalogRepository`` and runs ``AttemptQuizUseCase``. Without
the question-to-concept links a perfect attempt changes no mastery, so the Processes
gate (Req 2.1) never opens (Req 17.2).

**Validates: Requirements 17.2**
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from edu_it_application.learning import AttemptQuizRequest, AttemptQuizUseCase
from edu_it_content.postgres_store import Postgres_ContentStore
from edu_it_content.sync import sync_content
from edu_it_content.validation import load_content
from edu_it_domain.config import MASTERY_GAIN_PER_CORRECT_ANSWER
from edu_it_domain.learning.models import MasteryState
from edu_it_infrastructure.fakes import (
    In_Memory_AttemptRepository,
    In_Memory_IdempotencyStore,
    In_Memory_MasteryRepository,
    In_Memory_ReviewRepository,
    Sequential_IdGenerator,
    Test_Clock,
)
from edu_it_infrastructure.persistence import Postgres_CatalogRepository, metadata
from sqlalchemy import create_engine

_REPO = Path(__file__).resolve().parents[3]
_QUIZ = "quiz.reading-permissions"
_CONCEPT = "concept.reading-permissions"
_USER = "usr-lea"


def test_perfect_attempt_raises_the_concept_to_practiced_or_higher(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'catalog.db'}")
    metadata.create_all(engine)
    sync_content(Postgres_ContentStore(engine), load_content(_REPO / "content"))
    catalog = Postgres_CatalogRepository(engine)
    quiz = catalog.get_quiz(_QUIZ)
    assert quiz is not None
    selections = {
        q.stable_id: next(o.option_id for o in q.options if o.is_correct) for q in quiz.questions
    }
    mastery = In_Memory_MasteryRepository()

    result = AttemptQuizUseCase(
        catalog=catalog,
        attempts=In_Memory_AttemptRepository(),
        mastery=mastery,
        reviews=In_Memory_ReviewRepository(),
        idempotency=In_Memory_IdempotencyStore(),
        clock=Test_Clock(start=datetime(2024, 6, 1, 9, 0, 0, tzinfo=UTC)),
        ids=Sequential_IdGenerator("id"),
    ).execute(
        AttemptQuizRequest(
            user_id=_USER, quiz_id=_QUIZ, selections=selections, idempotency_key="k-1"
        )
    )

    assert [m.concept_id for m in result.mastery] == [_CONCEPT]
    stored = mastery.get(_USER, _CONCEPT)
    assert stored is not None
    assert stored.score == min(100, len(quiz.questions) * MASTERY_GAIN_PER_CORRECT_ANSWER)
    assert stored.state in (MasteryState.PRACTICED, MasteryState.PROFICIENT, MasteryState.MASTERED)
    engine.dispose()
