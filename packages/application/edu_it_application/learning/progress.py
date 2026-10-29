"""The enroll and progress use cases (Req 17.1, 17.2, 2.1-2.5, 21.3).

:class:`EnrollUseCase` enrolls a learner in a track and seeds a ``module_progress``
row per module with its computed lock state. :class:`GetProgressUseCase` returns
the learner's module lock states, lesson completions, and per-concept mastery.

Lock state is always *derived* from the learner's current prerequisite mastery by
the domain's unlocking service (Req 2.1, 2.2): it never depends on completions
(Req 2.4) and a decay dip cannot relock a module (Req 2.5). A module the learner
has started or completed keeps that stored state, and a module they tested out of
at placement is never reported locked (Req 21.3).
"""

from __future__ import annotations

from dataclasses import dataclass

from edu_it_domain.learning.models import Mastery, StableId
from edu_it_domain.learning.unlocking import module_lock_state
from edu_it_domain.ports.catalog import CatalogRepository, ModuleRecord
from edu_it_domain.ports.clock import Clock
from edu_it_domain.ports.enrollment import EnrollmentRecord, EnrollmentRepository
from edu_it_domain.ports.id_generator import IdGenerator
from edu_it_domain.ports.mastery import MasteryRepository
from edu_it_domain.ports.progress import (
    MODULE_STATE_COMPLETED,
    MODULE_STATE_IN_PROGRESS,
    MODULE_STATE_LOCKED,
    MODULE_STATE_UNLOCKED,
    LessonCompletionRecord,
    ModuleProgressRecord,
    ProgressRepository,
)

from edu_it_application.learning.errors import NotEnrolledError, NotFoundError

__all__ = [
    "EnrollResult",
    "EnrollUseCase",
    "GetProgressUseCase",
    "ModuleProgressView",
    "ProgressView",
]

#: Stored states a learner has earned by working in the module; they outrank the
#: computed lock state (a started or completed module is never shown as locked).
_STICKY_STATES = frozenset({MODULE_STATE_IN_PROGRESS, MODULE_STATE_COMPLETED})


def _effective_state(
    module: ModuleRecord,
    stored: ModuleProgressRecord | None,
    mastery_by_concept: dict[StableId, Mastery],
) -> tuple[str, bool]:
    """Return ``(state, tested_out)`` for ``module`` (Req 2.1, 2.2, 2.5, 21.3)."""
    tested_out = stored.tested_out if stored is not None else False
    if stored is not None and stored.state in _STICKY_STATES:
        return stored.state, tested_out
    computed = module_lock_state(module, mastery_by_concept)
    if tested_out and computed == MODULE_STATE_LOCKED:
        return MODULE_STATE_UNLOCKED, tested_out
    return computed, tested_out


def _mastery_by_concept(repo: MasteryRepository, user_id: StableId) -> dict[StableId, Mastery]:
    return {m.concept_id: m for m in repo.list_for_user(user_id)}


@dataclass(frozen=True, slots=True)
class EnrollResult:
    """The outcome of enrolling (Req 17.1)."""

    enrollment: EnrollmentRecord
    """The learner's enrollment in the track."""

    created: bool
    """``False`` when the learner was already enrolled (enrolling is idempotent)."""


class EnrollUseCase:
    """Enroll a learner in a track and seed its module progress (Req 17.1)."""

    def __init__(
        self,
        *,
        catalog: CatalogRepository,
        enrollments: EnrollmentRepository,
        progress: ProgressRepository,
        mastery: MasteryRepository,
        clock: Clock,
        ids: IdGenerator,
    ) -> None:
        """Wire the ports the use case orchestrates."""
        self._catalog = catalog
        self._enrollments = enrollments
        self._progress = progress
        self._mastery = mastery
        self._clock = clock
        self._ids = ids

    def execute(self, user_id: StableId, track_id: StableId) -> EnrollResult:
        """Enroll ``user_id`` in ``track_id``; return the existing enrollment if any.

        Raises:
            NotFoundError: the track has no modules, so it does not exist.
        """
        existing = self._enrollments.get_for_user_and_track(user_id, track_id)
        if existing is not None:
            return EnrollResult(enrollment=existing, created=False)

        modules = self._catalog.list_modules_for_track(track_id)
        if not modules:
            raise NotFoundError("track", track_id)

        enrollment = EnrollmentRecord(
            stable_id=self._ids.new_id(),
            user_id=user_id,
            track_id=track_id,
            enrolled_at=self._clock.now(),
        )
        self._enrollments.add(enrollment)

        mastery = _mastery_by_concept(self._mastery, user_id)
        for module in modules:
            state, tested_out = _effective_state(module, None, mastery)
            self._progress.upsert_module_progress(
                ModuleProgressRecord(
                    enrollment_id=enrollment.stable_id,
                    module_id=module.stable_id,
                    state=state,
                    tested_out=tested_out,
                )
            )
        return EnrollResult(enrollment=enrollment, created=True)


@dataclass(frozen=True, slots=True)
class ModuleProgressView:
    """One module's lock state and completions in a progress read (Req 17.2)."""

    module_id: StableId
    ordinal: int
    state: str
    """One of the ``MODULE_STATE_*`` constants."""

    tested_out: bool
    completions: tuple[LessonCompletionRecord, ...]


@dataclass(frozen=True, slots=True)
class ProgressView:
    """A learner's progress through a track (Req 17.2)."""

    enrollment_id: StableId
    track_id: StableId
    modules: tuple[ModuleProgressView, ...]
    """Module lock states and lesson completions, in authored order."""

    mastery: tuple[Mastery, ...]
    """Per-concept mastery for the learner."""


class GetProgressUseCase:
    """Read a learner's module lock states, completions, and mastery (Req 17.2)."""

    def __init__(
        self,
        *,
        catalog: CatalogRepository,
        enrollments: EnrollmentRepository,
        progress: ProgressRepository,
        mastery: MasteryRepository,
    ) -> None:
        """Wire the ports the use case reads."""
        self._catalog = catalog
        self._enrollments = enrollments
        self._progress = progress
        self._mastery = mastery

    def execute(self, user_id: StableId, track_id: StableId) -> ProgressView:
        """Return the progress view, with lock states derived from current mastery.

        Raises:
            NotEnrolledError: the learner is not enrolled in the track.
        """
        enrollment = self._enrollments.get_for_user_and_track(user_id, track_id)
        if enrollment is None:
            raise NotEnrolledError(user_id, track_id)

        mastery = self._mastery.list_for_user(user_id)
        by_concept = {m.concept_id: m for m in mastery}
        stored = {p.module_id: p for p in self._progress.list_module_progress(enrollment.stable_id)}

        views: list[ModuleProgressView] = []
        for module in self._catalog.list_modules_for_track(track_id):
            state, tested_out = _effective_state(module, stored.get(module.stable_id), by_concept)
            views.append(
                ModuleProgressView(
                    module_id=module.stable_id,
                    ordinal=module.ordinal,
                    state=state,
                    tested_out=tested_out,
                    completions=self._progress.list_lesson_completions(module.stable_id),
                )
            )
        return ProgressView(
            enrollment_id=enrollment.stable_id,
            track_id=track_id,
            modules=tuple(views),
            mastery=mastery,
        )
