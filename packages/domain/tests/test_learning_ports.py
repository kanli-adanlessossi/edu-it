"""Unit tests for the learning-domain ports (task 1.3, Req 1.1, 1.2, 1.4, 10.4).

These assert the new learning ports — the ``ProjectReviewer`` and ``LabCheck``
extension points and the catalog/enrollment/progress/mastery/review/attempts
repository ports — are declared, exported from the domain package, are
``@runtime_checkable`` Protocols, and are structurally satisfiable by minimal
in-test doubles (the substitutability the fakes of task 1.4 will rely on). They
reuse the domain learning types from
:mod:`edu_it_domain.learning.models` unchanged.

The implementations below are deliberately minimal test doubles, not the
production fakes; they exist only to prove each Protocol is satisfiable.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import edu_it_domain.ports as ports_pkg
from edu_it_domain.learning.models import (
    Answer,
    Mastery,
    MasteryState,
    ProjectSubmissionRef,
    QuestionOption,
    QuizAttempt,
    ReviewSchedule,
    Rubric,
    RubricCriterion,
    RubricScore,
)
from edu_it_domain.ports import (
    AttemptRepository,
    CatalogRepository,
    ConceptRecord,
    EnrollmentRecord,
    EnrollmentRepository,
    HintRevealRecord,
    LabCheck,
    LessonCompletionRecord,
    MasteryRepository,
    ModuleProgressRecord,
    ModuleRecord,
    ProgressRepository,
    ProjectReviewer,
    QuestionRecord,
    QuizRecord,
    ReviewRepository,
)

LEARNING_PORTS = (
    "ProjectReviewer",
    "LabCheck",
    "CatalogRepository",
    "EnrollmentRepository",
    "ProgressRepository",
    "MasteryRepository",
    "ReviewRepository",
    "AttemptRepository",
)

_NOW = datetime(2024, 1, 1, tzinfo=UTC)


def test_learning_ports_are_exported_from_the_ports_package() -> None:
    # Repository and extension-point ports are exported from the ports package
    # (as the identity repository ports are), not re-exported at the domain
    # package root which carries only the five external-system ports.
    for name in LEARNING_PORTS:
        assert hasattr(ports_pkg, name), f"ports is missing port {name!r}"
        assert name in ports_pkg.__all__


def test_learning_ports_are_runtime_checkable_protocols() -> None:
    for port in (
        ProjectReviewer,
        LabCheck,
        CatalogRepository,
        EnrollmentRepository,
        ProgressRepository,
        MasteryRepository,
        ReviewRepository,
        AttemptRepository,
    ):
        assert getattr(port, "_is_runtime_protocol", False) is True


# --- Minimal in-test doubles proving each Protocol is satisfiable ------------


class _StubProjectReviewer:
    def __init__(self, result: tuple[RubricScore, ...] | None) -> None:
        self._result = result

    def review(
        self,
        submission: ProjectSubmissionRef,
        rubric: Rubric,
    ) -> tuple[RubricScore, ...] | None:
        return self._result


class _StubLabCheck:
    def covers(self, concept_id: str) -> bool:
        return True

    def passed(self, concept_id: str, user_id: str) -> bool:
        return True


class _MemoryCatalog:
    def get_module(self, stable_id: str) -> ModuleRecord | None:
        return None

    def list_modules_for_track(self, track_id: str) -> tuple[ModuleRecord, ...]:
        return ()

    def get_concept(self, stable_id: str) -> ConceptRecord | None:
        return None

    def get_quiz(self, stable_id: str) -> QuizRecord | None:
        return None

    def get_quiz_at_version(self, stable_id: str, content_version: str) -> QuizRecord | None:
        return None

    def list_questions_for_concept(self, concept_id: str) -> tuple[QuestionRecord, ...]:
        return ()

    def get_rubric_for_project(self, project_id: str) -> Rubric | None:
        return None


class _MemoryEnrollment:
    def add(self, enrollment: EnrollmentRecord) -> None: ...

    def get(self, stable_id: str) -> EnrollmentRecord | None:
        return None

    def get_for_user_and_track(self, user_id: str, track_id: str) -> EnrollmentRecord | None:
        return None

    def list_for_user(self, user_id: str) -> tuple[EnrollmentRecord, ...]:
        return ()


class _MemoryProgress:
    def get_module_progress(
        self, enrollment_id: str, module_id: str
    ) -> ModuleProgressRecord | None:
        return None

    def list_module_progress(self, enrollment_id: str) -> tuple[ModuleProgressRecord, ...]:
        return ()

    def upsert_module_progress(self, progress: ModuleProgressRecord) -> None: ...

    def add_lesson_completion(self, completion: LessonCompletionRecord) -> None: ...

    def list_lesson_completions(self, module_id: str) -> tuple[LessonCompletionRecord, ...]:
        return ()


class _MemoryMastery:
    def get(self, user_id: str, concept_id: str) -> Mastery | None:
        return None

    def list_for_user(self, user_id: str) -> tuple[Mastery, ...]:
        return ()

    def save(self, mastery: Mastery) -> None: ...

    def add_hint_reveal(self, reveal: HintRevealRecord) -> None: ...

    def list_hint_reveals(self, user_id: str) -> tuple[HintRevealRecord, ...]:
        return ()


class _MemoryReview:
    def get(self, user_id: str, concept_id: str) -> ReviewSchedule | None:
        return None

    def list_for_user(self, user_id: str) -> tuple[ReviewSchedule, ...]:
        return ()

    def save(self, schedule: ReviewSchedule) -> None: ...


class _MemoryAttempts:
    def add_quiz_attempt(
        self, user_id: str, attempt: QuizAttempt, answers: tuple[Answer, ...]
    ) -> None: ...

    def get_quiz_attempt(self, stable_id: str) -> QuizAttempt | None:
        return None

    def list_quiz_attempts(self, user_id: str, quiz_id: str) -> tuple[QuizAttempt, ...]:
        return ()

    def list_answers_for_question(self, user_id: str, question_id: str) -> tuple[Answer, ...]:
        return ()

    def add_project_submission(self, user_id: str, submission: ProjectSubmissionRef) -> None: ...

    def get_project_submission(self, stable_id: str) -> ProjectSubmissionRef | None:
        return None

    def save_project_submission(self, submission: ProjectSubmissionRef) -> None: ...

    def list_project_submissions(self, user_id: str) -> tuple[ProjectSubmissionRef, ...]:
        return ()


def test_minimal_double_satisfies_each_learning_port_structurally() -> None:
    assert isinstance(_StubProjectReviewer(None), ProjectReviewer)
    assert isinstance(_StubLabCheck(), LabCheck)
    assert isinstance(_MemoryCatalog(), CatalogRepository)
    assert isinstance(_MemoryEnrollment(), EnrollmentRepository)
    assert isinstance(_MemoryProgress(), ProgressRepository)
    assert isinstance(_MemoryMastery(), MasteryRepository)
    assert isinstance(_MemoryReview(), ReviewRepository)
    assert isinstance(_MemoryAttempts(), AttemptRepository)


def test_project_reviewer_pending_returns_none() -> None:
    # A reviewer that has not completed reports pending via None (Req 10.4, 10.5).
    reviewer: ProjectReviewer = _StubProjectReviewer(None)
    submission = ProjectSubmissionRef(stable_id="ps_1", project_id="pr_1")
    rubric = Rubric(
        stable_id="rb_1",
        project_id="pr_1",
        criteria=(RubricCriterion(stable_id="rc_1", max_points=10),),
    )
    assert reviewer.review(submission, rubric) is None


def test_project_reviewer_completed_returns_scores() -> None:
    scores = (RubricScore(criterion_id="rc_1", points=8),)
    reviewer: ProjectReviewer = _StubProjectReviewer(scores)
    submission = ProjectSubmissionRef(stable_id="ps_1", project_id="pr_1")
    rubric = Rubric(stable_id="rb_1", project_id="pr_1")
    assert reviewer.review(submission, rubric) == scores


def test_lab_check_covers_and_passed() -> None:
    check: LabCheck = _StubLabCheck()
    assert check.covers("cn_1") is True
    assert check.passed("cn_1", "usr_1") is True


def test_catalog_record_shapes_reuse_domain_option_identity() -> None:
    # A question record carries QuestionOption identities from models.py.
    question = QuestionRecord(
        stable_id="qn_1",
        options=(
            QuestionOption(option_id=1, is_correct=True),
            QuestionOption(option_id=2, is_correct=False),
        ),
        concept_ids=("cn_1",),
    )
    quiz = QuizRecord(stable_id="qz_1", lesson_id="ls_1", questions=(question,))
    concept = ConceptRecord(stable_id="cn_1", requires=("cn_0",))
    module = ModuleRecord(
        stable_id="md_1",
        track_id="tr_1",
        ordinal=0,
        prerequisite_concept_ids=("cn_1",),
    )
    assert quiz.questions[0].options[0].option_id == 1
    assert concept.requires == ("cn_0",)
    assert module.prerequisite_concept_ids == ("cn_1",)


def test_enrollment_and_progress_records_carry_stable_ids() -> None:
    enrollment = EnrollmentRecord(
        stable_id="en_1",
        user_id="usr_1",
        track_id="tr_1",
        enrolled_at=_NOW,
    )
    progress = ModuleProgressRecord(
        enrollment_id="en_1",
        module_id="md_1",
        state="locked",
        tested_out=True,
    )
    completion = LessonCompletionRecord(
        module_id="md_1",
        lesson_id="ls_1",
        completed_at=_NOW,
    )
    assert enrollment.enrolled_at is _NOW
    assert progress.tested_out is True
    assert completion.lesson_id == "ls_1"


def test_mastery_and_review_records_reuse_domain_types() -> None:
    mastery = Mastery(
        user_id="usr_1",
        concept_id="cn_1",
        score=42,
        state=MasteryState.PRACTICED,
    )
    schedule = ReviewSchedule(
        user_id="usr_1",
        concept_id="cn_1",
        due_at=_NOW,
        interval=timedelta(days=1),
    )
    reveal = HintRevealRecord(user_id="usr_1", question_id="qn_1", revealed_at=_NOW)
    assert mastery.state is MasteryState.PRACTICED
    assert schedule.interval == timedelta(days=1)
    assert reveal.revealed_at is _NOW
