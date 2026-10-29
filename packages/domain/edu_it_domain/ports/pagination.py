"""Keyset-page reader ports for list endpoints (Req 17.5).

List endpoints page with a cursor over a stable sort key tie-broken by
``stable_id`` -- never an offset. These narrow protocols declare the ``*_page``
reads the persistence adapters expose; ``after`` is the ``(sort key, stable_id)``
of the last item of the previous page, and the results are strictly after it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from datetime import datetime

    from edu_it_domain.learning.models import QuizAttempt, StableId
    from edu_it_domain.ports.enrollment import EnrollmentRecord

__all__ = [
    "AttemptPageReader",
    "EnrollmentPageReader",
    "Keyset",
]

Keyset = tuple["datetime", "StableId"]
"""A keyset position: ``(sort-key instant, stable_id)``."""


@runtime_checkable
class EnrollmentPageReader(Protocol):
    """Keyset-paged read of a learner's enrollments, ordered ``(enrolled_at, stable_id)``."""

    def list_for_user_page(
        self,
        user_id: StableId,
        *,
        after: Keyset | None = None,
        limit: int,
    ) -> tuple[EnrollmentRecord, ...]:
        """Return up to ``limit`` enrollments strictly after ``after``."""
        ...


@runtime_checkable
class AttemptPageReader(Protocol):
    """Keyset-paged read of a learner's quiz attempts, ordered ``(attempted_at, stable_id)``."""

    def list_quiz_attempts_page(
        self,
        user_id: StableId,
        quiz_id: StableId,
        *,
        after: Keyset | None = None,
        limit: int,
    ) -> tuple[QuizAttempt, ...]:
        """Return up to ``limit`` attempts strictly after ``after``."""
        ...
