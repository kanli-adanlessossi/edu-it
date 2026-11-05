"""Tests for the Kubernetes domain types and the LabMetrics port (kubernetes Req 22.1, 22.6)."""

from __future__ import annotations

from datetime import timedelta

from edu_it_domain.labs.k8s_types import (
    CniOutcome,
    Fail,
    RejectionKind,
    Requeue,
    RuntimeDecision,
)
from edu_it_domain.labs.models import ACTIVE_SESSION_STATUSES, SessionStatus
from edu_it_domain.ports.sandbox import ProvisionCapacityError, ProvisionError


def test_capacity_error_is_a_provision_error_and_keeps_partial_handle() -> None:
    err = ProvisionCapacityError("quota")
    assert isinstance(err, ProvisionError)
    assert err.partial_handle is None


def test_ended_by_infrastructure_is_a_terminal_status() -> None:
    status = SessionStatus("ended-by-infrastructure")
    assert status is SessionStatus.ENDED_BY_INFRASTRUCTURE
    assert not status.is_active
    assert status not in ACTIVE_SESSION_STATUSES


def test_decision_types() -> None:
    assert Requeue(timedelta(seconds=10)).delay == timedelta(seconds=10)
    assert Fail().reason == "capacity"
    assert {k.name for k in RejectionKind} == {
        "CAPACITY",
        "POD_SECURITY_VIOLATION",
        "RUNTIME_CLASS_MISSING",
        "ALREADY_EXISTS",
        "OTHER",
    }
    assert {d.name for d in RuntimeDecision} == {"OK", "WARN", "REFUSE"}
    assert {o.name for o in CniOutcome} == {"PASS", "WARN", "FAIL"}
