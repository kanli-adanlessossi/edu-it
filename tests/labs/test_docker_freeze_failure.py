"""Docker freeze-failure test (hands-on-labs task 8.10).

Requirements 10.7, 10.8, 10.11, 28.8 and 28.9, evidenced against a real kernel with the real
``GradeRun`` and ``DockerSandboxProvider``. Call-logic counterpart: Property 35 (fake provider).

SIGSTOP itself cannot be ignored, so the learner cannot refuse the freeze. What a learner can do
(same uid, so it is allowed to) is undo it: several learner processes loop on
``kill -CONT -1``, which resumes the stopped processes faster than the freeze can be verified.
``ps`` then keeps showing learner processes in state ``R`` on every attempt, the freeze is never
verified, and the run must fail closed: no ``copy_in``, no Check, every Check
``could_not_complete``, no mastery, no raw output, and the learner processes running afterwards.

Tests carry the ``docker`` marker and are skipped without a daemon or the local
``edu-it/lab-linux-base:dev`` image. The Check script is written to a temporary bundle with LF
endings; it would print the planted marker if it ever ran.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import pytest
from edu_it_application.labs import GradeRun, RecordCheckResult
from edu_it_domain import config
from edu_it_domain.labs.grading import to_learner_view
from edu_it_domain.labs.models import (
    CheckOutcome,
    GradingRun,
    LabSession,
    RunStatus,
    SessionStatus,
)
from edu_it_domain.labs.sandbox_spec import build_sandbox_spec
from edu_it_domain.ports.lab_catalog import (
    LabCheckDefinition,
    LabDefinition,
    LabTaskDefinition,
)
from edu_it_domain.ports.sandbox import SandboxHandle
from edu_it_infrastructure.fakes import (
    Fake_LabCatalog,
    In_Memory_GradingRunRepository,
    In_Memory_LabSessionRepository,
    In_Memory_MasteryRepository,
    Test_Clock,
)
from edu_it_infrastructure.sandbox.docker_provider import DockerSandboxProvider

IMAGE = "edu-it/lab-linux-base:dev"
BUNDLE_NAME = "freeze_failure_bundle"
CHECK_IDS = ("c_one", "c_two")
#: Planted raw output. It is a Check's output and the ``comm`` of a learner process (visible in
#: the freeze-verification ``ps`` listing); it must never reach a learner-facing field.
MARKER = "RAWMK8f3a9c"
FREEZE = ["/bin/kill", "-s", "STOP", "-1"]
THAW = ["/bin/kill", "-s", "CONT", "-1"]
MESSAGES = {"lab.feedback.could-not-complete": "This check could not complete. Try again."}
WAKERS = 8


def _daemon_ready() -> bool:
    try:
        import docker

        client = docker.from_env(timeout=3)
        client.ping()
        client.images.get(IMAGE)
    except Exception:
        return False
    return True


pytestmark = [
    pytest.mark.docker,
    pytest.mark.skipif(not _daemon_ready(), reason="no Docker daemon or lab image"),
]


@dataclass
class _Call:
    kind: str  # "exec" | "copy_in"
    argv: list[str] = field(default_factory=list)
    user: str | None = None
    dest: str | None = None
    stdout: bytes = b""


class _Recorder:
    """Delegates to the real provider and records every exec and copy_in."""

    def __init__(self, inner: DockerSandboxProvider) -> None:
        self._inner = inner
        self.calls: list[_Call] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def exec(self, handle, command, *, as_user, env, cwd, timeout_s=None):  # type: ignore[no-untyped-def]
        call = _Call("exec", list(command), as_user)
        self.calls.append(call)
        result = self._inner.exec(
            handle, command, as_user=as_user, env=env, cwd=cwd, timeout_s=timeout_s
        )
        call.stdout = result.stdout
        return result

    def copy_in(self, handle, source_ro, dest, *, as_user=None):  # type: ignore[no-untyped-def]
        self.calls.append(_Call("copy_in", user=as_user, dest=dest))
        return self._inner.copy_in(handle, source_ro, dest, as_user=as_user)


def _lab() -> LabDefinition:
    return LabDefinition(
        lab_id="lab-ff",
        kind="terminal",
        image=IMAGE,
        concept_ids=("cn1",),
        tasks=(LabTaskDefinition("t1", required=True), LabTaskDefinition("t2", required=True)),
        checks=(
            LabCheckDefinition("c_one", "t1", 15.0),
            LabCheckDefinition("c_two", "t2", 15.0),
        ),
        bundle_path=BUNDLE_NAME,
    )


def _sh(provider: DockerSandboxProvider, handle: SandboxHandle, script: str, *, user="learner"):  # type: ignore[no-untyped-def]
    return provider.exec(
        handle,
        ["/bin/bash", "-c", script],
        as_user=user,
        env={"PATH": "/usr/bin:/bin"},
        cwd="/workspace" if user == "learner" else "/",
        timeout_s=30,
    )


@dataclass
class _Outcome:
    status: RunStatus | None
    run: GradingRun
    calls: list[_Call]
    session_status: SessionStatus
    mastery_score: float
    completions: bool
    checks_dir_present: bool
    learner_states_after: list[str]
    counter_resumed: bool


@pytest.fixture(scope="module")
def provider() -> DockerSandboxProvider:
    return DockerSandboxProvider()


@pytest.fixture(scope="module")
def outcome(
    provider: DockerSandboxProvider, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[_Outcome]:
    bundle_root = tmp_path_factory.mktemp("freeze_failure")
    checks = bundle_root / BUNDLE_NAME / "sandbox" / "checks"
    checks.mkdir(parents=True)
    for check_id in CHECK_IDS:  # would leak the marker and pass if it ever ran
        (checks / f"{check_id}.sh").write_text(
            f"echo {MARKER}\necho {MARKER} >&2\nexit 0\n", encoding="utf-8", newline="\n"
        )

    lab = _lab()
    handle = provider.provision(build_sandbox_spec(lab, "pytest-freeze-failure"))
    try:
        # A busy learner process (named with the marker so ``ps`` output carries it) advancing a
        # counter, plus wakers that resume every stopped learner process again and again.
        _sh(
            provider,
            handle,
            f": > /workspace/ctr; ln -s /bin/bash /home/learner/{MARKER}; "
            f"(/home/learner/{MARKER} -c 'while :; do echo x >> /workspace/ctr; done' "
            ">/dev/null 2>&1 &); "
            f"for i in $(seq {WAKERS}); do "
            "(bash -c 'while :; do kill -CONT -1; done' >/dev/null 2>&1 &); done",
        )
        time.sleep(0.5)

        clock = Test_Clock()
        runs = In_Memory_GradingRunRepository()
        sessions = In_Memory_LabSessionRepository(runs)
        mastery = In_Memory_MasteryRepository()
        now = clock.now()
        sessions.add_if_no_active(
            LabSession(
                "s1", "u1", lab.lab_id, SessionStatus.GRADING, now, now + config.LAB_SESSION_TTL
            )
        )
        sessions.transition(
            "s1",
            expected={SessionStatus.GRADING},
            to=SessionStatus.GRADING,
            sandbox_ref=handle.sandbox_id,
        )
        runs.add(
            GradingRun("r1", "s1", RunStatus.RUNNING, now, now + config.LAB_GRADING_RUN_DEADLINE)
        )
        recorder = _Recorder(provider)
        job = GradeRun(
            sessions=sessions,
            runs=runs,
            catalog=Fake_LabCatalog([lab]),
            sandbox=recorder,  # type: ignore[arg-type]
            record=RecordCheckResult(runs=runs, mastery=mastery, clock=clock),
            clock=clock,
            bundle_root=bundle_root,
        )
        status = job.execute("r1")

        run = runs.get("r1")
        session = sessions.get("s1")
        assert run is not None and session is not None
        present = _sh(provider, handle, "test -e /grader/checks", user="root").exit_code == 0
        # Stop the wakers' influence on observation: sample the states a few times; any learner
        # process in state T after the thaw would be a failure of the thaw.
        ps = _sh(provider, handle, "/bin/ps -e -o user= -o stat= -o pid=", user="grader").stdout
        states = [
            p[1]
            for p in (line.split() for line in ps.decode().splitlines())
            if len(p) == 3 and p[0] == "learner" and p[2] != "1"
        ]
        before = _sh(provider, handle, "stat -c %s /workspace/ctr").stdout.strip()
        time.sleep(0.6)
        after = _sh(provider, handle, "stat -c %s /workspace/ctr").stdout.strip()
        score = mastery.get("u1", "cn1")
        done = any(runs.first_pass_exists("u1", t.task_id) for t in lab.tasks)
        try:
            yield _Outcome(
                status=status,
                run=run,
                calls=recorder.calls,
                session_status=session.status,
                mastery_score=score.score if score is not None else 0.0,
                completions=done,
                checks_dir_present=present,
                learner_states_after=states,
                counter_resumed=int(after) > int(before),
            )
        finally:
            pass
    finally:
        provider.destroy(handle)
        assert handle not in provider.list_sandboxes()


def test_freeze_was_attempted_but_never_verified(outcome: _Outcome) -> None:
    freezes = [c for c in outcome.calls if c.argv == FREEZE and c.user == "learner"]
    assert len(freezes) == config.LAB_GRADING_FREEZE_ATTEMPTS
    listings = [c for c in outcome.calls if c.argv[:1] == ["/bin/ps"] and c.user == "grader"]
    assert len(listings) == config.LAB_GRADING_FREEZE_ATTEMPTS
    # Evidence: on the real kernel, a running (non-stopped) non-init learner process is listed on
    # every attempt, so the freeze could not be verified.
    for call in listings:
        rows = [line.split() for line in call.stdout.decode().splitlines() if line.strip()]
        running = [r for r in rows if r[0] == "learner" and r[2] != "1" and r[1][0] not in "TZ"]
        assert running, call.stdout


def test_nothing_is_copied_and_no_check_runs(outcome: _Outcome) -> None:
    assert [c for c in outcome.calls if c.kind == "copy_in"] == []
    assert [c for c in outcome.calls if c.argv[:1] == ["/bin/sh"]] == []
    for call in outcome.calls:
        assert not any(a.startswith("/grader/checks") for a in call.argv), call.argv
    assert not outcome.checks_dir_present


def test_run_and_every_check_are_could_not_complete(outcome: _Outcome) -> None:
    assert outcome.status is RunStatus.COULD_NOT_COMPLETE
    assert outcome.run.status is RunStatus.COULD_NOT_COMPLETE
    assert {r.check_id: r.outcome for r in outcome.run.results} == {
        c: CheckOutcome.COULD_NOT_COMPLETE for c in CHECK_IDS
    }
    assert outcome.session_status is SessionStatus.READY


def test_no_mastery_or_completion_is_granted(outcome: _Outcome) -> None:
    assert outcome.mastery_score == 0.0
    assert not outcome.completions


def test_feedback_is_translated_and_carries_no_raw_output(outcome: _Outcome) -> None:
    # The marker was really visible to the Grading_Job through the ps listing ...
    listings = b"".join(c.stdout for c in outcome.calls if c.argv[:1] == ["/bin/ps"])
    assert MARKER.encode() in listings
    # ... and still reaches no stored field or learner view.
    assert MARKER not in repr(outcome.run)
    views = [to_learner_view(r, MESSAGES) for r in outcome.run.results]
    assert [v.message for v in views] == [MESSAGES["lab.feedback.could-not-complete"]] * len(
        CHECK_IDS
    )
    assert MARKER not in repr(views)
    assert all(v.outcome == CheckOutcome.COULD_NOT_COMPLETE.value for v in views)


def test_thawed_exactly_once_and_learner_processes_run_again(outcome: _Outcome) -> None:
    thaws = [i for i, c in enumerate(outcome.calls) if c.argv == THAW and c.user == "learner"]
    assert len(thaws) == 1
    last_freeze = max(i for i, c in enumerate(outcome.calls) if c.argv == FREEZE)
    assert thaws[0] > last_freeze
    assert outcome.learner_states_after, "the learner processes should still exist"
    assert all(not s.startswith("T") for s in outcome.learner_states_after)
    assert outcome.counter_resumed
