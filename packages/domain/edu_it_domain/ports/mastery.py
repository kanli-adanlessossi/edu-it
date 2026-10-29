"""The mastery repository port (Req 1.2, 5.1, 5.7, 7.1, D3 §7).

A learner's mastery of a concept and the append-only history behind it — the
``mastery``, ``mastery_event``, and ``hint_reveal`` entities of the mastery and
review group (D3 §7) — are read and written through the port declared here.
``mastery`` holds the integer score 0-100 per learner x concept and its derived
state (Req 5.1); ``mastery_event`` is the append-only audit of every change
(Req 5.7); ``hint_reveal`` records each revealed hint, which also produces a
mastery event so hint use is reflected in progress (Req 7.1, D3 §7.4).

The domain mastery types are reused unchanged from
:mod:`edu_it_domain.learning.models` — :class:`~edu_it_domain.learning.models.Mastery`
(with its embedded :class:`~edu_it_domain.learning.models.MasteryEvent` history)
— so this port reads and writes those domain values and does not redefine them.
Only the ``hint_reveal`` record, which has no domain model type yet, is declared
here.

Like the identity repositories (ADR 0004) and the sibling learning ports, this
is a framework-free :class:`typing.Protocol` (``@runtime_checkable``, standard
library + domain imports only, no web/API framework per Req 1.8). The
application layer depends on the protocol; the infrastructure layer supplies a
PostgreSQL (SQLAlchemy Core) implementation and, for tests, an in-memory one.
The learner, concept, and question are addressed by their ``stable_id``
(D3 §1.2, Req 1.1); the internal numeric primary key of a row is never exposed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from edu_it_domain.learning.models import Mastery, StableId

__all__ = [
    "HintRevealRecord",
    "MasteryRepository",
]


@dataclass(frozen=True, slots=True)
class HintRevealRecord:
    """One hint a learner revealed on a question (D3 §7.4, Req 7.1).

    Mirrors a ``hint_reveal`` row as the domain sees it: the learner and the
    question by ``stable_id``, and the :attr:`revealed_at` instant read from the
    injected ``Clock`` (D3 §1.4). A reveal produces a corresponding
    :class:`~edu_it_domain.learning.models.MasteryEvent`, so revealing a hint is
    reflected in the learner's mastery history (Req 7.1, D3 §7.4).
    """

    user_id: StableId
    """The ``stable_id`` of the learner who revealed the hint."""

    question_id: StableId
    """The ``stable_id`` of the question the hint was revealed on."""

    revealed_at: datetime
    """The instant the hint was revealed, from the injected Clock (D3 §1.4)."""


@runtime_checkable
class MasteryRepository(Protocol):
    """Port for reading and writing a learner's ``mastery`` state and history (Req 5.1, 5.7)."""

    def get(self, user_id: StableId, concept_id: StableId) -> Mastery | None:
        """Return the learner's mastery of the concept, or ``None`` when untracked.

        ``None`` before the concept has been encountered
        (:attr:`~edu_it_domain.learning.models.MasteryState.UNSEEN`); the mastery
        service then begins tracking it on first encounter (Req 5.2).
        """
        ...

    def list_for_user(self, user_id: StableId) -> tuple[Mastery, ...]:
        """Return every tracked mastery for ``user_id``.

        Backs the per-concept mastery of the progress read (Req 17.2) and the
        GDPR export of the ``mastery`` and ``mastery_event`` tables (Req 22.1).
        """
        ...

    def save(self, mastery: Mastery) -> None:
        """Insert or update ``mastery``, persisting its appended events.

        Keyed by ``(user_id, concept_id)``: saving applies the new score and
        state and appends the new :class:`~edu_it_domain.learning.models.MasteryEvent`
        entries carried on :attr:`~edu_it_domain.learning.models.Mastery.events`.
        The ``mastery_event`` table is append-only, so a save adds events and
        never rewrites history (Req 5.7). Every mastery change appends exactly
        one event (Req 5.7).
        """
        ...

    def add_hint_reveal(self, reveal: HintRevealRecord) -> None:
        """Record ``reveal`` as a revealed hint (Req 7.1).

        The corresponding mastery event is appended through :meth:`save`; this
        records the ``hint_reveal`` row itself, backing the GDPR export of the
        ``hint_reveal`` table (Req 22.1).
        """
        ...

    def list_hint_reveals(self, user_id: StableId) -> tuple[HintRevealRecord, ...]:
        """Return every hint reveal by ``user_id``, oldest first.

        Backs the hint multiplier -- a hint revealed before an attempt was
        submitted tempers that attempt's gain (Req 7.2, 7.4) -- the progressive
        hint level, and the GDPR export of the ``hint_reveal`` table (Req 22.1).
        """
        ...
