"""In-memory learning-state repository fakes (Req 1.4, 5.2, 11.4).

These fakes implement the learning persistence ports with plain in-process
dictionaries so the Milestone A domain services (unlocking, scoring, mastery,
review, rubric) and the Milestone C application use cases can be exercised in
tests and local development without a database:

- :class:`In_Memory_CatalogRepository` implements
  :class:`~edu_it_domain.ports.catalog.CatalogRepository`.
- :class:`In_Memory_EnrollmentRepository` implements
  :class:`~edu_it_domain.ports.enrollment.EnrollmentRepository`.
- :class:`In_Memory_ProgressRepository` implements
  :class:`~edu_it_domain.ports.progress.ProgressRepository`.
- :class:`In_Memory_MasteryRepository` implements
  :class:`~edu_it_domain.ports.mastery.MasteryRepository`.
- :class:`In_Memory_ReviewRepository` implements
  :class:`~edu_it_domain.ports.review.ReviewRepository`.
- :class:`In_Memory_AttemptRepository` implements
  :class:`~edu_it_domain.ports.attempts.AttemptRepository`.

Each fake is substitutable for its PostgreSQL-backed production counterpart
without changing domain or application code (Req 5.2): it satisfies the same
protocol and enforces the same invariants the port docstrings specify — unique
``stable_id`` on enrollment/attempt/submission inserts, one enrollment per
``(user, track)``, a single review-schedule entry keyed by ``(user, concept)``
that resumes rather than duplicates, idempotent lesson completions per
``(module, lesson)``, and append-only mastery/review history on save. The fakes
hold no internal numeric key — every record is addressed by its ``stable_id`` —
matching the domain's view (Req 1.1).
"""

from __future__ import annotations

from datetime import datetime

from edu_it_domain.learning.models import (
    Answer,
    Mastery,
    ProjectSubmissionRef,
    QuizAttempt,
    ReviewSchedule,
    Rubric,
    StableId,
)
from edu_it_domain.ports.attempts import AttemptRepository
from edu_it_domain.ports.catalog import (
    CatalogRepository,
    ConceptRecord,
    ModuleRecord,
    QuestionRecord,
    QuizRecord,
)
from edu_it_domain.ports.enrollment import EnrollmentRecord, EnrollmentRepository
from edu_it_domain.ports.mastery import HintRevealRecord, MasteryRepository
from edu_it_domain.ports.progress import (
    LessonCompletionRecord,
    ModuleProgressRecord,
    ProgressRepository,
)
from edu_it_domain.ports.review import ReviewRepository

__all__ = [
    "In_Memory_AttemptRepository",
    "In_Memory_CatalogRepository",
    "In_Memory_EnrollmentRepository",
    "In_Memory_MasteryRepository",
    "In_Memory_ProgressRepository",
    "In_Memory_ReviewRepository",
]


class In_Memory_CatalogRepository(CatalogRepository):
    """A :class:`CatalogRepository` backed by in-process dictionaries (Req 1.2, 5.2).

    Satisfies the :class:`~edu_it_domain.ports.catalog.CatalogRepository`
    protocol, so the domain services read authored content by ``stable_id``
    exactly as against the PostgreSQL adapter. The catalog is read-only to the
    domain; the ``add_*`` helpers here are the test-fixture seam the content
    pipeline fills in production, and reject a duplicate ``stable_id`` so a fixture
    cannot register the same item twice.
    """

    __test__ = False

    def __init__(self) -> None:
        """Create an empty catalog."""
        self._modules: dict[StableId, ModuleRecord] = {}
        self._concepts: dict[StableId, ConceptRecord] = {}
        self._quizzes: dict[StableId, QuizRecord] = {}
        self._quiz_versions: dict[tuple[StableId, str], QuizRecord] = {}
        self._rubrics_by_project: dict[StableId, Rubric] = {}

    def add_module(self, module: ModuleRecord) -> None:
        """Register ``module``; reject a reused ``stable_id``."""
        if module.stable_id in self._modules:
            raise ValueError(f"module with stable_id {module.stable_id!r} already exists")
        self._modules[module.stable_id] = module

    def add_concept(self, concept: ConceptRecord) -> None:
        """Register ``concept``; reject a reused ``stable_id``."""
        if concept.stable_id in self._concepts:
            raise ValueError(f"concept with stable_id {concept.stable_id!r} already exists")
        self._concepts[concept.stable_id] = concept

    def add_quiz(self, quiz: QuizRecord) -> None:
        """Register ``quiz``; reject a reused ``stable_id``."""
        if quiz.stable_id in self._quizzes:
            raise ValueError(f"quiz with stable_id {quiz.stable_id!r} already exists")
        self._quizzes[quiz.stable_id] = quiz
        self._quiz_versions[(quiz.stable_id, quiz.content_version)] = quiz

    def revise_quiz(self, quiz: QuizRecord) -> None:
        """Replace the current revision of an existing quiz (a ``content sync`` edit).

        The superseded revision stays retrievable by version -- archived, never
        deleted -- so in-flight attempts remain pinned to it (Req 25.1, 26.3).
        """
        if quiz.stable_id not in self._quizzes:
            raise ValueError(f"quiz with stable_id {quiz.stable_id!r} does not exist")
        self._quizzes[quiz.stable_id] = quiz
        self._quiz_versions[(quiz.stable_id, quiz.content_version)] = quiz

    def add_rubric(self, rubric: Rubric) -> None:
        """Register ``rubric`` for its project; reject a second rubric per project."""
        if rubric.project_id in self._rubrics_by_project:
            raise ValueError(f"project {rubric.project_id!r} already has a rubric")
        self._rubrics_by_project[rubric.project_id] = rubric

    def get_module(self, stable_id: StableId) -> ModuleRecord | None:
        """Return the module with ``stable_id``, or ``None`` when absent."""
        return self._modules.get(stable_id)

    def list_modules_for_track(self, track_id: StableId) -> tuple[ModuleRecord, ...]:
        """Return every module of ``track_id``, ordered by ordinal."""
        modules = [m for m in self._modules.values() if m.track_id == track_id]
        return tuple(sorted(modules, key=lambda m: m.ordinal))

    def get_concept(self, stable_id: StableId) -> ConceptRecord | None:
        """Return the concept with ``stable_id`` and its prerequisite edges, or ``None``."""
        return self._concepts.get(stable_id)

    def get_quiz(self, stable_id: StableId) -> QuizRecord | None:
        """Return the quiz with ``stable_id`` and its questions/options, or ``None``."""
        return self._quizzes.get(stable_id)

    def get_quiz_at_version(self, stable_id: StableId, content_version: str) -> QuizRecord | None:
        """Return the quiz as of ``content_version``, or ``None`` when unknown."""
        return self._quiz_versions.get((stable_id, content_version))

    def list_questions_for_concept(self, concept_id: StableId) -> tuple[QuestionRecord, ...]:
        """Return every question covering ``concept_id``, ordered by ``stable_id``."""
        pool = {
            question.stable_id: question
            for quiz in self._quizzes.values()
            for question in quiz.questions
            if concept_id in question.concept_ids
        }
        return tuple(pool[stable_id] for stable_id in sorted(pool))

    def get_rubric_for_project(self, project_id: StableId) -> Rubric | None:
        """Return the rubric scoring ``project_id``, or ``None`` when absent."""
        return self._rubrics_by_project.get(project_id)


class In_Memory_EnrollmentRepository(EnrollmentRepository):
    """An :class:`EnrollmentRepository` backed by in-process dictionaries (Req 17.1, 5.2).

    Satisfies the :class:`~edu_it_domain.ports.enrollment.EnrollmentRepository`
    protocol. :meth:`add` rejects a reused ``stable_id`` and a second enrollment
    of the same learner in the same track, so a duplicate enrollment cannot be
    created, matching the PostgreSQL adapter's contract.
    """

    __test__ = False

    def __init__(self) -> None:
        """Create an empty enrollment repository."""
        self._by_stable_id: dict[StableId, EnrollmentRecord] = {}
        self._by_user_track: dict[tuple[StableId, StableId], EnrollmentRecord] = {}

    def add(self, enrollment: EnrollmentRecord) -> None:
        """Insert ``enrollment``; reject a duplicate ``stable_id`` or ``(user, track)``.

        Raises :class:`ValueError` when an enrollment with the same ``stable_id``
        already exists, or when the learner is already enrolled in the track.
        """
        if enrollment.stable_id in self._by_stable_id:
            raise ValueError(f"enrollment with stable_id {enrollment.stable_id!r} already exists")
        key = (enrollment.user_id, enrollment.track_id)
        if key in self._by_user_track:
            raise ValueError(
                f"user {enrollment.user_id!r} is already enrolled in track {enrollment.track_id!r}"
            )
        self._by_stable_id[enrollment.stable_id] = enrollment
        self._by_user_track[key] = enrollment

    def get(self, stable_id: StableId) -> EnrollmentRecord | None:
        """Return the enrollment with ``stable_id``, or ``None`` when absent."""
        return self._by_stable_id.get(stable_id)

    def get_for_user_and_track(
        self,
        user_id: StableId,
        track_id: StableId,
    ) -> EnrollmentRecord | None:
        """Return the learner's enrollment in the track, or ``None`` when absent."""
        return self._by_user_track.get((user_id, track_id))

    def list_for_user(self, user_id: StableId) -> tuple[EnrollmentRecord, ...]:
        """Return every enrollment owned by ``user_id``."""
        return tuple(e for e in self._by_stable_id.values() if e.user_id == user_id)

    def list_for_user_page(
        self,
        user_id: StableId,
        *,
        after: tuple[datetime, StableId] | None = None,
        limit: int = 50,
    ) -> tuple[EnrollmentRecord, ...]:
        """Return up to ``limit`` enrollments strictly after the keyset ``after``."""
        rows = sorted(self.list_for_user(user_id), key=lambda e: (e.enrolled_at, e.stable_id))
        if after is not None:
            rows = [e for e in rows if (e.enrolled_at, e.stable_id) > after]
        return tuple(rows[:limit])


class In_Memory_ProgressRepository(ProgressRepository):
    """A :class:`ProgressRepository` backed by in-process dictionaries (Req 17.2, 5.2).

    Satisfies the :class:`~edu_it_domain.ports.progress.ProgressRepository`
    protocol. Module progress is keyed by ``(enrollment_id, module_id)`` so
    :meth:`upsert_module_progress` updates in place rather than duplicating;
    lesson completion is idempotent per ``(module_id, lesson_id)`` so a completed
    lesson stays completed and is preserved across content edits (Req 14.3, 26.1).
    """

    __test__ = False

    def __init__(self) -> None:
        """Create an empty progress repository."""
        self._module_progress: dict[tuple[StableId, StableId], ModuleProgressRecord] = {}
        self._completions: dict[tuple[StableId, StableId], LessonCompletionRecord] = {}

    def get_module_progress(
        self,
        enrollment_id: StableId,
        module_id: StableId,
    ) -> ModuleProgressRecord | None:
        """Return the module's progress under ``enrollment_id``, or ``None``."""
        return self._module_progress.get((enrollment_id, module_id))

    def list_module_progress(
        self,
        enrollment_id: StableId,
    ) -> tuple[ModuleProgressRecord, ...]:
        """Return every module-progress row under ``enrollment_id``."""
        return tuple(
            p
            for (enr_id, _module_id), p in self._module_progress.items()
            if enr_id == enrollment_id
        )

    def upsert_module_progress(self, progress: ModuleProgressRecord) -> None:
        """Insert or update the module-progress row for ``progress``.

        Keyed by ``(enrollment_id, module_id)`` so a state or ``tested_out``
        change updates the existing row rather than inserting a duplicate (Req
        2.2, 21.3).
        """
        self._module_progress[(progress.enrollment_id, progress.module_id)] = progress

    def add_lesson_completion(self, completion: LessonCompletionRecord) -> None:
        """Record ``completion`` idempotently per ``(module_id, lesson_id)``.

        A lesson already completed stays completed and no duplicate row is
        written, preserving the earliest completion across content edits (Req
        14.3, 26.1).
        """
        key = (completion.module_id, completion.lesson_id)
        if key in self._completions:
            return
        self._completions[key] = completion

    def list_lesson_completions(
        self,
        module_id: StableId,
    ) -> tuple[LessonCompletionRecord, ...]:
        """Return every lesson completion recorded under ``module_id``."""
        return tuple(
            c for (mod_id, _lesson_id), c in self._completions.items() if mod_id == module_id
        )


class In_Memory_MasteryRepository(MasteryRepository):
    """A :class:`MasteryRepository` backed by in-process dictionaries (Req 5.1, 5.7, 5.2).

    Satisfies the :class:`~edu_it_domain.ports.mastery.MasteryRepository`
    protocol. Mastery is keyed by ``(user_id, concept_id)``; :meth:`save` is an
    upsert that treats the ``mastery_event`` history as append-only — a save
    never drops or rewrites previously stored events, so history only grows (Req
    5.7). Hint reveals are recorded append-only for the GDPR export (Req 7.1,
    22.1).
    """

    __test__ = False

    def __init__(self) -> None:
        """Create an empty mastery repository."""
        self._by_user_concept: dict[tuple[StableId, StableId], Mastery] = {}
        self._hint_reveals: list[HintRevealRecord] = []

    def get(self, user_id: StableId, concept_id: StableId) -> Mastery | None:
        """Return the learner's mastery of the concept, or ``None`` when untracked."""
        return self._by_user_concept.get((user_id, concept_id))

    def list_for_user(self, user_id: StableId) -> tuple[Mastery, ...]:
        """Return every tracked mastery for ``user_id``."""
        return tuple(m for (uid, _concept_id), m in self._by_user_concept.items() if uid == user_id)

    def save(self, mastery: Mastery) -> None:
        """Insert or update ``mastery``, keeping its event history append-only.

        Keyed by ``(user_id, concept_id)``. The stored events are never a prefix
        shorter than what was already persisted: saving a mastery whose
        :attr:`~edu_it_domain.learning.models.Mastery.events` drops previously
        stored events is rejected, so the ``mastery_event`` log is append-only
        (Req 5.7).
        """
        key = (mastery.user_id, mastery.concept_id)
        existing = self._by_user_concept.get(key)
        if existing is not None and not self._is_append_only(existing.events, mastery.events):
            raise ValueError(
                "mastery_event history is append-only; a save may only extend the "
                f"existing events for ({mastery.user_id!r}, {mastery.concept_id!r})"
            )
        self._by_user_concept[key] = mastery

    def add_hint_reveal(self, reveal: HintRevealRecord) -> None:
        """Record ``reveal`` as a revealed hint, append-only (Req 7.1, 22.1)."""
        self._hint_reveals.append(reveal)

    def list_hint_reveals(self, user_id: StableId) -> tuple[HintRevealRecord, ...]:
        """Return every hint reveal by ``user_id``, for the GDPR export (Req 22.1)."""
        return tuple(r for r in self._hint_reveals if r.user_id == user_id)

    @staticmethod
    def _is_append_only(
        existing: tuple[object, ...],
        incoming: tuple[object, ...],
    ) -> bool:
        """Return whether ``incoming`` extends ``existing`` without rewriting it."""
        return len(incoming) >= len(existing) and incoming[: len(existing)] == existing


class In_Memory_ReviewRepository(ReviewRepository):
    """A :class:`ReviewRepository` backed by an in-process dictionary (Req 9.1, 9.4, 5.2).

    Satisfies the :class:`~edu_it_domain.ports.review.ReviewRepository` protocol.
    A schedule is keyed by ``(user_id, concept_id)``, so the first :meth:`save`
    creates the single entry on reaching Practiced and a later save resumes that
    same entry — updating its ``due_at`` / ``interval`` and appending attempts —
    rather than creating a second entry (Req 5.4, 9.1, 9.2). The attempt history
    is append-only: a save may only extend it (Req 9.4, 9.5).
    """

    __test__ = False

    def __init__(self) -> None:
        """Create an empty review repository."""
        self._by_user_concept: dict[tuple[StableId, StableId], ReviewSchedule] = {}

    def get(self, user_id: StableId, concept_id: StableId) -> ReviewSchedule | None:
        """Return the learner's review schedule for the concept, or ``None``."""
        return self._by_user_concept.get((user_id, concept_id))

    def list_for_user(self, user_id: StableId) -> tuple[ReviewSchedule, ...]:
        """Return every review schedule for ``user_id``."""
        return tuple(s for (uid, _concept_id), s in self._by_user_concept.items() if uid == user_id)

    def save(self, schedule: ReviewSchedule) -> None:
        """Insert or update ``schedule``, keeping its attempt history append-only.

        Keyed by ``(user_id, concept_id)``: the first save creates the single
        entry (Req 9.1); a later save updates the existing entry's ``due_at`` /
        ``interval`` and appends new attempts, never creating a second entry for
        an already-scheduled concept (Req 9.2). Saving a schedule whose
        :attr:`~edu_it_domain.learning.models.ReviewSchedule.history` drops
        previously stored attempts is rejected (Req 9.4, 9.5).
        """
        key = (schedule.user_id, schedule.concept_id)
        existing = self._by_user_concept.get(key)
        if existing is not None and not self._is_append_only(existing.history, schedule.history):
            raise ValueError(
                "review_attempt history is append-only; a save may only extend the "
                f"existing attempts for ({schedule.user_id!r}, {schedule.concept_id!r})"
            )
        self._by_user_concept[key] = schedule

    @staticmethod
    def _is_append_only(
        existing: tuple[object, ...],
        incoming: tuple[object, ...],
    ) -> bool:
        """Return whether ``incoming`` extends ``existing`` without rewriting it."""
        return len(incoming) >= len(existing) and incoming[: len(existing)] == existing


class In_Memory_AttemptRepository(AttemptRepository):
    """An :class:`AttemptRepository` backed by in-process dictionaries (Req 3.1, 10.4, 5.2).

    Satisfies the :class:`~edu_it_domain.ports.attempts.AttemptRepository`
    protocol exactly — the write methods carry the same signatures the port
    declares, taking only the domain value(s), so the fake is substitutable
    without changing domain or application code (Req 5.2). :meth:`add_quiz_attempt`
    and :meth:`add_project_submission` reject a reused ``stable_id``; quiz
    attempts and answers are retained in insertion order so best-attempt
    selection, the cooldown computation, and the 24-hour anti-farming window read
    them oldest-first. A project submission is updated in place by
    :meth:`save_project_submission` once the reviewer's outcome is known (``None``
    while pending), keyed by its ``stable_id`` (Req 10.4, 10.5).

    Ownership note: the domain
    :class:`~edu_it_domain.learning.models.QuizAttempt`,
    :class:`~edu_it_domain.learning.models.Answer`, and
    :class:`~edu_it_domain.learning.models.ProjectSubmissionRef` carry no
    ``user_id`` (the owning learner is a database foreign key, not a domain
    attribute), so the write methods take the owning ``user_id`` explicitly and
    attribute every row written to it; the learner-scoped reads filter on it.
    """

    __test__ = False

    def __init__(self) -> None:
        """Create an empty attempt repository."""
        self._attempts: dict[StableId, QuizAttempt] = {}
        self._answers: list[Answer] = []
        self._answer_users: list[StableId] = []
        self._attempt_users: dict[StableId, StableId] = {}
        self._attempt_order: list[StableId] = []
        self._submissions: dict[StableId, ProjectSubmissionRef] = {}
        self._submission_users: dict[StableId, StableId] = {}

    def add_quiz_attempt(
        self,
        user_id: StableId,
        attempt: QuizAttempt,
        answers: tuple[Answer, ...],
    ) -> None:
        """Record ``attempt`` and its ``answers`` for ``user_id``; reject a reused ``stable_id``.

        The attempt and its answers are attributed to ``user_id`` so the
        learner-scoped reads can resolve them. Raises :class:`ValueError` when an
        attempt with the same ``stable_id`` already exists.
        """
        if attempt.stable_id in self._attempts:
            raise ValueError(f"quiz attempt with stable_id {attempt.stable_id!r} already exists")
        owner = user_id
        self._attempts[attempt.stable_id] = attempt
        self._attempt_users[attempt.stable_id] = owner
        self._attempt_order.append(attempt.stable_id)
        for answer in answers:
            self._answers.append(answer)
            self._answer_users.append(owner)

    def get_quiz_attempt(self, stable_id: StableId) -> QuizAttempt | None:
        """Return the quiz attempt with ``stable_id``, or ``None`` when absent."""
        return self._attempts.get(stable_id)

    def list_quiz_attempts(
        self,
        user_id: StableId,
        quiz_id: StableId,
    ) -> tuple[QuizAttempt, ...]:
        """Return the learner's attempts at the quiz, oldest first."""
        return tuple(
            self._attempts[sid]
            for sid in self._attempt_order
            if self._attempt_users.get(sid) == user_id and self._attempts[sid].quiz_id == quiz_id
        )

    def list_quiz_attempts_page(
        self,
        user_id: StableId,
        quiz_id: StableId,
        *,
        after: tuple[datetime, StableId] | None = None,
        limit: int = 50,
    ) -> tuple[QuizAttempt, ...]:
        """Return up to ``limit`` attempts strictly after the keyset ``after``."""
        rows = sorted(
            self.list_quiz_attempts(user_id, quiz_id),
            key=lambda a: (a.attempted_at, a.stable_id),
        )
        if after is not None:
            rows = [a for a in rows if (a.attempted_at, a.stable_id) > after]
        return tuple(rows[:limit])

    def list_answers_for_question(
        self,
        user_id: StableId,
        question_id: StableId,
    ) -> tuple[Answer, ...]:
        """Return the learner's answers to the question, oldest first."""
        return tuple(
            answer
            for owner, answer in zip(self._answer_users, self._answers, strict=True)
            if owner == user_id and answer.question_id == question_id
        )

    def add_project_submission(
        self,
        user_id: StableId,
        submission: ProjectSubmissionRef,
    ) -> None:
        """Record ``submission`` by ``user_id``; reject a reused ``stable_id``.

        Attributed to ``user_id``. Recorded with no ``review_outcome`` while the
        reviewer is pending; :meth:`save_project_submission` records the outcome once it completes
        (Req 10.4, 10.5). Raises :class:`ValueError` when a submission with the
        same ``stable_id`` already exists.
        """
        if submission.stable_id in self._submissions:
            raise ValueError(
                f"project submission with stable_id {submission.stable_id!r} already exists"
            )
        self._submissions[submission.stable_id] = submission
        self._submission_users[submission.stable_id] = user_id

    def get_project_submission(self, stable_id: StableId) -> ProjectSubmissionRef | None:
        """Return the project submission with ``stable_id``, or ``None`` when absent."""
        return self._submissions.get(stable_id)

    def save_project_submission(self, submission: ProjectSubmissionRef) -> None:
        """Update ``submission`` in place, recording the reviewer's outcome once set.

        Keyed by ``stable_id``: writes the
        :attr:`~edu_it_domain.learning.models.ProjectSubmissionRef.review_outcome`
        the reviewer produced, which stays ``None`` while pending (Req 10.4,
        10.5). Raises :class:`ValueError` when no submission with that
        ``stable_id`` was previously added.
        """
        if submission.stable_id not in self._submissions:
            raise ValueError(
                f"project submission with stable_id {submission.stable_id!r} does not exist"
            )
        self._submissions[submission.stable_id] = submission

    def list_project_submissions(
        self,
        user_id: StableId,
    ) -> tuple[ProjectSubmissionRef, ...]:
        """Return every project submission by ``user_id``."""
        return tuple(
            submission
            for sid, submission in self._submissions.items()
            if self._submission_users.get(sid) == user_id
        )
