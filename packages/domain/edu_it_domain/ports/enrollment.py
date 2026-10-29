"""The enrollment repository port (Req 1.2, 17.1, D3 §8).

A learner's enrollment in a track — the ``enrollment`` entity of the progress
group (D3 §8) — is read and written through the port declared here. Enrolling a
learner records one ``enrollment`` row tying the learner to the track and the
instant they enrolled (Req 17.1); the per-module progress and lesson completions
that hang off an enrollment are read and written through the separate
:mod:`~edu_it_domain.ports.progress` port.

Like the identity repositories (ADR 0004) and the sibling learning ports, this
is a framework-free :class:`typing.Protocol` (``@runtime_checkable``, standard
library + domain imports only, no web/API framework per Req 1.8). The
application layer depends on the protocol; the infrastructure layer supplies a
PostgreSQL (SQLAlchemy Core) implementation and, for tests, an in-memory one.
The learner and the track are addressed by their ``stable_id`` (D3 §1.2,
Req 1.1) and the enrollment by its own ``stable_id``; the internal numeric
primary key of a row is never exposed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from edu_it_domain.learning.models import StableId

__all__ = [
    "EnrollmentRecord",
    "EnrollmentRepository",
]


@dataclass(frozen=True, slots=True)
class EnrollmentRecord:
    """A learner's enrollment in a track (D3 §8, Req 17.1).

    Mirrors an ``enrollment`` row as the domain sees it: the enrollment by its
    own :attr:`stable_id`, the enrolled learner and the track by ``stable_id``,
    and the :attr:`enrolled_at` instant read from the injected ``Clock``
    (D3 §1.4). The internal numeric key is never surfaced.
    """

    stable_id: StableId
    """The enrollment's immutable public identifier."""

    user_id: StableId
    """The ``stable_id`` of the enrolled learner."""

    track_id: StableId
    """The ``stable_id`` of the track the learner is enrolled in."""

    enrolled_at: datetime
    """The instant the learner enrolled, from the injected Clock (D3 §1.4)."""


@runtime_checkable
class EnrollmentRepository(Protocol):
    """Port for reading and writing durable ``enrollment`` records (Req 17.1)."""

    def add(self, enrollment: EnrollmentRecord) -> None:
        """Insert ``enrollment`` as a new durable record.

        Raises :class:`ValueError` when an enrollment with the same
        ``stable_id`` already exists, or when the learner is already enrolled in
        the track, so a duplicate enrollment cannot be created.
        """
        ...

    def get(self, stable_id: StableId) -> EnrollmentRecord | None:
        """Return the enrollment with ``stable_id``, or ``None`` when absent."""
        ...

    def get_for_user_and_track(
        self,
        user_id: StableId,
        track_id: StableId,
    ) -> EnrollmentRecord | None:
        """Return the learner's enrollment in the track, or ``None`` when absent.

        Lets the enroll use case detect an existing enrollment before creating a
        second one, and the progress read resolve the enrollment a learner's
        module progress hangs off (D3 ``enrollment ||--o{ module_progress``).
        """
        ...

    def list_for_user(self, user_id: StableId) -> tuple[EnrollmentRecord, ...]:
        """Return every enrollment owned by ``user_id``.

        Backs a learner's dashboard across the tracks they are enrolled in, and
        the GDPR export of the ``enrollment`` table (Req 22.1).
        """
        ...
