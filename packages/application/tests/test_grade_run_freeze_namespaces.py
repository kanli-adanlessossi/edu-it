"""GradeRun's freeze verification under separate process namespaces (Kubernetes layout B).

In layout B each container has its own process namespace: a listing run as the ``grader`` sees only
the grader container's processes, so "no running learner process" would be vacuously true. These
tests model that namespace split, and fail if the verification cannot see the learner processes:
with the verification in the grader container, a learner process that never stops would still let
the Checks run.

Claims: 5.4, 16.10
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from edu_it_application.labs import GradeRun, RecordCheckResult
from edu_it_application.labs.grade_run import _parse_ps
from edu_it_domain import config
from edu_it_domain.labs.models import GradingRun, LabSession, RunStatus, SessionStatus
from edu_it_domain.labs.sandbox_spec import build_sandbox_spec
from edu_it_domain.ports.lab_catalog import LabCheckDefinition, LabDefinition, LabTaskDefinition
from edu_it_domain.ports.sandbox import ExecResult
from edu_it_infrastructure.fakes import (
    Fake_LabCatalog,
    Fake_SandboxProvider,
    In_Memory_GradingRunRepository,
    In_Memory_LabSessionRepository,
    In_Memory_MasteryRepository,
    Test_Clock,
)

_STOP = ["/bin/kill", "-s", "STOP", "-1"]
_CONT = ["/bin/kill", "-s", "CONT", "-1"]
_CONTAINER = {"learner": "learner", "grader": "grader", "root": "owner"}
_UID = {"learner": "10000", "grader": "10001", "owner": "10002"}
_SELF_PID = 4242


@dataclass
class _Proc:
    pid: int
    container: str
    state: str = "S"
    comm: str = "bash"


@dataclass
class _Pods:
    """Per-container process namespaces: a command only sees its own container's processes."""

    procs: list[_Proc] = field(default_factory=list)
    stop_works: bool = True
    listings: list[tuple[str, list[str]]] = field(default_factory=list)

    def handle(self, _sb: object, command: Sequence[str], as_user: str) -> ExecResult | None:
        container = _CONTAINER[as_user]
        command = list(command)
        if command == _STOP:
            targets = [p for p in self.procs if p.container == container and p.pid != 1]
            if not targets:  # kill(-1) with nothing to signal: ESRCH, as busybox kill reports it
                return ExecResult(1, b"", b"kill: can't kill pid -1: No such process\n", False)
            if self.stop_works:
                for p in targets:
                    if p.state != "Z":
                        p.state = "T"
            return ExecResult(0, b"", b"", False)
        if command == _CONT:
            for p in self.procs:
                if p.container == container and p.state == "T":
                    p.state = "S"
            return ExecResult(0, b"", b"", False)
        if command[:1] == ["/bin/ps"] or (
            command[:2] == ["/bin/bash", "-c"] and "/bin/ps" in command[2]
        ):
            self.listings.append((container, command))
            uid = _UID[container]
            lines = []
            if command[:1] == ["/bin/bash"]:
                lines.append(f"SELF {_SELF_PID}")
            rows = [(p.pid, p.state, p.comm) for p in self.procs if p.container == container]
            if command[:1] == ["/bin/bash"]:
                rows.append((_SELF_PID, "R", "ps"))
            lines += [f"{uid} {state} {pid} {comm}" for pid, state, comm in rows]
            return ExecResult(0, ("\n".join(lines) + "\n").encode(), b"", False)
        return None


class _Env:
    def __init__(self, pods: _Pods) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "lab1" / "sandbox" / "checks").mkdir(parents=True)
        (self.tmp / "lab1" / "sandbox" / "checks" / "c1.sh").write_text("exit 0\n")
        (self.tmp / "lab1" / "expected").mkdir()
        lab = LabDefinition(
            lab_id="lab1",
            kind="terminal",
            image="img",
            concept_ids=("cn1",),
            tasks=(LabTaskDefinition("t1", required=True),),
            checks=(LabCheckDefinition("c1", "t1", 5.0, {"pass": "fb.ok"}),),
            bundle_path="lab1",
        )
        self.pods = pods
        self.clock = Test_Clock()
        self.runs = In_Memory_GradingRunRepository()
        self.sessions = In_Memory_LabSessionRepository(self.runs)
        self.sandbox = Fake_SandboxProvider(self.clock)
        self.handle = self.sandbox.provision(build_sandbox_spec(lab, "s1"))
        self.sandbox.exec_handler = pods.handle
        now = self.clock.now()
        self.sessions.add_if_no_active(
            LabSession("s1", "u1", "lab1", SessionStatus.GRADING, now, now + config.LAB_SESSION_TTL)
        )
        self.sessions.transition(
            "s1",
            expected={SessionStatus.GRADING},
            to=SessionStatus.GRADING,
            sandbox_ref=self.handle.sandbox_id,
        )
        self.runs.add(
            GradingRun("r1", "s1", RunStatus.RUNNING, now, now + config.LAB_GRADING_RUN_DEADLINE)
        )
        self.job = GradeRun(
            sessions=self.sessions,
            runs=self.runs,
            catalog=Fake_LabCatalog([lab]),
            sandbox=self.sandbox,
            record=RecordCheckResult(
                runs=self.runs, mastery=In_Memory_MasteryRepository(), clock=self.clock
            ),
            clock=self.clock,
            bundle_root=self.tmp,
            provider_name="kubernetes",
        )


@pytest.fixture
def make_env() -> Iterator[type[_Env]]:
    created: list[_Env] = []

    def build(pods: _Pods) -> _Env:
        env = _Env(pods)
        created.append(env)
        return env

    yield build  # type: ignore[misc]
    for env in created:
        shutil.rmtree(env.tmp, ignore_errors=True)


def _learner_world() -> _Pods:
    return _Pods(
        procs=[
            _Proc(1, "learner", "S", "sh"),
            _Proc(7, "learner", "R", "bash"),
            _Proc(8, "learner", "S", "sleep"),
            _Proc(1, "grader", "S", "tail"),
            _Proc(1, "owner", "S", "tail"),
        ]
    )


def test_freeze_is_verified_in_the_learner_container(make_env) -> None:  # type: ignore[no-untyped-def]
    env = make_env(_learner_world())
    assert env.job.execute("r1") is RunStatus.PASSED
    assert env.pods.listings, "the freeze was never verified"
    assert {container for container, _ in env.pods.listings} == {"learner"}


def test_unstoppable_learner_process_blocks_the_run(make_env) -> None:  # type: ignore[no-untyped-def]
    """Fails if the verification cannot see learner processes: a listing in the grader container
    would find no learner process, call the freeze verified, and run the Checks."""
    pods = _learner_world()
    pods.stop_works = False
    env = make_env(pods)
    assert env.job.execute("r1") is RunStatus.COULD_NOT_COMPLETE
    assert "copy_in" not in env.sandbox.calls
    assert "grader" not in {container for container, _ in pods.listings}


def test_listing_that_does_not_show_itself_fails_closed(make_env) -> None:  # type: ignore[no-untyped-def]
    """A listing from the wrong namespace (own PID absent) is not accepted as proof."""
    pods = _learner_world()
    original = pods.handle

    def blind(sb: object, command: list[str], as_user: str) -> ExecResult | None:
        result = original(sb, command, as_user)
        if (
            result is not None
            and list(command)[:2] == ["/bin/bash", "-c"]
            and "/bin/ps" in list(command)[2]
        ):
            kept = [ln for ln in result.stdout.decode().splitlines() if f" {_SELF_PID} " not in ln]
            return ExecResult(0, ("\n".join(kept) + "\n").encode(), b"", False)
        return result

    env = make_env(pods)
    env.sandbox.exec_handler = blind
    assert env.job.execute("r1") is RunStatus.COULD_NOT_COMPLETE


def test_fresh_pod_with_only_the_init_can_be_graded(make_env) -> None:  # type: ignore[no-untyped-def]
    """``kill -s STOP -1`` exits 1 (ESRCH) when only the init runs; the verified listing (no
    learner process besides the init and the listing itself) is the proof of the freeze."""
    pods = _Pods(procs=[_Proc(1, "learner", "S", "bash"), _Proc(1, "grader"), _Proc(1, "owner")])
    env = make_env(pods)
    assert env.job.execute("r1") is RunStatus.PASSED


def test_parse_ps_in_learner_container_ignores_init_and_itself_and_maps_numeric_users() -> None:
    out = b"SELF 50\n10000 S 1 sh\n10000 T 7 bash\n10000 T 8 sleep\n10000 R 50 ps\n"
    rows = _parse_ps(out, True)
    assert [(r.user, r.state) for r in rows] == [("learner", "T"), ("learner", "T")]


def test_parse_ps_in_learner_container_counts_a_running_process() -> None:
    out = b"SELF 50\n10000 R 9 sleep\n10000 R 50 ps\n"
    rows = _parse_ps(out, True)
    assert [(r.user, r.state) for r in rows] == [("learner", "R")]


def test_parse_ps_in_learner_container_without_itself_is_a_running_row() -> None:
    assert [(r.user, r.state) for r in _parse_ps(b"10000 T 7 bash\n", True)][-1] == ("learner", "")
