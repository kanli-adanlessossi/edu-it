"""The reveal-hint use case (Req 18.3, 7.1, 7.2).

:class:`RevealHintUseCase` records one ``hint_reveal`` and, for each concept the
question covers, one ``mastery_event`` (Req 7.1) through the domain's
:func:`~edu_it_domain.learning.hints.record_hint_reveal`. The reveal itself
carries the Config :data:`~edu_it_domain.config.HINT_REVEAL_MASTERY_DELTA` (zero),
so repeated reveals cannot farm mastery; the cost of a hint is the multiplier the
*next* submission of that question earns (Req 7.2), applied by the attempt-quiz
use case from the reveals recorded here.

It returns the progressive hint *level* (1 for the first reveal of a question, 2
for the next, ...). Resolving the level to hint text is the boundary's job: hint
text is authored content, not domain state.
"""

from __future__ import annotations

from dataclasses import dataclass

from edu_it_domain.config import HINT_REVEAL_MASTERY_DELTA
from edu_it_domain.learning.hints import record_hint_reveal
from edu_it_domain.learning.models import Mastery, StableId
from edu_it_domain.ports.catalog import CatalogRepository
from edu_it_domain.ports.clock import Clock
from edu_it_domain.ports.mastery import HintRevealRecord, MasteryRepository

from edu_it_application.learning._support import load_or_start_mastery
from edu_it_application.learning.errors import NotFoundError

__all__ = [
    "RevealHintResult",
    "RevealHintUseCase",
]


@dataclass(frozen=True, slots=True)
class RevealHintResult:
    """The outcome of revealing a hint."""

    reveal: HintRevealRecord
    """The recorded ``hint_reveal``."""

    hint_level: int
    """The progressive level just revealed (1-based) (Req 18.3)."""

    mastery: tuple[Mastery, ...]
    """The concept masteries that gained a ``mastery_event`` (Req 7.1)."""


class RevealHintUseCase:
    """Record a hint reveal and its mastery event."""

    def __init__(
        self,
        *,
        catalog: CatalogRepository,
        mastery: MasteryRepository,
        clock: Clock,
    ) -> None:
        """Wire the ports the use case orchestrates."""
        self._catalog = catalog
        self._mastery = mastery
        self._clock = clock

    def execute(
        self,
        user_id: StableId,
        quiz_id: StableId,
        question_id: StableId,
    ) -> RevealHintResult:
        """Reveal the next hint of ``question_id`` for ``user_id``.

        Raises:
            NotFoundError: the quiz or the question within it does not exist.
        """
        quiz = self._catalog.get_quiz(quiz_id)
        if quiz is None:
            raise NotFoundError("quiz", quiz_id)
        question = next((q for q in quiz.questions if q.stable_id == question_id), None)
        if question is None:
            raise NotFoundError("question", question_id)

        level = (
            sum(1 for r in self._mastery.list_hint_reveals(user_id) if r.question_id == question_id)
            + 1
        )

        revealed_at = self._clock.now()
        reveal = HintRevealRecord(
            user_id=user_id,
            question_id=question_id,
            revealed_at=revealed_at,
        )
        self._mastery.add_hint_reveal(reveal)

        changed: list[Mastery] = []
        for concept_id in question.concept_ids:
            _, updated = record_hint_reveal(
                load_or_start_mastery(self._mastery, user_id, concept_id),
                clock=self._clock,
                base_gain=HINT_REVEAL_MASTERY_DELTA,
            )
            self._mastery.save(updated)
            changed.append(updated)

        return RevealHintResult(reveal=reveal, hint_level=level, mastery=tuple(changed))
