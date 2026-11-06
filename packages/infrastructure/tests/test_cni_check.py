"""Unit tests of the post-install CNI check (kubernetes-deployment task 12.8).

The check runs over ``FakeCniCheckApi``: a cluster that enforces NetworkPolicy or ignores it, with a
fake clock that ``sleep`` advances. The pure verdict (``cni_check_outcome``) has its own property
test; here the tests prove the wiring: which facts are observed, the exit status, the message and
the cleanup.
"""

from __future__ import annotations

import logging

import pytest
from edu_it_domain.labs.k8s_types import CniOutcome
from edu_it_infrastructure.cni_check import (
    ALLOWED_PROBE,
    CLIENT_LABEL,
    DENIED_PROBE,
    ENFORCEMENT_ABSENT_MESSAGE,
    CniCheckApi,
    build_network_policies,
    build_probe_pod,
    main,
    run_check,
)
from edu_it_infrastructure.fakes.cni_check_api import FakeCniCheckApi

IMAGE = "registry.example/edu-it-api@sha256:" + "a" * 64
NS = "platform"


def _run(api: FakeCniCheckApi, *, required: bool, budget_s: float = 100.0):  # type: ignore[no-untyped-def]
    return run_check(
        api,
        namespace=NS,
        image=IMAGE,
        required=required,
        budget_s=budget_s,
        monotonic=api.monotonic,
        sleep=api.sleep,
    )


def _main(api: FakeCniCheckApi, *extra: str) -> int:
    return main(
        ["--namespace", NS, "--image", IMAGE, *extra],
        api_factory=lambda: api,
        monotonic=api.monotonic,
        sleep=api.sleep,
    )


def test_fake_satisfies_the_port() -> None:
    assert isinstance(FakeCniCheckApi(), CniCheckApi)


# Claims: 11.9
def test_enforcing_cni_passes_and_removes_probe_objects() -> None:
    api = FakeCniCheckApi(enforcing=True)
    result = _run(api, required=True)
    assert result.outcome is CniOutcome.PASS
    assert (result.allowed_reachable, result.denied_blocked, result.timed_out) == (
        True,
        True,
        False,
    )
    assert sorted(api.created_pod_names) == [ALLOWED_PROBE, DENIED_PROBE]
    assert api.pods == {} and api.policies == {}


# Claims: 11.10
def test_missing_enforcement_fails_when_required(caplog: pytest.LogCaptureFixture) -> None:
    api = FakeCniCheckApi(enforcing=False)
    with caplog.at_level(logging.ERROR):
        assert _main(api, "--required", "true") == 1
    assert ENFORCEMENT_ABSENT_MESSAGE in caplog.text
    assert api.pods == {} and api.policies == {}


# Claims: 11.11
def test_missing_enforcement_warns_and_exits_zero_when_not_required(
    caplog: pytest.LogCaptureFixture,
) -> None:
    api = FakeCniCheckApi(enforcing=False)
    with caplog.at_level(logging.WARNING):
        assert _main(api, "--required", "false") == 0
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any(ENFORCEMENT_ABSENT_MESSAGE in r.getMessage() for r in warnings)


# Claims: 11.12
def test_pods_never_ready_counts_as_timeout_and_follows_required_flag() -> None:
    api = FakeCniCheckApi(ready_after_s=None)
    result = _run(api, required=True, budget_s=20.0)
    assert result.timed_out is True
    assert result.outcome is CniOutcome.FAIL
    assert api.now < 60  # bounded by the budget, well inside the 120 s Job deadline
    api = FakeCniCheckApi(ready_after_s=None)
    assert _run(api, required=False, budget_s=20.0).outcome is CniOutcome.WARN


# Claims: 11.12
def test_creation_rejected_counts_as_timeout_and_still_cleans_up() -> None:
    api = FakeCniCheckApi(reject_pods=True)
    result = _run(api, required=True)
    assert result.timed_out is True and result.outcome is CniOutcome.FAIL
    assert api.pods == {} and api.policies == {}


# Claims: 11.12
def test_cluster_client_failure_follows_required_flag() -> None:
    def broken() -> CniCheckApi:
        raise RuntimeError("no in-cluster config")

    assert (
        main(["--namespace", NS, "--image", IMAGE, "--required", "true"], api_factory=broken) == 1
    )
    assert (
        main(["--namespace", NS, "--image", IMAGE, "--required", "false"], api_factory=broken) == 0
    )


def test_required_defaults_to_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CNI_CHECK_REQUIRED", "true")
    assert _main(FakeCniCheckApi(enforcing=False)) == 1
    monkeypatch.setenv("CNI_CHECK_REQUIRED", "false")
    assert _main(FakeCniCheckApi(enforcing=False)) == 0


def test_missing_arguments_are_a_usage_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CNI_CHECK_NAMESPACE", raising=False)
    monkeypatch.delenv("CNI_CHECK_IMAGE", raising=False)
    assert main([], api_factory=FakeCniCheckApi) == 2


def test_stale_probe_objects_from_an_earlier_run_are_replaced() -> None:
    api = FakeCniCheckApi()
    api.pods[ALLOWED_PROBE] = {"stale": True}
    api._created_at[ALLOWED_PROBE] = 0.0
    api._ips[ALLOWED_PROBE] = "10.9.9.9"
    assert _run(api, required=True).outcome is CniOutcome.PASS


def test_probe_pod_passes_restricted_pod_security() -> None:
    pod = build_probe_pod(ALLOWED_PROBE, "allowed", IMAGE)
    spec = pod["spec"]
    container = spec["containers"][0]
    assert spec["automountServiceAccountToken"] is False
    assert spec["securityContext"]["runAsNonRoot"] is True
    assert container["securityContext"]["allowPrivilegeEscalation"] is False
    assert container["securityContext"]["capabilities"] == {"drop": ["ALL"]}
    assert container["image"] == IMAGE
    assert spec["activeDeadlineSeconds"] > 0


def test_allow_policy_admits_only_the_job_pod_to_the_allowed_probe() -> None:
    deny, allow = build_network_policies()
    assert deny["spec"]["podSelector"]["matchLabels"] == {
        "app.kubernetes.io/component": "cni-check-probe"
    }
    assert "ingress" not in deny["spec"]  # isolate: no ingress rule at all
    assert allow["spec"]["podSelector"]["matchLabels"] == {"edu-it.io/cni-probe": "allowed"}
    (rule,) = allow["spec"]["ingress"]
    assert rule["from"] == [{"podSelector": {"matchLabels": {CLIENT_LABEL[0]: CLIENT_LABEL[1]}}}]
