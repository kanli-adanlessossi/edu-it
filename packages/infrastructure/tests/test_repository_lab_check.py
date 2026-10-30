"""Unit tests for ``Repository_LabCheck`` (hands-on-labs task 15.2, Req 19.3)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from edu_it_domain.labs.models import TaskCompletion
from edu_it_domain.learning.models import Mastery, MasteryEvent, MasteryState
from edu_it_domain.ports.lab_catalog import LabDefinition, LabTaskDefinition
from edu_it_domain.ports.lab_check import LabCheck
from edu_it_infrastructure.adapters.lab_check import Repository_LabCheck
from edu_it_infrastructure.fakes import (
    Fake_LabCatalog,
    In_Memory_GradingRunRepository,
    In_Memory_MasteryRepository,
)

_T0 = datetime(2024, 1, 1, tzinfo=UTC)


def _lab(
    lab_id: str = "lab1", *, archived: bool = False, concepts: tuple[str, ...] = ("c1",)
) -> LabDefinition:
    return LabDefinition(
        lab_id=lab_id,
        kind="linux",
        image="img",
        concept_ids=concepts,
        tasks=(
            LabTaskDefinition(f"{lab_id}-t1", required=True),
            LabTaskDefinition(f"{lab_id}-t2", required=True),
            LabTaskDefinition(f"{lab_id}-opt", required=False),
        ),
        checks=(),
        bundle_path="b",
        archived=archived,
    )


def _build(*labs: LabDefinition):
    runs = In_Memory_GradingRunRepository()
    mastery = In_Memory_MasteryRepository()
    return Repository_LabCheck(Fake_LabCatalog(labs), runs, mastery), runs, mastery


def _complete(runs: In_Memory_GradingRunRepository, user: str, *task_ids: str) -> None:
    runs.add_task_completions(
        "r", [TaskCompletion(user, t, "r", _T0, counted_for_mastery=True) for t in task_ids]
    )


def test_satisfies_port_and_covers_only_non_archived_labs() -> None:
    check, _, _ = _build(_lab("a", concepts=("c1",)), _lab("b", archived=True, concepts=("c2",)))
    assert isinstance(check, LabCheck)
    assert check.covers("c1") is True
    assert check.covers("c2") is False
    assert check.covers("c3") is False


def test_passed_requires_all_required_tasks_for_the_user() -> None:
    check, runs, _ = _build(_lab())
    assert check.passed("c1", "u1") is False
    _complete(runs, "u1", "lab1-t1")
    assert check.passed("c1", "u1") is False
    _complete(runs, "u1", "lab1-t2")
    assert check.passed("c1", "u1") is True
    assert check.passed("c1", "u2") is False


def test_passed_via_lab_passed_marker() -> None:
    check, _, mastery = _build(_lab())
    m = Mastery(user_id="u1", concept_id="c1", score=0, state=MasteryState.UNSEEN)
    m = replace(m, events=(MasteryEvent(delta=0, source="lab_passed", occurred_at=_T0),))
    mastery.save(m)
    assert check.passed("c1", "u1") is True


def test_uncovered_concept_is_not_passed() -> None:
    check, runs, _ = _build(_lab(archived=True))
    _complete(runs, "u1", "lab1-t1", "lab1-t2")
    assert check.passed("c1", "u1") is False
