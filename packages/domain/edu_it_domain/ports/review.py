"""The review repository port (Req 1.2, 9.1, 9.4, D3 §7, §8).

A concept's spaced-review schedule for a learner and the attempts against it —
the ``review_schedule`` and ``review_attempt`` entities of the mastery and
review group (D3 §7.2, §8) — are read and written through the port declared
here. Exactly one schedule entry is created when a concept first reaches
Practiced, and none is added when Practiced is re-reached after a decay dip
(Req 5.4, 9.1, 9.2); each ``review_attempt`` records a pass/fail that lengthens
or shortens the schedule's interval (Req 9.4, 9.5).

The domain review types are reused unchanged from
:mod:`edu_it_domain.learning.models` —
:class:`~edu_it_domain.learning.models.ReviewSchedule` (with its embedded
:class:`~edu_it_domain.learning.models.ReviewAttempt` history) — so this port
reads and writes those domain values and does not redefine them.

Like the identity repositories (ADR 0004) and the sibling learning ports, this
is a framework-free :class:`typing.Protocol` (``@runtime_checkable``, standard
library + domain imports only, no web/API framework per Req 1.8). The
application layer depends on the protocol; the infrastructure layer supplies a
PostgreSQL (SQLAlchemy Core) implementation and, for tests, an in-memory one.
The learner and concept are addressed by their ``stable_id`` (D3 §1.2, Req 1.1);
the internal numeric primary key of a row is never exposed. Whether a schedule
is due or overdue derives purely from the injected ``Clock`` compared against
:attr:`~edu_it_domain.learning.models.ReviewSchedule.due_at` (Req 9.6), so the
port stores the schedule state and never a precomputed due/overdue flag.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from edu_it_domain.learning.models import ReviewSchedule, StableId

__all__ = [
    "ReviewRepository",
]


@runtime_checkable
class ReviewRepository(Protocol):
    """Port for reading and writing ``review_schedule`` and ``review_attempt`` (Req 9.1, 9.4)."""

    def get(self, user_id: StableId, concept_id: StableId) -> ReviewSchedule | None:
        """Return the learner's review schedule for the concept, or ``None``.

        ``None`` before the concept first reaches Practiced; the review service
        creates exactly one entry at that point, and finding an existing entry
        is what makes re-reaching Practiced resume rather than create a second
        entry (Req 5.4, 9.1, 9.2).
        """
        ...

    def list_for_user(self, user_id: StableId) -> tuple[ReviewSchedule, ...]:
        """Return every review schedule for ``user_id``.

        Backs the dashboard's concepts-due/overdue view (due/overdue derived
        from the Clock, Req 9.6) and the GDPR export of the ``review_schedule``
        and ``review_attempt`` tables (Req 22.1).
        """
        ...

    def save(self, schedule: ReviewSchedule) -> None:
        """Insert or update ``schedule``, persisting its appended attempts.

        Keyed by ``(user_id, concept_id)``: the first save creates the single
        schedule entry on reaching Practiced (Req 9.1); later saves update the
        :attr:`~edu_it_domain.learning.models.ReviewSchedule.due_at` /
        :attr:`~edu_it_domain.learning.models.ReviewSchedule.interval` the pass /
        fail outcome produced and append the new
        :class:`~edu_it_domain.learning.models.ReviewAttempt` entries, never
        creating a second entry for an already-scheduled concept (Req 9.2, 9.4,
        9.5).
        """
        ...
