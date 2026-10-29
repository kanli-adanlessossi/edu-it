"""Unit tests for the configurable learning extension-point fakes (task 1.4, Req 1.4, 10.4, 10.5).

These prove :class:`Fake_ProjectReviewer` and :class:`Fake_LabCheck` satisfy
their ``@runtime_checkable`` domain ports (the substitutability the fakes exist
for, Req 5.2) and that their configurable behaviors match the extension-point
contracts: a reviewer returns ``None`` while pending and per-criterion scores
once completed (Req 10.4, 10.5), and a lab check toggles coverage and pass per
``(concept, learner)`` to drive the Mastered gate (Req 1.4, 5.6).
"""

from __future__ import annotations

from edu_it_domain.learning.models import (
    ProjectSubmissionRef,
    Rubric,
    RubricCriterion,
)
from edu_it_domain.ports.lab_check import LabCheck
from edu_it_domain.ports.project_reviewer import ProjectReviewer
from edu_it_infrastructure.fakes import Fake_LabCheck, Fake_ProjectReviewer

_RUBRIC = Rubric(
    stable_id="rub-1",
    project_id="proj-1",
    criteria=(
        RubricCriterion(stable_id="crit-correctness", max_points=10),
        RubricCriterion(stable_id="crit-least-privilege", max_points=5),
    ),
)
_SUBMISSION = ProjectSubmissionRef(stable_id="sub-1", project_id="proj-1")


# --- Fake_ProjectReviewer (Req 10.4, 10.5) -----------------------------------


def test_project_reviewer_satisfies_its_port() -> None:
    reviewer: ProjectReviewer = Fake_ProjectReviewer()
    assert isinstance(reviewer, ProjectReviewer)


def test_project_reviewer_returns_none_while_pending() -> None:
    # Req 10.4, 10.5: a pending reviewer exposes no pass/fail result.
    reviewer = Fake_ProjectReviewer(pending=True)
    assert reviewer.is_pending
    assert reviewer.review(_SUBMISSION, _RUBRIC) is None


def test_project_reviewer_returns_one_score_per_criterion_when_completed() -> None:
    # A completed reviewer returns one RubricScore per rubric criterion, in order,
    # carrying the configured points (Req 10.1, 10.2).
    reviewer = Fake_ProjectReviewer(
        scores={"crit-correctness": 9, "crit-least-privilege": 4},
    )
    scores = reviewer.review(_SUBMISSION, _RUBRIC)
    assert scores is not None
    assert tuple((s.criterion_id, s.points) for s in scores) == (
        ("crit-correctness", 9),
        ("crit-least-privilege", 4),
    )


def test_project_reviewer_falls_back_to_default_points_for_unscored_criteria() -> None:
    reviewer = Fake_ProjectReviewer(scores={"crit-correctness": 7}, default_points=2)
    scores = reviewer.review(_SUBMISSION, _RUBRIC)
    assert scores is not None
    assert {s.criterion_id: s.points for s in scores} == {
        "crit-correctness": 7,
        "crit-least-privilege": 2,
    }


def test_project_reviewer_complete_toggles_pending_to_scores() -> None:
    # A test can drive one submission from pending to reviewed with the same
    # reviewer instance (Req 10.5).
    reviewer = Fake_ProjectReviewer(scores={"crit-correctness": 10}, pending=True)
    assert reviewer.review(_SUBMISSION, _RUBRIC) is None

    reviewer.complete()
    assert not reviewer.is_pending
    scores = reviewer.review(_SUBMISSION, _RUBRIC)
    assert scores is not None and len(scores) == len(_RUBRIC.criteria)

    # set_pending can put it back into the pending state.
    reviewer.set_pending(True)
    assert reviewer.review(_SUBMISSION, _RUBRIC) is None


# --- Fake_LabCheck (Req 1.4, 5.6) --------------------------------------------


def test_lab_check_satisfies_its_port() -> None:
    lab: LabCheck = Fake_LabCheck()
    assert isinstance(lab, LabCheck)


def test_lab_check_uncovered_concept_is_vacuously_satisfied() -> None:
    # Req 1.4: no lab covers the concept, so covers() is False (the gate is then
    # vacuously satisfied by the mastery service) and passed() is False.
    lab = Fake_LabCheck()
    assert lab.covers("concept-x") is False
    assert lab.passed("concept-x", "user-1") is False


def test_lab_check_coverage_and_pass_toggles_per_concept_and_user() -> None:
    # Req 5.6: a covered concept consults passed() per learner.
    lab = Fake_LabCheck(
        covered={"concept-a"},
        passed={"concept-a": {"user-1"}},
    )
    assert lab.covers("concept-a") is True
    assert lab.passed("concept-a", "user-1") is True
    # A different learner has not passed the same covered concept.
    assert lab.passed("concept-a", "user-2") is False


def test_lab_check_setters_toggle_coverage_and_pass() -> None:
    lab = Fake_LabCheck()
    lab.set_covered("concept-b", covered=True)
    assert lab.covers("concept-b") is True

    lab.set_passed("concept-b", "user-9", passed=True)
    assert lab.passed("concept-b", "user-9") is True

    lab.set_passed("concept-b", "user-9", passed=False)
    assert lab.passed("concept-b", "user-9") is False

    lab.set_covered("concept-b", covered=False)
    assert lab.covers("concept-b") is False
