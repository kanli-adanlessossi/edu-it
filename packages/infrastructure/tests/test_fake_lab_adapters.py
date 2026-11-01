"""Unit tests for the fake sandbox provider and in-memory lab repositories (task 2.1)."""

from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from edu_it_domain.labs.models import (
    GradingRun,
    LabSession,
    RunStatus,
    SessionStatus,
)
from edu_it_domain.labs.sandbox_spec import build_sandbox_spec
from edu_it_domain.ports.lab_catalog import LabDefinition
from edu_it_domain.ports.lab_sessions import ActiveSessionExists
from edu_it_domain.ports.sandbox import ProvisionError, ProvisionTimeout, SandboxGone
from edu_it_infrastructure.fakes import (
    Fake_LabCatalog,
    Fake_SandboxProvider,
    In_Memory_GradingRunRepository,
    In_Memory_LabSessionRepository,
    ProvisionMode,
    Test_Clock,
)
from edu_it_infrastructure.fakes import sandbox_provider as sp_module

_T0 = datetime(2024, 1, 1, tzinfo=UTC)


class _Lab:
    image = "linux-base"


def _spec(sid: str = "s1"):
    return build_sandbox_spec(_Lab(), sid)


def _session(sid: str, user: str = "u1", status=SessionStatus.PROVISIONING) -> LabSession:
    return LabSession(sid, user, "lab", status, _T0, _T0 + timedelta(hours=1))


def test_provider_lifecycle_and_clock_advance() -> None:
    clock = Test_Clock(start=_T0)
    p = Fake_SandboxProvider(clock, call_duration=timedelta(seconds=1))
    h = p.provision(_spec())
    assert p.exec(h, ["true"], as_user="learner", env={}, cwd="/").exit_code == 0
    assert clock.now() == _T0 + timedelta(seconds=2)
    assert [x.sandbox_id for x in p.list_sandboxes()] == [h.sandbox_id]
    p.destroy(h)
    p.destroy(h)  # idempotent
    with pytest.raises(SandboxGone):
        p.exec(h, ["true"], as_user="learner", env={}, cwd="/")
    assert not p.health(h).healthy


def test_freeze_and_files(tmp_path: Path) -> None:
    p = Fake_SandboxProvider(Test_Clock(start=_T0))
    h = p.provision(_spec())
    p.add_process(h, "learner", "sleep", "R")
    p.exec(h, ["/bin/kill", "-s", "STOP", "-1"], as_user="learner", env={}, cwd="/")
    assert {x.state for x in p.sandbox(h).processes} == {"T"}
    src = tmp_path / "f.txt"
    src.write_bytes(b"hi")
    p.copy_in(h, src, "/grader/f.txt")
    out = tmp_path / "out.txt"
    p.copy_out(h, "/grader/f.txt", out)
    assert out.read_bytes() == b"hi"


def test_failure_hang_and_partial_modes() -> None:
    clock = Test_Clock(start=_T0)
    p = Fake_SandboxProvider(clock, hang_duration=timedelta(seconds=30))
    p.script_provision(ProvisionMode.FAIL, ProvisionMode.HANG, ProvisionMode.PARTIAL)
    with pytest.raises(ProvisionError):
        p.provision(_spec())
    with pytest.raises(ProvisionTimeout):
        p.provision(_spec())
    assert clock.now() == _T0 + timedelta(seconds=30)
    with pytest.raises(ProvisionError) as info:
        p.provision(_spec())
    assert info.value.partial_handle is not None
    p.destroy(info.value.partial_handle)
    assert p.list_sandboxes() == ()


def test_provider_imports_no_process_or_network_module() -> None:
    tree = ast.parse(Path(sp_module.__file__).read_text(encoding="utf-8"))
    names = {
        a.name.split(".")[0]
        for n in ast.walk(tree)
        if isinstance(n, ast.Import | ast.ImportFrom)
        for a in (n.names if isinstance(n, ast.Import) else [ast.alias(n.module or "")])
    }
    assert not names & {"subprocess", "socket", "docker"}


def test_one_active_session_per_user_and_cas() -> None:
    sessions = In_Memory_LabSessionRepository()
    sessions.add_if_no_active(_session("a"))
    with pytest.raises(ActiveSessionExists):
        sessions.add_if_no_active(_session("b"))
    sessions.add_if_no_active(_session("c", user="u2"))
    assert sessions.claim_provision_attempt("a", expected_attempts=0, now=_T0)
    assert not sessions.claim_provision_attempt("a", expected_attempts=0, now=_T0)
    P, R = SessionStatus.PROVISIONING, SessionStatus.READY
    assert sessions.transition("a", expected={P}, to=R, sandbox_ref="x")
    assert not sessions.transition("a", expected={P}, to=SessionStatus.FAILED)
    got = sessions.get("a")
    assert got is not None
    assert got.sandbox_ref == "x"


def test_grading_acceptance_interval_is_strict() -> None:
    sessions = In_Memory_LabSessionRepository()
    sessions.add_if_no_active(_session("a", status=SessionStatus.READY))
    gap = timedelta(seconds=10)
    assert sessions.try_accept_grading("a", now=_T0, min_interval=gap)
    assert sessions.transition("a", expected={SessionStatus.GRADING}, to=SessionStatus.READY)
    assert not sessions.try_accept_grading("a", now=_T0 + gap, min_interval=gap)
    assert sessions.try_accept_grading(
        "a", now=_T0 + gap + timedelta(milliseconds=1), min_interval=gap
    )


def test_run_repository_cas_and_idempotent_results() -> None:
    runs = In_Memory_GradingRunRepository()
    sessions = In_Memory_LabSessionRepository(runs)
    sessions.add_if_no_active(_session("a", status=SessionStatus.READY))
    runs.add(GradingRun("r1", "a", RunStatus.RUNNING, _T0, _T0 + timedelta(seconds=15)))
    assert len(runs.list_running()) == 1
    assert runs.finish("r1", status=RunStatus.PASSED, graded_at=_T0)
    assert not runs.finish("r1", status=RunStatus.FAILED, graded_at=_T0)
    sessions.transition("a", expected={SessionStatus.READY}, to=SessionStatus.ENDED, ended_at=_T0)
    assert sessions.delete_session_data(["a"]) == 1
    assert runs.get("r1") is None


def test_fake_catalog_covering() -> None:
    lab = LabDefinition("l1", "terminal", "img", ("c1",), (), ())
    old = LabDefinition("l2", "terminal", "img", ("c1",), (), (), archived=True)
    cat = Fake_LabCatalog([lab, old])
    assert [x.lab_id for x in cat.labs_covering("c1")] == ["l1"]
    assert cat.get("l2") is old
