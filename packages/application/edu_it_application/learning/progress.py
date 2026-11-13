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
from edu_it_domain.ports.catalog_titles import CatalogTitleReader
from edu_it_domain.ports.clock import Clock
from edu_it_domain.ports.enrollment import EnrollmentRecord, EnrollmentRepository
from edu_it_domain.ports.id_generator import IdGenerator
from edu_it_domain.ports.lab_catalog import LabCatalog
from edu_it_domain.ports.lab_sessions import LabCompletionReader
from edu_it_domain.ports.lessons import ModuleLessonReader
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
from edu_it_application.learning.get_lesson import FALLBACK_LOCALE, LabLinkView, lab_links

__all__ = [
    "EnrollResult",
    "EnrollUseCase",
    "GetProgressUseCase",
    "LabProgressView",
    "ModuleLessonView",
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
class ModuleLessonView:
    """One lesson of a module, with the quiz and labs a learner reaches from it."""

    lesson_id: StableId
    title: str
    """The title in the requested locale, falling back to English (Req 17.3)."""
    quiz_id: StableId | None
    lab_ids: tuple[StableId, ...]
    labs: tuple[LabLinkView, ...] = ()
    """The lesson's labs with their titles; empty without a lab catalog."""


@dataclass(frozen=True, slots=True)
class ModuleProgressView:
    """One module's lock state and completions in a progress read (Req 17.2)."""

    module_id: StableId
    ordinal: int
    state: str
    """One of the ``MODULE_STATE_*`` constants."""

    tested_out: bool
    completions: tuple[LessonCompletionRecord, ...]
    lessons: tuple[ModuleLessonView, ...] = ()
    """The module's lessons in authored order; empty without a lesson reader."""
    title: str = ""
    """The module title in the requested locale (English fallback); empty without titles."""


@dataclass(frozen=True, slots=True)
class LabProgressView:
    """One lab's completed tasks and the mastery of the concepts it covers (labs Req 19.7)."""

    lab_id: StableId
    completed_task_ids: tuple[StableId, ...]
    """Tasks the learner completed, in the lab's authored task order."""

    passed: bool
    """Whether every required task is completed."""

    mastery: tuple[Mastery, ...]
    """Current mastery of the lab's covered concepts that the learner has a record for."""


@dataclass(frozen=True, slots=True)
class ProgressView:
    """A learner's progress through a track (Req 17.2)."""

    enrollment_id: StableId
    track_id: StableId
    modules: tuple[ModuleProgressView, ...]
    """Module lock states and lesson completions, in authored order."""

    mastery: tuple[Mastery, ...]
    """Per-concept mastery for the learner."""

    labs: tuple[LabProgressView, ...] = ()
    """Lab completion and lab-derived mastery (labs Req 19.7); empty without a lab catalog."""
    track_title: str = ""
    """The track title in the requested locale (English fallback); empty without titles."""


class GetProgressUseCase:
    """Read a learner's module lock states, completions, and mastery (Req 17.2)."""

    def __init__(
        self,
        *,
        catalog: CatalogRepository,
        enrollments: EnrollmentRepository,
        progress: ProgressRepository,
        mastery: MasteryRepository,
        labs: LabCatalog | None = None,
        lab_completions: LabCompletionReader | None = None,
        lessons: ModuleLessonReader | None = None,
        titles: CatalogTitleReader | None = None,
    ) -> None:
        """Wire the ports the use case reads; the lab, lesson and title ports are optional."""
        self._titles = titles
        self._lessons = lessons
        self._labs = labs
        self._lab_completions = lab_completions
        self._catalog = catalog
        self._enrollments = enrollments
        self._progress = progress
        self._mastery = mastery

    def execute(
        self, user_id: StableId, track_id: StableId, locale: str = FALLBACK_LOCALE
    ) -> ProgressView:
        """Return the progress view, with lock states derived from current mastery.

        ``locale`` picks the lesson titles; a lesson without that translation shows English.

        Raises:
            NotEnrolledError: the learner is not enrolled in the track.
        """
        enrollment = self._enrollments.get_for_user_and_track(user_id, track_id)
        if enrollment is None:
            raise NotEnrolledError(user_id, track_id)

        mastery = self._mastery.list_for_user(user_id)
        by_concept = {m.concept_id: m for m in mastery}
        # Lesson completions hang off one enrollment's module progress. Adapters that
        # share a module across enrollments expose ``for_enrollment``; bind it so the
        # read never guesses between learners (Req 17.2). Fakes keyed by module need no bind.
        bind = getattr(self._progress, "for_enrollment", None)
        progress = bind(enrollment.stable_id) if callable(bind) else self._progress
        stored = {p.module_id: p for p in progress.list_module_progress(enrollment.stable_id)}

        views: list[ModuleProgressView] = []
        for module in self._catalog.list_modules_for_track(track_id):
            state, tested_out = _effective_state(module, stored.get(module.stable_id), by_concept)
            views.append(
                ModuleProgressView(
                    module_id=module.stable_id,
                    ordinal=module.ordinal,
                    state=state,
                    tested_out=tested_out,
                    completions=progress.list_lesson_completions(module.stable_id),
                    lessons=self._lesson_views(module.stable_id, locale),
                    title=self._title("module", module.stable_id, locale),
                )
            )
        return ProgressView(
            enrollment_id=enrollment.stable_id,
            track_id=track_id,
            modules=tuple(views),
            mastery=mastery,
            labs=self._lab_views(user_id, by_concept),
            track_title=self._title("track", track_id, locale),
        )

    def _lesson_views(self, module_id: StableId, locale: str) -> tuple[ModuleLessonView, ...]:
        if self._lessons is None:
            return ()
        return tuple(
            ModuleLessonView(
                lesson_id=lesson.stable_id,
                title=lesson.titles.get(locale) or lesson.titles.get(FALLBACK_LOCALE, ""),
                quiz_id=lesson.quiz_id,
                lab_ids=tuple(lesson.lab_ids),
                labs=lab_links(self._labs, lesson.lab_ids, locale),
            )
            for lesson in self._lessons.list_lessons_for_module(module_id)
        )

    def _title(self, kind: str, stable_id: StableId, locale: str) -> str:
        if self._titles is None:
            return ""
        titles = self._titles.titles(kind, stable_id)
        return titles.get(locale) or titles.get(FALLBACK_LOCALE, "")

    def _lab_views(
        self, user_id: StableId, by_concept: dict[StableId, Mastery]
    ) -> tuple[LabProgressView, ...]:
        if self._labs is None or self._lab_completions is None:
            return ()
        done = {c.task_id for c in self._lab_completions.list_task_completions(user_id)}
        views: list[LabProgressView] = []
        for lab in self._labs.list_labs():
            required = {t.task_id for t in lab.tasks if t.required}
            views.append(
                LabProgressView(
                    lab_id=lab.lab_id,
                    completed_task_ids=tuple(t.task_id for t in lab.tasks if t.task_id in done),
                    passed=bool(required) and required <= done,
                    mastery=tuple(by_concept[c] for c in lab.concept_ids if c in by_concept),
                )
            )
        return tuple(views)
