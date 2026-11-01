"""Failure-scenario tests for provisioning failure or slowness (task 8.7).

Drives the real ``ProvisionSandbox`` and ``ReapSessions`` use cases over the
``Fake_SandboxProvider`` (scripted to fail, hang and partially create), in-memory
repositories and the ``Test_Clock``. Numbers come from ``edu_it_domain.config``.

**Validates: Requirements 25.1, 25.2, 25.3, 25.5, 25.6, 25.7**
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from edu_it_api.errors.labs import LAB_PROVISION_FAILED
from edu_it_application.labs.provision_sandbox import ProvisionOutcome, ProvisionSandbox
from edu_it_application.labs.reap_sessions import ReapSessions
from edu_it_domain import config
from edu_it_domain.labs.models import LabSession, SessionStatus
from edu_it_domain.ports.lab_catalog import LabCheckDefinition, LabDefinition, LabTaskDefinition
from edu_it_domain.ports.sandbox import SandboxHandle
from edu_it_infrastructure.fakes import (
    Fake_LabCatalog,
    Fake_SandboxProvider,
    In_Memory_GradingRunRepository,
    In_Memory_LabSessionRepository,
    Test_Clock,
)
from edu_it_infrastructure.fakes.sandbox_provider import ProvisionMode

T0 = datetime(2025, 1, 1, tzinfo=UTC)
MAX_ATTEMPTS = config.LAB_PROVISION_MAX_RETRIES + 1
MIN_INTERVAL = config.LAB_PROVISION_MIN_INTERVAL
TIMEOUT = config.LAB_PROVISION_TIMEOUT
SID = "s1"

_LAB = LabDefinition(
    lab_id="lab1",
    kind="terminal",
    image="img",
    concept_ids=("cn1",),
    tasks=(LabTaskDefinition("t1", required=True),),
    checks=(LabCheckDefinition("c1", "t1", 5.0),),
    bundle_path="lab1",
)


class _FlakyDestroyProvider(Fake_SandboxProvider):
    """Destroy fails while ``fail_destroy`` is set, leaving the sandbox listed."""

    fail_destroy = False

    def destroy(self, handle: SandboxHandle) -> None:
        if self.fail_destroy:
            raise RuntimeError("boom: raw docker daemon output")
        super().destroy(handle)


class _Env:
    def __init__(self, provider_cls: type[Fake_SandboxProvider] = Fake_SandboxProvider, **kw) -> None:  # type: ignore[no-untyped-def]
        self.clock = Test_Clock(T0)
        self.runs = In_Memory_GradingRunRepository()
        self.sessions = In_Memory_LabSessionRepository(self.runs)
        self.sandbox = provider_cls(self.clock, **kw)
        self.job = ProvisionSandbox(
            sessions=self.sessions,
            catalog=Fake_LabCatalog([_LAB]),
            sandbox=self.sandbox,
            clock=self.clock,
        )
        self.reaper = ReapSessions(
            sessions=self.sessions, runs=self.runs, sandbox=self.sandbox, clock=self.clock
        )
        now = self.clock.now()
        self.sessions.add_if_no_active(
            LabSession(
                stable_id=SID,
                user_id="u1",
                lab_id="lab1",
                status=SessionStatus.PROVISIONING,
                created_at=now,
                ttl_at=now + config.LAB_SESSION_TTL,
            )
        )

    def session(self) -> LabSession:
        got = self.sessions.get(SID)
        assert got is not None
        return got

    def run_attempts(self) -> list[tuple[ProvisionOutcome, datetime, timedelta]]:
        """Deliver the job until it stops retrying; return (outcome, start, duration)."""
        log = []
        for _ in range(MAX_ATTEMPTS + 2):
            start = self.clock.now()
            outcome = self.job.execute(SID)
            if outcome is ProvisionOutcome.SKIPPED:
                break
            log.append((outcome, start, self.clock.now() - start))
            if outcome is not ProvisionOutcome.RETRY_SCHEDULED:
                break
            self.clock.advance(MIN_INTERVAL)
        return log


def test_config_numbers_match_the_requirement() -> None:
    assert MAX_ATTEMPTS == 3
    assert MIN_INTERVAL == timedelta(seconds=10)
    assert TIMEOUT == timedelta(seconds=30)


@pytest.mark.parametrize(
    "mode", [ProvisionMode.FAIL, ProvisionMode.PARTIAL, ProvisionMode.UNAVAILABLE]
)
def test_persistent_failure_makes_three_spaced_attempts_then_failed(mode: ProvisionMode) -> None:
    env = _Env(default_mode=mode)
    log = env.run_attempts()

    assert [o for o, _, _ in log] == [
        ProvisionOutcome.RETRY_SCHEDULED,
        ProvisionOutcome.RETRY_SCHEDULED,
        ProvisionOutcome.FAILED,
    ]
    assert env.sandbox.calls.count("provision") == MAX_ATTEMPTS
    starts = [s for _, s, _ in log]
    assert all(b - a >= MIN_INTERVAL for a, b in zip(starts, starts[1:], strict=False))
    assert all(d < TIMEOUT for _, _, d in log)
    assert env.session().status is SessionStatus.FAILED
    assert not env.session().status.is_active
    assert env.session().sandbox_ref is None
    assert env.sandbox.list_sandboxes() == ()


def test_attempts_are_not_started_before_the_minimum_interval() -> None:
    env = _Env(default_mode=ProvisionMode.FAIL)
    assert env.job.execute(SID) is ProvisionOutcome.RETRY_SCHEDULED
    env.clock.advance(MIN_INTERVAL - timedelta(seconds=1))
    assert env.job.execute(SID) is ProvisionOutcome.SKIPPED
    assert env.sandbox.calls.count("provision") == 1
    env.clock.advance(timedelta(seconds=1))
    assert env.job.execute(SID) is ProvisionOutcome.RETRY_SCHEDULED
    assert env.sandbox.calls.count("provision") == 2


def test_partial_creation_destroys_every_partial_handle() -> None:
    env = _Env()
    env.sandbox.script_provision(ProvisionMode.PARTIAL, ProvisionMode.PARTIAL, ProvisionMode.PARTIAL)
    env.run_attempts()

    assert env.sandbox.calls.count("destroy") == MAX_ATTEMPTS
    assert env.sandbox.list_sandboxes() == ()
    assert env.sandbox.calls.count("provision") == MAX_ATTEMPTS


def test_hang_is_cut_at_the_provision_timeout_and_retried() -> None:
    env = _Env(hang_duration=TIMEOUT - timedelta(seconds=1))
    env.sandbox.script_provision(ProvisionMode.HANG, ProvisionMode.HANG)
    log = env.run_attempts()

    assert [o for o, _, _ in log] == [
        ProvisionOutcome.RETRY_SCHEDULED,
        ProvisionOutcome.RETRY_SCHEDULED,
        ProvisionOutcome.READY,
    ]
    assert all(d < TIMEOUT for _, _, d in log)
    assert env.session().status is SessionStatus.READY


def test_hang_on_every_attempt_ends_failed() -> None:
    env = _Env(default_mode=ProvisionMode.HANG, hang_duration=TIMEOUT - timedelta(seconds=1))
    log = env.run_attempts()

    assert len(log) == MAX_ATTEMPTS
    assert log[-1][0] is ProvisionOutcome.FAILED
    assert env.session().status is SessionStatus.FAILED


def test_slow_provision_over_budget_is_destroyed_and_never_ready() -> None:
    env = _Env(provision_duration=TIMEOUT + timedelta(seconds=1))
    assert env.job.execute(SID) is ProvisionOutcome.RETRY_SCHEDULED

    assert env.session().status is SessionStatus.PROVISIONING
    assert env.session().sandbox_ref is None
    assert env.sandbox.list_sandboxes() == ()


def test_recovery_on_a_later_attempt_becomes_ready() -> None:
    env = _Env()
    env.sandbox.script_provision(ProvisionMode.FAIL, ProvisionMode.PARTIAL)
    log = env.run_attempts()

    assert log[-1][0] is ProvisionOutcome.READY
    assert env.session().status is SessionStatus.READY
    assert len(env.sandbox.list_sandboxes()) == 1  # only the successful one


def test_failed_session_shows_translated_error_without_raw_provider_output() -> None:
    env = _Env(default_mode=ProvisionMode.PARTIAL)
    env.run_attempts()
    assert env.session().status is SessionStatus.FAILED

    raw = ("provision failed", "partially created", "fake-", "Traceback", "boom")
    for locale in ("fr", "en"):
        title, detail = LAB_PROVISION_FAILED.title[locale], LAB_PROVISION_FAILED.detail[locale]
        assert title and detail
        for text in (title, detail):
            assert not any(r.lower() in text.lower() for r in raw)
    assert LAB_PROVISION_FAILED.title["fr"] != LAB_PROVISION_FAILED.title["en"]


def test_reaper_destroys_leftovers_of_a_failed_session() -> None:
    env = _Env(_FlakyDestroyProvider, default_mode=ProvisionMode.PARTIAL)
    env.sandbox.fail_destroy = True  # type: ignore[attr-defined]
    env.run_attempts()

    assert env.session().status is SessionStatus.FAILED
    leftovers = env.sandbox.list_sandboxes()
    assert len(leftovers) == MAX_ATTEMPTS  # every destroy failed, nothing masked the outcome

    env.sandbox.fail_destroy = False  # type: ignore[attr-defined]
    env.clock.advance(config.LAB_REAPER_INTERVAL)
    report = env.reaper.execute()

    assert report.destroyed == MAX_ATTEMPTS
    assert env.sandbox.list_sandboxes() == ()
    assert env.session().status is SessionStatus.FAILED
