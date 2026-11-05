"""Unit tests for capacity handling in ProvisionSandbox (kubernetes-deployment task 8.4).

Covers Req 6.8, 6.9, 22.2-22.7: a ``ProvisionCapacityError`` is requeued with the capacity
backoff below the provisioning timeout and failed with reason ``capacity`` at or above it,
never uses up the retry allowance, is recorded as a capacity attempt, deletes any partial
pod and increments the quota rejection counter exactly once per rejection.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from edu_it_api.errors.labs import (
    LAB_CAPACITY,
    LAB_MESSAGES,
    LAB_PROVISION_FAILED,
    LAB_SESSION_INTERRUPTED,
)
from edu_it_application.labs import ProvisionOutcome, ProvisionSandbox
from edu_it_domain import config
from edu_it_domain.labs.models import LabSession, SessionStatus
from edu_it_domain.ports.lab_catalog import LabDefinition
from edu_it_infrastructure.fakes import (
    Fake_LabCatalog,
    Fake_SandboxProvider,
    In_Memory_GradingRunRepository,
    In_Memory_LabSessionRepository,
    Test_Clock,
)
from edu_it_infrastructure.fakes.sandbox_provider import ProvisionMode
from edu_it_infrastructure.telemetry import PrometheusLabMetrics
from prometheus_client import CollectorRegistry

_LAB = LabDefinition(
    lab_id="lab1", kind="terminal", image="img", concept_ids=(), tasks=(), checks=()
)
_BACKOFF = config.LAB_PROVISION_CAPACITY_BACKOFF
_TIMEOUT = config.LAB_PROVISION_TIMEOUT
_QUOTA = "edu_it_lab_quota_rejections_total"


class _Env:
    def __init__(self) -> None:
        self.clock = Test_Clock()
        self.sessions = In_Memory_LabSessionRepository(In_Memory_GradingRunRepository())
        self.sandbox = Fake_SandboxProvider(self.clock)
        self.registry = CollectorRegistry()
        self.job = ProvisionSandbox(
            sessions=self.sessions,
            catalog=Fake_LabCatalog([_LAB]),
            sandbox=self.sandbox,
            clock=self.clock,
            metrics=PrometheusLabMetrics(self.registry),
        )
        now = self.clock.now()
        self.sessions.add_if_no_active(
            LabSession(
                stable_id="s1",
                user_id="u1",
                lab_id="lab1",
                status=SessionStatus.PROVISIONING,
                created_at=now,
                ttl_at=now + config.LAB_SESSION_TTL,
            )
        )

    def session(self) -> LabSession:
        got = self.sessions.get("s1")
        assert got is not None
        return got

    def rejections(self) -> float:
        return self.registry.get_sample_value(_QUOTA) or 0.0

    def failures(self, reason: str) -> float:
        value = self.registry.get_sample_value(
            "edu_it_lab_provision_failures_total", {"reason": reason}
        )
        return value or 0.0


def test_capacity_below_timeout_requeues_with_the_capacity_backoff() -> None:
    env = _Env()
    env.sandbox.script_provision(ProvisionMode.CAPACITY)
    t0 = env.clock.now()
    assert env.job.execute("s1") is ProvisionOutcome.RETRY_SCHEDULED
    s = env.session()
    assert s.status is SessionStatus.PROVISIONING
    assert s.next_provision_at == t0 + _BACKOFF
    assert env.job.execute("s1") is ProvisionOutcome.SKIPPED  # not due before the backoff


def test_capacity_does_not_count_against_the_retry_allowance() -> None:
    env = _Env()
    before = env.session().provision_attempts
    env.sandbox.script_provision(ProvisionMode.CAPACITY)
    env.job.execute("s1")
    s = env.session()
    assert s.provision_attempts == before == 0
    assert s.capacity_attempts == 1


def test_more_capacity_rejections_than_the_allowance_do_not_fail_the_session() -> None:
    env = _Env()
    rejections = config.LAB_PROVISION_MAX_RETRIES + 1
    env.sandbox.script_provision(*([ProvisionMode.CAPACITY] * rejections))
    for _ in range(rejections):
        assert env.job.execute("s1") is ProvisionOutcome.RETRY_SCHEDULED
        env.clock.advance(_BACKOFF)
    s = env.session()
    assert s.status is SessionStatus.PROVISIONING
    assert s.provision_attempts == 0 and s.capacity_attempts == rejections
    # The full allowance of ordinary failures is still available afterwards.
    assert env.job.execute("s1") is ProvisionOutcome.READY


def test_capacity_at_or_above_timeout_fails_with_the_capacity_reason() -> None:
    env = _Env()
    env.sandbox.script_provision(ProvisionMode.CAPACITY)
    env.clock.advance(_TIMEOUT)  # elapsed since the first attempt == timeout
    assert env.job.execute("s1") is ProvisionOutcome.FAILED
    s = env.session()
    assert s.status is SessionStatus.FAILED and s.ended_at is not None
    assert s.provision_attempts == 0 and s.capacity_attempts == 1
    assert env.failures("capacity") == 1
    assert env.job.execute("s1") is ProvisionOutcome.SKIPPED


def test_capacity_just_below_timeout_still_requeues() -> None:
    env = _Env()
    env.sandbox.script_provision(ProvisionMode.CAPACITY)
    env.clock.advance(_TIMEOUT - timedelta(seconds=1))
    assert env.job.execute("s1") is ProvisionOutcome.RETRY_SCHEDULED


def test_quota_rejection_counter_moves_once_per_rejection() -> None:
    env = _Env()
    env.sandbox.script_provision(ProvisionMode.CAPACITY, ProvisionMode.CAPACITY)
    assert env.rejections() == 0
    env.job.execute("s1")
    assert env.rejections() == 1
    env.clock.advance(_BACKOFF)
    env.job.execute("s1")
    assert env.rejections() == 2


def test_final_capacity_failure_counts_its_rejection_once() -> None:
    env = _Env()
    env.sandbox.script_provision(ProvisionMode.CAPACITY)
    env.clock.advance(_TIMEOUT)
    env.job.execute("s1")
    assert env.rejections() == 1


def test_ordinary_failure_does_not_touch_the_quota_counter_or_capacity_attempts() -> None:
    env = _Env()
    env.sandbox.script_provision(ProvisionMode.FAIL)
    assert env.job.execute("s1") is ProvisionOutcome.RETRY_SCHEDULED
    s = env.session()
    assert env.rejections() == 0
    assert s.provision_attempts == 1 and s.capacity_attempts == 0


def test_capacity_with_a_partial_pod_deletes_it() -> None:
    env = _Env()
    env.sandbox.script_provision(ProvisionMode.CAPACITY_PARTIAL)
    assert env.job.execute("s1") is ProvisionOutcome.RETRY_SCHEDULED
    assert env.sandbox.list_sandboxes() == ()
    assert "destroy" in env.sandbox.calls


def test_capacity_then_success_becomes_ready() -> None:
    env = _Env()
    env.sandbox.script_provision(ProvisionMode.CAPACITY)
    env.job.execute("s1")
    env.clock.advance(_BACKOFF)
    assert env.job.execute("s1") is ProvisionOutcome.READY
    assert env.session().status is SessionStatus.READY


def test_works_without_a_metrics_sink() -> None:
    env = _Env()
    job = ProvisionSandbox(
        sessions=env.sessions,
        catalog=Fake_LabCatalog([_LAB]),
        sandbox=env.sandbox,
        clock=env.clock,
    )
    env.sandbox.script_provision(ProvisionMode.CAPACITY)
    assert job.execute("s1") is ProvisionOutcome.RETRY_SCHEDULED


@pytest.mark.parametrize("key", ["labs.capacity", "labs.session_interrupted"])
def test_message_keys_exist_in_both_locales_without_raw_provider_text(key: str) -> None:
    problem = LAB_MESSAGES[key]
    raw = ("exceeded quota", "pods=", "forbidden", "kubernetes", "traceback", "node")
    for locale in ("fr", "en"):
        for text in (problem.title[locale], problem.detail[locale]):
            assert text
            assert not any(r in text.lower() for r in raw)
    assert problem.detail["fr"] != problem.detail["en"]


def test_provisioning_failure_message_is_registered_and_distinct_from_capacity() -> None:
    assert LAB_MESSAGES["labs.provision_failed"] is LAB_PROVISION_FAILED
    assert LAB_MESSAGES["labs.capacity"] is LAB_CAPACITY
    assert LAB_MESSAGES["labs.session_interrupted"] is LAB_SESSION_INTERRUPTED
    assert LAB_CAPACITY.detail["en"] != LAB_PROVISION_FAILED.detail["en"]
