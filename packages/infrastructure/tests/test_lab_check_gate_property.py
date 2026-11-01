"""Property test for the lab-check gate (hands-on-labs task 15.3).

Feature: hands-on-labs, Property 13: The lab-check gate follows completed labs

**Validates: Requirements 19.3**
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from edu_it_domain.labs.models import TaskCompletion
from edu_it_domain.learning.models import Mastery, MasteryEvent, MasteryState
from edu_it_domain.ports.lab_catalog import LabDefinition, LabTaskDefinition
from edu_it_infrastructure.adapters.lab_check import Repository_LabCheck
from edu_it_infrastructure.fakes import (
    Fake_LabCatalog,
    In_Memory_GradingRunRepository,
    In_Memory_MasteryRepository,
)
from hypothesis import given, settings
from hypothesis import strategies as st

_T0 = datetime(2024, 1, 1, tzinfo=UTC)
_CONCEPTS = ("c0", "c1", "c2", "c3")
_USERS = ("u0", "u1")


type _Scenario = tuple[list[LabDefinition], dict[str, set[str]], set[tuple[str, str]]]


@st.composite
def _scenario(draw: st.DrawFn) -> _Scenario:
    n_labs = draw(st.integers(min_value=0, max_value=4))
    labs: list[LabDefinition] = []
    for i in range(n_labs):
        required_flags = draw(st.lists(st.booleans(), min_size=0, max_size=4))
        tasks = tuple(
            LabTaskDefinition(f"l{i}-t{j}", required=r) for j, r in enumerate(required_flags)
        )
        concepts = tuple(draw(st.lists(st.sampled_from(_CONCEPTS), unique=True, max_size=3)))
        labs.append(
            LabDefinition(
                lab_id=f"l{i}",
                kind="linux",
                image="img",
                concept_ids=concepts,
                tasks=tasks,
                checks=(),
                bundle_path="b",
                archived=draw(st.booleans()),
            )
        )
    all_tasks = [t.task_id for lab in labs for t in lab.tasks]
    done = {
        u: set(draw(st.lists(st.sampled_from(all_tasks), unique=True))) if all_tasks else set()
        for u in _USERS
    }
    markers = {
        (u, c)
        for u in _USERS
        for c in _CONCEPTS
        if draw(st.integers(min_value=0, max_value=9)) == 0
    }
    return labs, done, markers


# Feature: hands-on-labs, Property 13: The lab-check gate follows completed labs
@settings(max_examples=100, deadline=None)
@given(_scenario())
def test_lab_check_gate_follows_completed_labs(scenario: _Scenario) -> None:
    labs, done, markers = scenario
    runs = In_Memory_GradingRunRepository()
    mastery = In_Memory_MasteryRepository()
    for user, task_ids in done.items():
        if task_ids:
            runs.add_task_completions(
                f"r-{user}",
                [
                    TaskCompletion(user, t, f"r-{user}", _T0, counted_for_mastery=True)
                    for t in sorted(task_ids)
                ],
            )
    for user, concept in markers:
        m = Mastery(user_id=user, concept_id=concept, score=0, state=MasteryState.UNSEEN)
        m = replace(m, events=(MasteryEvent(delta=0, source="lab_passed", occurred_at=_T0),))
        mastery.save(m)
    check = Repository_LabCheck(Fake_LabCatalog(labs), runs, mastery)

    for concept in _CONCEPTS:
        covering = [lab for lab in labs if not lab.archived and concept in lab.concept_ids]
        assert check.covers(concept) is bool(covering)
        for user in _USERS:
            by_labs = any(
                (req := {t.task_id for t in lab.tasks if t.required}) and req <= done[user]
                for lab in covering
            )
            expected = bool(covering) and (by_labs or (user, concept) in markers)
            assert check.passed(concept, user) is expected
