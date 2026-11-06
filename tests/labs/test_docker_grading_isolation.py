"""Docker grading-isolation tests (hands-on-labs task 8.3).

Requirements 10.1 to 10.7, 10.9 and 10.10, evidenced against a real kernel: the real ``GradeRun``
(task 6.10) drives ``SandboxProvider`` over a minimal Check_Bundle loaded from repository
files (``tests/labs/fixtures/grading_isolation_bundle``). Check scripts report only through their
exit status, so each isolation property is asserted by a dedicated Check that must be recorded as
passed, plus call-level assertions from a recording wrapper around the real provider.

Tamper resistance is evidenced by these Docker tests; Fake_Provider tests verify call logic only.
Tests carry the ``docker`` marker and are skipped without a daemon or the local
``edu-it/lab-linux-base:dev`` image. Command forms follow ``lab-images/README.md``.
"""

from __future__ import annotations

import re
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from edu_it_application.labs import GradeRun, RecordCheckResult
from edu_it_domain import config
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
from edu_it_domain.ports.sandbox import SandboxHandle, SandboxProvider
from edu_it_infrastructure.fakes import (
    Fake_LabCatalog,
    In_Memory_GradingRunRepository,
    In_Memory_LabSessionRepository,
    In_Memory_MasteryRepository,
    Test_Clock,
)

from tests.labs.sandbox_backend import (
    OWNER_UID,
    backend_name,
    is_kubernetes,
    make_provider,
    provider_ready,
    skip_reason,
)

IMAGE = "edu-it/lab-linux-base:dev"
BUNDLE_ROOT = Path(__file__).parent / "fixtures"
BUNDLE_NAME = "grading_isolation_bundle"
CHECKS_DIR = BUNDLE_ROOT / BUNDLE_NAME / "sandbox" / "checks"

#: Independent oracle of the Check environment (Req 10.4, 10.5): Config constants only.
CLEAN_ENV = {"PATH": "/usr/bin:/bin", "LANG": "C", "HOME": "/grader/run"}
LEARNER_WRITABLE = ("/workspace", "/home/learner", "/tmp")
FREEZE = ["/bin/kill", "-s", "STOP", "-1"]
THAW = ["/bin/kill", "-s", "CONT", "-1"]

CHECK_IDS = (
    "c_identity",
    "c_env",
    "c_paths",
    "c_bundle",
    "c_frozen",
    "c_noisy_pass",
    "c_planted_fail",
)
PASSING = tuple(c for c in CHECK_IDS if c != "c_planted_fail")


def _daemon_ready() -> bool:
    return provider_ready(IMAGE)


pytestmark = [
    pytest.mark.docker,
    pytest.mark.skipif(not _daemon_ready(), reason=skip_reason()),
]


@dataclass(frozen=True)
class _ImageOnly:
    image: str = IMAGE


# -- recording wrapper around the real provider -------------------------------


@dataclass
class _Call:
    kind: str  # "exec" | "copy_in"
    argv: list[str] = field(default_factory=list)
    user: str | None = None
    env: dict[str, str] | None = None
    cwd: str | None = None
    source: Path | None = None
    dest: str | None = None
    counter_before: bytes | None = None
    counter_after: bytes | None = None


class _Recorder:
    """Delegates to the real provider; records calls; samples the learner counter per Check."""

    def __init__(self, inner: SandboxProvider) -> None:
        self._inner = inner
        self.calls: list[_Call] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def _counter(self, handle: SandboxHandle) -> bytes:
        # A fresh learner exec is not stopped by the earlier SIGSTOP of existing processes.
        return self._inner.exec(
            handle,
            ["/bin/cat", "/workspace/ctr"],
            as_user="learner",
            env={"PATH": "/usr/bin:/bin"},
            cwd="/workspace",
            timeout_s=10,
        ).stdout.strip()

    def exec(self, handle, command, *, as_user, env, cwd, timeout_s=None):  # type: ignore[no-untyped-def]
        call = _Call("exec", list(command), as_user, dict(env), cwd)
        is_check = len(command) == 2 and str(command[1]).startswith("/grader/checks/")
        if is_check:
            call.counter_before = self._counter(handle)
        self.calls.append(call)
        result = self._inner.exec(
            handle, command, as_user=as_user, env=env, cwd=cwd, timeout_s=timeout_s
        )
        if is_check:
            call.counter_after = self._counter(handle)
        return result

    def copy_in(self, handle, source_ro, dest, *, as_user=None):  # type: ignore[no-untyped-def]
        self.calls.append(_Call("copy_in", user=as_user, source=Path(source_ro), dest=dest))
        return self._inner.copy_in(handle, source_ro, dest, as_user=as_user)


# -- environment ----------------------------------------------------------------


def _lab() -> LabDefinition:
    return LabDefinition(
        lab_id="lab-gi",
        kind="terminal",
        image=IMAGE,
        concept_ids=("cn1",),
        tasks=(LabTaskDefinition("t1", required=True),),
        checks=tuple(LabCheckDefinition(c, "t1", 15.0) for c in CHECK_IDS),
        bundle_path=BUNDLE_NAME,
    )


def _sh(
    provider: SandboxProvider,
    handle: SandboxHandle,
    script: str,
    *,
    user: str = "learner",
    timeout_s: float = 30.0,
):  # type: ignore[no-untyped-def]
    return provider.exec(
        handle,
        ["/bin/bash", "-c", script],
        as_user=user,
        env={"PATH": "/usr/bin:/bin"},
        cwd="/workspace" if user == "learner" else "/",
        timeout_s=timeout_s,
    )


def _learner_states(provider: SandboxProvider, handle: SandboxHandle) -> list[str]:
    """States of the learner's processes, PID 1 excepted.

    Docker: one process namespace, listed as the grader and filtered by user. Kubernetes (layout
    B): the grader container cannot see the learner container at all, so the listing runs in the
    learner container exactly as GradeRun's freeze verification does (its own PID excluded).
    """
    if is_kubernetes():
        from edu_it_application.labs.grade_run import _PS_LIST_IN_LEARNER, _parse_ps

        out = provider.exec(
            handle, _PS_LIST_IN_LEARNER, as_user="learner", env={"PATH": "/usr/bin:/bin"}, cwd="/"
        ).stdout
        return [row.state for row in _parse_ps(out, True)]
    ps = _sh(provider, handle, "/bin/ps -e -o user= -o stat= -o pid=", user="grader").stdout
    return [
        p[1]
        for p in (line.split() for line in ps.decode().splitlines())
        if len(p) == 3 and p[0] == "learner" and p[2] != "1"
    ]


@dataclass
class _Graded:
    status: RunStatus | None
    outcomes: dict[str, CheckOutcome]
    calls: list[_Call]
    session_status: SessionStatus
    grader_listing_after: bytes
    learner_states_after: list[str]
    counter_resumed: bool
    mastery_score: float


@pytest.fixture(scope="module")
def provider() -> SandboxProvider:
    return make_provider()


@pytest.fixture(scope="module")
def graded(provider: SandboxProvider) -> Iterator[_Graded]:
    """Run one real GradeRun over the fixture bundle with a hostile, busy learner."""
    lab = _lab()
    handle = provider.provision(build_sandbox_spec(lab, "pytest-grading-iso"))
    try:
        # Learner state before grading: a busy process advancing a counter, and planted
        # "result" files in every learner-writable location.
        _sh(
            provider,
            handle,
            "echo 0 > /workspace/ctr; "
            "(bash -c 'n=0; while :; do n=$((n+1)); echo $n > /workspace/ctr; "
            "/bin/sleep 0.05; done' >/dev/null 2>&1 &) ; "
            "for d in /workspace /tmp /home/learner; do "
            'echo PASS > "$d/result"; echo \'{"passed": true}\' > "$d/result.json"; '
            'echo PASS > "$d/c_planted_fail.sh"; done',
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
            bundle_root=BUNDLE_ROOT,
            provider_name=backend_name(),  # as the Worker composition passes it
        )
        status = job.execute("r1")

        run = runs.get("r1")
        session = sessions.get("s1")
        assert run is not None and session is not None
        listing = _sh(provider, handle, "/bin/ls -A /grader", user="root").stdout
        states = _learner_states(provider, handle)
        before = _sh(provider, handle, "cat /workspace/ctr").stdout.strip()
        time.sleep(0.6)
        after = _sh(provider, handle, "cat /workspace/ctr").stdout.strip()
        score = mastery.get("u1", "cn1")
        yield _Graded(
            status=status,
            outcomes={r.check_id: r.outcome for r in run.results},
            calls=recorder.calls,
            session_status=session.status,
            grader_listing_after=listing,
            learner_states_after=states,
            counter_resumed=int(after) > int(before),
            mastery_score=score.score if score is not None else 0.0,
        )
    finally:
        provider.destroy(handle)


# -- Req 10.1 / 10.3 / 10.4 / 10.5 / 10.7: properties checked by Checks themselves --------


@pytest.mark.parametrize("check_id", PASSING)
def test_isolation_check_passes_inside_the_sandbox(graded: _Graded, check_id: str) -> None:
    assert graded.outcomes[check_id] is CheckOutcome.PASSED, check_id


# -- Req 10.1 / 10.2: bundle copy, root-owned, source read-only, nothing learner-writable -----


@pytest.mark.parametrize("target", ["/grader/checks/c_identity.sh", "/grader/checks", "/grader"])
def test_copied_bundle_is_root_owned_and_not_learner_writable(
    provider: SandboxProvider, target: str
) -> None:
    handle = provider.provision(build_sandbox_spec(_ImageOnly(), "pytest-grading-copy"))
    try:
        provider.copy_in(handle, BUNDLE_ROOT / BUNDLE_NAME / "sandbox", "/grader", as_user="root")
        assert (
            _sh(provider, handle, f"stat -c %u {target}", user="root").stdout.strip()
            == str(OWNER_UID[backend_name()]).encode()
        )
        # The learner can neither modify, replace, delete nor add anything in the bundle.
        for script in (
            f"echo 'exit 0' >> {target}",
            f"chmod 777 {target}",
            "touch /grader/checks/injected.sh",
            "rm -f /grader/checks/c_identity.sh",
            "mv /grader/checks /grader/checks.old",
        ):
            assert _sh(provider, handle, script).exit_code != 0, script
        listing = _sh(provider, handle, "ls /grader/checks", user="root").stdout.split()
        assert sorted(listing) == sorted(f"{c}.sh".encode() for c in CHECK_IDS)
    finally:
        provider.destroy(handle)


def test_bundle_copied_once_from_read_only_source_as_root(graded: _Graded) -> None:
    copies = [c for c in graded.calls if c.kind == "copy_in"]
    assert len(copies) == 1
    (copy,) = copies
    assert copy.user == "root"
    assert copy.dest == "/grader"
    assert copy.source == BUNDLE_ROOT / BUNDLE_NAME / "sandbox"


def test_no_check_or_expected_value_comes_from_a_learner_writable_path(graded: _Graded) -> None:
    # Call level: nothing the Grading_Job executes or copies names a learner-writable path.
    check_calls = [c for c in graded.calls if c.kind == "exec" and c.argv[:1] == ["/bin/sh"]]
    assert [c.argv[1] for c in check_calls] == [f"/grader/checks/{c}.sh" for c in CHECK_IDS]
    for call in graded.calls:
        if call.kind == "copy_in":
            assert not str(call.dest).startswith(LEARNER_WRITABLE)
        elif call.argv[:1] != ["/bin/cat"]:  # the test's own counter sampler is excluded
            assert not any(a.startswith(LEARNER_WRITABLE) for a in call.argv), call.argv
            assert not str(call.cwd).startswith(LEARNER_WRITABLE)
    # Source level: the fixture's Check scripts reference no learner-writable location as code
    # or data. (c_planted_fail only mentions them in a comment.)
    for script in CHECKS_DIR.glob("*.sh"):
        code = "\n".join(
            ln
            for ln in script.read_text(encoding="utf-8").splitlines()
            if not ln.lstrip().startswith("#")
        )
        assert not re.search(r"/workspace|/home/learner|/tmp", code), script.name


# -- Req 10.3 / 10.4 / 10.5: how every Check is invoked --------------------------------------


def test_every_check_runs_as_grader_with_clean_env_absolute_paths_and_explicit_cwd(
    graded: _Graded,
) -> None:
    checks = [c for c in graded.calls if c.kind == "exec" and c.argv[:1] == ["/bin/sh"]]
    assert len(checks) == len(CHECK_IDS)
    for call in checks:
        assert call.user == "grader"
        assert call.env == CLEAN_ENV
        assert call.cwd == "/grader/run"
        assert call.argv[0].startswith("/") and call.argv[1].startswith("/grader/checks/")
    # Every other command the job issues uses an absolute binary path and an explicit cwd too.
    for call in graded.calls:
        if call.kind == "exec":
            assert call.argv[0].startswith("/"), call.argv
            assert call.cwd and call.cwd.startswith("/")
            assert call.env == CLEAN_ENV


# -- Req 10.6: bundle removed after grading ---------------------------------------------------


def test_bundle_is_removed_after_grading(graded: _Graded) -> None:
    assert graded.grader_listing_after.strip() == b""
    assert graded.session_status is SessionStatus.READY


# -- Req 10.7: learner processes frozen during each Check, thawed afterwards --------------------


def test_learner_processes_are_frozen_during_each_check_and_resumed_after(
    graded: _Graded,
) -> None:
    calls = graded.calls
    freeze_at = next(i for i, c in enumerate(calls) if c.argv == FREEZE and c.user == "learner")
    first_check = next(i for i, c in enumerate(calls) if c.argv[:1] == ["/bin/sh"])
    first_copy = next(i for i, c in enumerate(calls) if c.kind == "copy_in")
    assert freeze_at < first_copy < first_check
    for call in calls:
        if call.argv[:1] == ["/bin/sh"]:
            # The counter written by the busy learner process did not move across the Check.
            assert call.counter_before and call.counter_before == call.counter_after, call.argv
    frozen = next(c for c in calls if c.argv == ["/bin/sh", "/grader/checks/c_frozen.sh"])
    assert int(frozen.counter_before or b"0") > 0  # the learner process really had been running
    thaw = [i for i, c in enumerate(calls) if c.argv == THAW and c.user == "learner"]
    assert len(thaw) == 1 and thaw[0] > max(
        i for i, c in enumerate(calls) if c.argv[:1] == ["/bin/sh"]
    )
    assert graded.learner_states_after, "the learner process should still exist"
    assert all(not s.startswith("T") for s in graded.learner_states_after)
    assert graded.counter_resumed


# -- Req 10.9: result from the Check's own channel -----------------------------------------------


def test_outcome_comes_from_the_checks_own_exit_status(graded: _Graded) -> None:
    # Planted "PASS" files in every learner-writable location, and a Check that prints PASS,
    # do not turn a failing Check into a pass.
    assert graded.outcomes["c_planted_fail"] is CheckOutcome.FAILED
    # Output saying FAIL does not turn a passing Check into a failure.
    assert graded.outcomes["c_noisy_pass"] is CheckOutcome.PASSED
    assert graded.status is RunStatus.FAILED
    assert set(graded.outcomes) == set(CHECK_IDS)
