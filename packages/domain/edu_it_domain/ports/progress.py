"""The learning-progress repository port (Req 1.2, 17.2, 21.3, D3 §8).

A learner's progress through a track's modules and lessons — the
``module_progress`` and ``lesson_completion`` entities of the progress group
(D3 §8) — is read and written through the port declared here. ``module_progress``
carries the module's :attr:`ModuleProgressRecord.state` and the
:attr:`ModuleProgressRecord.tested_out` flag a learner earns by proving mastery
at placement (Req 21.3); ``lesson_completion`` records each completed lesson
under a module's progress (Req 17.2). Both hang off an ``enrollment`` read
through the separate :mod:`~edu_it_domain.ports.enrollment` port
(D3 ``enrollment ||--o{ module_progress ||--o{ lesson_completion``).

Like the identity repositories (ADR 0004) and the sibling learning ports, this
is a framework-free :class:`typing.Protocol` (``@runtime_checkable``, standard
library + domain imports only, no web/API framework per Req 1.8). The
application layer depends on the protocol; the infrastructure layer supplies a
PostgreSQL (SQLAlchemy Core) implementation and, for tests, an in-memory one.
The enrollment, module, and lesson are addressed by their ``stable_id``
(D3 §1.2, Req 1.1); the internal numeric primary key of a row is never exposed.

Edits never reset progress (D3 §10): these rows key on a content item's
``stable_id``, not its ``content_version``, so a re-synced item leaves existing
progress and completions intact (Req 14.3, 26.1).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Final, Protocol, runtime_checkable

from edu_it_domain.learning.models import StableId

__all__ = [
    "MODULE_STATE_COMPLETED",
    "MODULE_STATE_IN_PROGRESS",
    "MODULE_STATE_LOCKED",
    "MODULE_STATE_UNLOCKED",
    "LessonCompletionRecord",
    "ModuleProgressRecord",
    "ProgressRepository",
]

#: A module whose prerequisites are not yet met (Req 2.1). The unlocking service
#: derives this from prerequisite concepts' mastery; decay never relocks a
#: module to this state (Req 2.5).
MODULE_STATE_LOCKED: Final[str] = "locked"

#: A module whose prerequisites are all met but which the learner has not yet
#: started (Req 2.2).
MODULE_STATE_UNLOCKED: Final[str] = "unlocked"

#: A module the learner has started but not completed.
MODULE_STATE_IN_PROGRESS: Final[str] = "in_progress"

#: A module the learner has completed (Req 2.3).
MODULE_STATE_COMPLETED: Final[str] = "completed"


@dataclass(frozen=True, slots=True)
class ModuleProgressRecord:
    """A learner's progress on one module (D3 §8, Req 2.2, 21.3).

    Mirrors a ``module_progress`` row as the domain sees it: the owning
    enrollment and the module by ``stable_id``, the module's :attr:`state`
    (one of the ``MODULE_STATE_*`` constants), and :attr:`tested_out` — ``True``
    when the learner tested out of the module at placement rather than working
    through it step by step (Req 21.3, D3 ``module_progress.tested_out``).
    """

    enrollment_id: StableId
    """The ``stable_id`` of the enrollment this progress belongs to."""

    module_id: StableId
    """The ``stable_id`` of the module this progress tracks."""

    state: str
    """The module's progress state (a ``MODULE_STATE_*`` constant)."""

    tested_out: bool = False
    """Whether the learner tested out of the module at placement (Req 21.3)."""


@dataclass(frozen=True, slots=True)
class LessonCompletionRecord:
    """A learner's completion of one lesson (D3 §8, Req 17.2).

    Mirrors a ``lesson_completion`` row as the domain sees it: the owning module
    progress and the completed lesson by ``stable_id``, and the
    :attr:`completed_at` instant read from the injected ``Clock`` (D3 §1.4). A
    completion is preserved across content edits (Req 14.3, 26.1).
    """

    module_id: StableId
    """The ``stable_id`` of the module whose progress this completion is under."""

    lesson_id: StableId
    """The ``stable_id`` of the completed lesson."""

    completed_at: datetime
    """The instant the lesson was completed, from the injected Clock (D3 §1.4)."""


@runtime_checkable
class ProgressRepository(Protocol):
    """Port for reading and writing module progress and lesson completions (Req 17.2)."""

    def get_module_progress(
        self,
        enrollment_id: StableId,
        module_id: StableId,
    ) -> ModuleProgressRecord | None:
        """Return the module's progress under ``enrollment_id``, or ``None``.

        ``None`` before the learner has any progress recorded for the module;
        the unlocking service then treats the module by its computed lock state.
        """
        ...

    def list_module_progress(
        self,
        enrollment_id: StableId,
    ) -> tuple[ModuleProgressRecord, ...]:
        """Return every module-progress row under ``enrollment_id``.

        Backs the progress read of a learner's module lock states across a track
        (Req 17.2) and the GDPR export of the ``module_progress`` table (Req 22.1).
        """
        ...

    def upsert_module_progress(self, progress: ModuleProgressRecord) -> None:
        """Insert or update the module-progress row for ``progress``.

        Keyed by ``(enrollment_id, module_id)``: recording a state change or the
        :attr:`~ModuleProgressRecord.tested_out` flag updates the existing row
        rather than inserting a duplicate (Req 2.2, 21.3).
        """
        ...

    def add_lesson_completion(self, completion: LessonCompletionRecord) -> None:
        """Record ``completion`` as a completed lesson under a module's progress.

        Recording a completion is idempotent per ``(module_id, lesson_id)``: a
        lesson already completed stays completed and no duplicate row is written,
        and an existing completion is preserved across content edits (Req 14.3,
        26.1).
        """
        ...

    def list_lesson_completions(
        self,
        module_id: StableId,
    ) -> tuple[LessonCompletionRecord, ...]:
        """Return every lesson completion recorded under the module ``module_id``.

        Backs the progress read of completed lessons (Req 17.2) and the GDPR
        export of the ``lesson_completion`` table (Req 22.1).
        """
        ...
