"""JSON codec for a stored quiz-attempt result (task 18.5, Req 23.1).

A durable ``IdempotencyStore`` keeps the first result of an ``Idempotency-Key`` as
text so any worker process can replay it. :func:`encode_attempt_result` and
:func:`decode_attempt_result` round-trip an :class:`AttemptQuizResult` losslessly;
the store is handed them at composition time.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from edu_it_domain.learning.models import (
    Answer,
    Mastery,
    MasteryEvent,
    MasteryState,
    QuizAttempt,
)

from edu_it_application.learning.attempt_quiz import AttemptQuizResult

__all__ = ["decode_attempt_result", "encode_attempt_result"]


def _attempt(a: QuizAttempt) -> dict[str, Any]:
    return {
        "stable_id": a.stable_id,
        "quiz_id": a.quiz_id,
        "score": a.score,
        "is_best": a.is_best,
        "attempted_at": a.attempted_at.isoformat(),
        "content_version": a.content_version,
    }


def _to_attempt(d: dict[str, Any]) -> QuizAttempt:
    return QuizAttempt(
        stable_id=d["stable_id"],
        quiz_id=d["quiz_id"],
        score=d["score"],
        is_best=d["is_best"],
        attempted_at=datetime.fromisoformat(d["attempted_at"]),
        content_version=d["content_version"],
    )


def encode_attempt_result(result: object) -> str:
    """Serialize an :class:`AttemptQuizResult` to JSON text."""
    assert isinstance(result, AttemptQuizResult)
    return json.dumps(
        {
            "attempt": _attempt(result.attempt),
            "best_attempt": _attempt(result.best_attempt),
            "answers": [
                {
                    "question_id": a.question_id,
                    "first_answered_at": a.first_answered_at.isoformat(),
                    "counted_for_mastery": a.counted_for_mastery,
                }
                for a in result.answers
            ],
            "mastery": [
                {
                    "user_id": m.user_id,
                    "concept_id": m.concept_id,
                    "score": m.score,
                    "state": m.state.value,
                    "events": [
                        {
                            "delta": e.delta,
                            "source": e.source,
                            "occurred_at": e.occurred_at.isoformat(),
                        }
                        for e in m.events
                    ],
                }
                for m in result.mastery
            ],
        }
    )


def decode_attempt_result(text: str) -> AttemptQuizResult:
    """Rebuild the :class:`AttemptQuizResult` that :func:`encode_attempt_result` stored."""
    d = json.loads(text)
    return AttemptQuizResult(
        attempt=_to_attempt(d["attempt"]),
        best_attempt=_to_attempt(d["best_attempt"]),
        answers=tuple(
            Answer(
                question_id=a["question_id"],
                first_answered_at=datetime.fromisoformat(a["first_answered_at"]),
                counted_for_mastery=a["counted_for_mastery"],
            )
            for a in d["answers"]
        ),
        mastery=tuple(
            Mastery(
                user_id=m["user_id"],
                concept_id=m["concept_id"],
                score=m["score"],
                state=MasteryState(m["state"]),
                events=tuple(
                    MasteryEvent(
                        delta=e["delta"],
                        source=e["source"],
                        occurred_at=datetime.fromisoformat(e["occurred_at"]),
                    )
                    for e in m["events"]
                ),
            )
            for m in d["mastery"]
        ),
    )
