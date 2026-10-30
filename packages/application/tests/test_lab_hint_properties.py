"""Property test for progressive hint reveal (hands-on-labs task 6.6).

Feature: hands-on-labs, Property 20: Hints are revealed progressively

**Validates: Requirements 15.3**
"""

from __future__ import annotations

import pytest
from edu_it_application.labs import (
    HintNotAvailableError,
    HintOutOfOrderError,
    RevealLabHintUseCase,
)
from edu_it_domain import config
from edu_it_domain.labs.models import LabSession, SessionStatus
from edu_it_domain.ports.lab_catalog import LabDefinition, LabTaskDefinition
from edu_it_infrastructure.fakes import (
    Fake_LabCatalog,
    In_Memory_GradingRunRepository,
    In_Memory_LabSessionRepository,
    In_Memory_MasteryRepository,
    Test_Clock,
)
from hypothesis import given, settings
from hypothesis import strategies as st

_USER = "u1"
_SESSION = "s1"
_TASK = "t1"


def _build(hint_count: int):  # type: ignore[no-untyped-def]
    clock = Test_Clock()
    sessions = In_Memory_LabSessionRepository(In_Memory_GradingRunRepository())
    mastery = In_Memory_MasteryRepository()
    lab = LabDefinition(
        lab_id="lab1",
        kind="code",
        image="img",
        concept_ids=(),
        tasks=(
            LabTaskDefinition(_TASK, required=True, hint_count=hint_count),
            LabTaskDefinition("other", required=False, hint_count=hint_count),
        ),
        checks=(),
        learner_files=("main.py",),
    )
    now = clock.now()
    sessions.add_if_no_active(
        LabSession(
            stable_id=_SESSION,
            user_id=_USER,
            lab_id="lab1",
            status=SessionStatus.READY,
            created_at=now,
            ttl_at=now + config.LAB_SESSION_TTL,
            sandbox_ref=None,
        )
    )
    use_case = RevealLabHintUseCase(
        sessions=sessions, catalog=Fake_LabCatalog([lab]), mastery=mastery, clock=clock
    )
    return use_case, mastery


def _count(mastery, task_id: str) -> int:  # type: ignore[no-untyped-def]
    return sum(1 for r in mastery.list_hint_reveals(_USER) if r.lab_task_id == task_id)


@settings(max_examples=200, deadline=None)
@given(
    hint_count=st.integers(min_value=0, max_value=6),
    data=st.data(),
)
def test_property_20_hints_are_revealed_progressively(hint_count: int, data: st.DataObject) -> None:
    """Feature: hands-on-labs, Property 20: Hints are revealed progressively

    **Validates: Requirements 15.3**
    """
    requests = data.draw(st.lists(st.integers(min_value=-1, max_value=hint_count + 2), max_size=25))
    use_case, mastery = _build(hint_count)
    revealed = 0  # model: highest level revealed so far

    for level in requests:
        before = _count(mastery, _TASK)
        assert before == revealed
        if level < 1 or level > hint_count:
            with pytest.raises(HintNotAvailableError):
                use_case.execute(_USER, _SESSION, _TASK, level)
            assert _count(mastery, _TASK) == before
        elif level <= revealed:
            grant = use_case.execute(_USER, _SESSION, _TASK, level)
            assert grant.level == level
            assert not grant.newly_revealed
            assert _count(mastery, _TASK) == before
        elif level == revealed + 1:
            grant = use_case.execute(_USER, _SESSION, _TASK, level)
            assert grant.level == level
            assert grant.newly_revealed
            assert _count(mastery, _TASK) == before + 1
            revealed += 1
        else:
            with pytest.raises(HintOutOfOrderError):
                use_case.execute(_USER, _SESSION, _TASK, level)
            assert _count(mastery, _TASK) == before

    # Never more reveals than levels; other tasks are untouched.
    assert _count(mastery, _TASK) == revealed <= hint_count
    assert _count(mastery, "other") == 0
