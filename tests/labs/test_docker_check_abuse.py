"""Docker Check-timeout and abuse tests (hands-on-labs task 8.4).

Requirements 11.3, 11.7, 28.1 to 28.7 and 28.9, evidenced with the real ``GradeRun`` against
``SandboxProvider``. An infinite loop, a fork bomb and an unbounded allocation inside a
Check each: finish the run inside the 15 s deadline, record ``could_not_complete`` like a
timeout, leave later Checks to run, make the run ``failed`` and deliver no raw output.

Tests carry the ``docker`` marker and are skipped when no daemon or no local
``edu-it/lab-linux-base:dev`` image is available. Argv forms follow ``lab-images/README.md``.
Check scripts are written to a temporary bundle with LF endings (so Windows checkouts cannot
corrupt them); nothing is written outside ``tmp_path``.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from edu_it_application.labs import GradeRun, RecordCheckResult
from edu_it_domain import config as cfg
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
from edu_it_domain.ports.sandbox import SandboxProvider
from edu_it_infrastructure.clock import System_Clock
from edu_it_infrastructure.fakes import (
    Fake_LabCatalog,
    In_Memory_GradingRunRepository,
    In_Memory_LabSessionRepository,
    In_Memory_MasteryRepository,
)

from tests.labs.sandbox_backend import backend_name, make_provider, provider_ready, skip_reason

IMAGE = "edu-it/lab-linux-base:dev"
MARKER = "RAW-OUTPUT-MARKER-8f3a9c"
CHECK_TIMEOUT_S = 3.0


@dataclass(frozen=True)
class _Img:
    image: str = IMAGE


def _daemon_ready() -> bool:
    return provider_ready(IMAGE)


pytestmark = [
    pytest.mark.docker,
    pytest.mark.skipif(not _daemon_ready(), reason=skip_reason()),
]

# Every script first prints a planted marker to stdout and stderr: it must never be delivered.
_PRELUDE = f"echo {MARKER}\necho {MARKER} >&2\n"
_SCRIPTS = {
    "c_loop": "while :; do :; done\n",
    # Classic fork bomb under bash: BusyBox ash exits on the first failed fork, while bash keeps
    # retrying failed forks (about 30 s at the PID ceiling, see task 8.1), so the bomb saturates
    # the PID limit and only the per-Check timeout ends it.
    # The top shell keeps waiting so the Check itself never returns before its timeout.
    "c_fork": "exec /bin/bash -c 'b() { b | b & }; b; while :; do wait; done'\n",
    # tail buffers an endless line-less stream in memory until the cgroup limit.
    "c_mem": "/usr/bin/tail /dev/zero\n",
    "c_hang": "/bin/sleep 60\n",  # baseline: a plain timeout
    "c_ok": "exit 0\n",
}


def _write_bundle(root: Path) -> None:
    checks = root / "abuse" / "sandbox" / "checks"
    checks.mkdir(parents=True, exist_ok=True)
    for name, body in _SCRIPTS.items():
        (checks / f"{name}.sh").write_text(_PRELUDE + body, encoding="utf-8", newline="\n")


@pytest.fixture(scope="module")
def provider() -> SandboxProvider:
    return make_provider()


class _Run:
    """One real GradeRun over a freshly provisioned Docker Sandbox."""

    def __init__(self, provider: SandboxProvider, tmp_path: Path, check_ids: list[str]):
        _write_bundle(tmp_path)
        self.provider = provider
        self.clock = System_Clock()
        self.lab = LabDefinition(
            lab_id="abuse",
            kind="terminal",
            image=IMAGE,
            concept_ids=(),
            tasks=(LabTaskDefinition("t1", required=True),),
            checks=tuple(LabCheckDefinition(c, "t1", CHECK_TIMEOUT_S) for c in check_ids),
            bundle_path="abuse",
        )
        # One session id per run: the Kubernetes pod name is derived from it (ADR 0009), so a
        # reused id would adopt the pod of the previous run instead of a fresh Sandbox.
        self.handle = provider.provision(
            build_sandbox_spec(_Img(), f"pytest-abuse-{'-'.join(check_ids)}")
        )
        self.runs = In_Memory_GradingRunRepository()
        self.sessions = In_Memory_LabSessionRepository(self.runs)
        now = self.clock.now()
        self.sessions.add_if_no_active(
            LabSession("s1", "u1", "abuse", SessionStatus.GRADING, now, now + cfg.LAB_SESSION_TTL)
        )
        self.sessions.transition(
            "s1",
            expected={SessionStatus.GRADING},
            to=SessionStatus.GRADING,
            sandbox_ref=self.handle.sandbox_id,
        )
        self.runs.add(
            GradingRun("r1", "s1", RunStatus.RUNNING, now, now + cfg.LAB_GRADING_RUN_DEADLINE)
        )
        self.job = GradeRun(
            sessions=self.sessions,
            runs=self.runs,
            catalog=Fake_LabCatalog([self.lab]),
            sandbox=provider,
            record=RecordCheckResult(
                runs=self.runs, mastery=In_Memory_MasteryRepository(), clock=self.clock
            ),
            clock=self.clock,
            bundle_root=tmp_path,
            provider_name=backend_name(),  # as the Worker composition passes it
        )

    def execute(self) -> tuple[RunStatus | None, float]:
        started = time.monotonic()
        status = self.job.execute("r1")
        return status, time.monotonic() - started

    def results(self):  # type: ignore[no-untyped-def]
        run = self.runs.get("r1")
        assert run is not None
        return {r.check_id: r for r in run.results}


@pytest.fixture
def make_run(provider: SandboxProvider, tmp_path: Path) -> Iterator[object]:
    created: list[_Run] = []

    def _make(check_ids: list[str]) -> _Run:
        run = _Run(provider, tmp_path, check_ids)
        created.append(run)
        return run

    try:
        yield _make
    finally:
        for run in created:
            provider.destroy(run.handle)
        for run in created:
            assert run.handle not in provider.list_sandboxes()


def _baseline_timeout(make_run) -> object:  # type: ignore[no-untyped-def]
    run = make_run(["c_hang"])
    run.execute()
    return run.results()["c_hang"]


@pytest.mark.parametrize("abuse", ["c_loop", "c_fork", "c_mem"])
def test_abusive_check_is_contained_like_a_timeout(make_run, abuse: str) -> None:  # type: ignore[no-untyped-def]
    baseline = _baseline_timeout(make_run)
    assert baseline.outcome is CheckOutcome.COULD_NOT_COMPLETE

    run = make_run([abuse, "c_ok"])
    status, elapsed = run.execute()

    # 28.1 / 11.7: the whole run ends inside the 15 s deadline.
    assert elapsed < cfg.LAB_GRADING_RUN_DEADLINE.total_seconds()
    # 28.5 / 11.3: recorded like a timeout.
    results = run.results()
    assert results[abuse].outcome is CheckOutcome.COULD_NOT_COMPLETE
    assert results[abuse].outcome is baseline.outcome
    assert results[abuse].outcome_code == baseline.outcome_code
    assert results[abuse].feedback_key == baseline.feedback_key
    # 28.3 / 28.4: a later Check still runs and passes while budget remains.
    assert results["c_ok"].outcome is CheckOutcome.PASSED
    # 28.7: the run is failed (not passed, not aborted).
    assert status is RunStatus.FAILED
    stored = run.runs.get("r1")
    assert stored is not None and stored.status is RunStatus.FAILED
    # 28.9: no raw output is delivered, in any stored field or learner view.
    assert MARKER not in repr(stored)
    views = [to_learner_view(r, {}) for r in stored.results]
    assert MARKER not in repr(views)
    # The session returns to ready and the Sandbox is still usable afterwards.
    session = run.sessions.get("s1")
    assert session is not None and session.status is SessionStatus.READY
    assert run.provider.health(run.handle).healthy
