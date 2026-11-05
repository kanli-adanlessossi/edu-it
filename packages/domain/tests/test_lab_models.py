"""Tests for lab domain models and sandbox spec (hands-on-labs task 1.3)."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass

from edu_it_domain import config
from edu_it_domain.labs.models import (
    CheckResult,
    GradingRun,
    RunStatus,
    SessionStatus,
)
from edu_it_domain.labs.sandbox_spec import build_sandbox_spec, split_writable_quota
from hypothesis import given
from hypothesis import strategies as st


@dataclass(frozen=True)
class _Lab:
    image: str = "edu-it/linux-base:1"


@given(st.integers(min_value=0, max_value=2**40))
def test_split_sums_exactly(total: int) -> None:
    """Property: the four parts always sum exactly to the total."""
    parts = split_writable_quota(total)
    assert parts.total == total
    assert min(parts.workspace, parts.home, parts.tmp, parts.grader) >= 0


def test_split_default_quota_matches_config() -> None:
    parts = split_writable_quota(config.SANDBOX_WRITABLE_QUOTA)
    assert parts.workspace == config.SANDBOX_QUOTA_WORKSPACE
    assert parts.home == config.SANDBOX_QUOTA_HOME
    assert parts.tmp == config.SANDBOX_QUOTA_TMP
    assert parts.grader == config.SANDBOX_QUOTA_GRADER


def test_spec_is_hardened() -> None:
    spec = build_sandbox_spec(_Lab(), "sess-1")
    assert spec.user != "root"
    assert spec.network_enabled is False
    assert spec.drop_all_capabilities and spec.no_new_privileges
    assert spec.host_mounts == ()
    assert spec.cpu_limit == config.SANDBOX_CPU_LIMIT
    assert spec.memory_limit_bytes == config.SANDBOX_MEMORY_LIMIT
    assert spec.pid_limit == config.SANDBOX_PID_LIMIT
    assert spec.writable_quota_bytes == config.SANDBOX_WRITABLE_QUOTA
    assert sum(m.size_bytes for m in spec.tmpfs_mounts) == config.SANDBOX_WRITABLE_QUOTA
    assert ("edu-it.session", "sess-1") in spec.labels


def test_status_values() -> None:
    assert {s.value for s in SessionStatus} == {
        "provisioning",
        "ready",
        "grading",
        "ended",
        "expired",
        "idle-expired",
        "failed",
        "ended-by-infrastructure",
    }
    assert {s.value for s in RunStatus} == {
        "running",
        "passed",
        "failed",
        "could-not-complete",
    }
    assert SessionStatus.GRADING.is_active and not SessionStatus.ENDED.is_active


def test_check_result_carries_no_output_exit_code_or_path() -> None:
    names = {f.name for f in dataclasses.fields(CheckResult)}
    assert names == {"check_id", "task_id", "outcome", "outcome_code", "feedback_key"}


def test_grading_run_has_timing_fields() -> None:
    names = {f.name for f in dataclasses.fields(GradingRun)}
    assert {"accepted_at", "deadline_at", "started_at"} <= names
