"""Platform NetworkPolicies (kubernetes-deployment Req 10.10, 11.1 to 11.6, 11.13, 11.14).

Renders the Chart with ``helm template``; skipped when ``helm`` is not installed.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

CHART_DIR = Path(__file__).resolve().parents[2] / "deploy" / "helm" / "edu-it"
VALUES_FILES = ["values-dev.yaml", "values-eks.yaml", "values-aks.yaml", "values-onprem.yaml"]
_DIGEST = "sha256:" + "a" * 64
_IMAGES = [
    arg
    for name in ("web", "api", "worker", "migrations")
    for arg in ("--set", f"images.{name}.digest={_DIGEST}")
]

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm is not installed")


def _helm(values_file: str, *sets: str) -> subprocess.CompletedProcess[bytes]:
    extra = [arg for s in sets for arg in ("--set", s)]
    return subprocess.run(
        ["helm", "template", "t", str(CHART_DIR), "-f", str(CHART_DIR / values_file), *_IMAGES, *extra],
        capture_output=True,
        timeout=120,
        check=False,
    )


def _policies(values_file: str = "values-eks.yaml", *sets: str) -> dict[str, dict[str, Any]]:
    """Platform NetworkPolicies by name suffix (``api-egress`` for ``t-edu-it-platform-api-egress``)."""
    done = _helm(values_file, *sets)
    assert done.returncode == 0, done.stderr.decode("utf-8", "replace")
    docs = [d for d in yaml.safe_load_all(done.stdout) if d]
    prefix = "t-edu-it-platform-"
    return {
        d["metadata"]["name"].removeprefix(prefix): d
        for d in docs
        if d["kind"] == "NetworkPolicy" and d["metadata"]["name"].startswith(prefix)
    }


def _ports(rule: dict[str, Any]) -> set[tuple[str, int]]:
    return {(p["protocol"], p["port"]) for p in rule.get("ports", [])}


def _egress_ports(policy: dict[str, Any]) -> set[tuple[str, int]]:
    return {p for rule in policy["spec"]["egress"] for p in _ports(rule)}


def _component(policy: dict[str, Any]) -> str:
    return policy["spec"]["podSelector"]["matchLabels"]["app.kubernetes.io/component"]


# Claims: 11.1
@pytest.mark.parametrize("values_file", VALUES_FILES)
def test_default_deny_selects_all_pods_for_both_directions(values_file: str) -> None:
    deny = _policies(values_file)["default-deny"]
    assert deny["metadata"]["namespace"] == "platform"
    assert deny["spec"]["podSelector"] == {}
    assert deny["spec"]["policyTypes"] == ["Ingress", "Egress"]
    assert "ingress" not in deny["spec"] and "egress" not in deny["spec"]


# Claims: 11.2, 11.5, 11.6
def test_datastore_ports_only_for_api_worker_and_migration_postgres() -> None:
    policies = _policies()
    api, worker, mig = (policies[f"{c}-egress"] for c in ("api", "worker", "migration"))
    for policy in (api, worker):
        assert {("TCP", 5432), ("TCP", 6379)} <= _egress_ports(policy)
    assert ("TCP", 5432) in _egress_ports(mig) and ("TCP", 6379) not in _egress_ports(mig)
    for name, policy in policies.items():
        if name not in {"api-egress", "worker-egress", "migration-egress"}:
            assert not {("TCP", 5432), ("TCP", 6379)} & _egress_ports(policy) if "egress" in policy["spec"] else True


# Claims: 11.13, 11.14
@pytest.mark.parametrize("values_file", VALUES_FILES)
def test_kubernetes_api_egress_is_ipblock_from_values(values_file: str) -> None:
    policies = _policies(values_file, "networkPolicy.kubernetesApi.cidrs={192.0.2.0/24,198.51.100.7}")
    for name in ("api-egress", "worker-egress", "cni-check"):
        rules = [r for r in policies[name]["spec"]["egress"] if any("ipBlock" in t for t in r.get("to", []))]
        (rule,) = rules
        assert [t["ipBlock"]["cidr"] for t in rule["to"]] == ["192.0.2.0/24", "198.51.100.7/32"]
        assert _ports(rule) == {("TCP", 443), ("TCP", 6443)}


# Claims: 11.14
def test_empty_kubernetes_api_cidrs_fails_rendering_and_names_the_key() -> None:
    # The schema rejects an empty list first; skip it to reach the template-level `fail` helper.
    done = subprocess.run(
        [
            "helm", "template", "t", str(CHART_DIR), "-f", str(CHART_DIR / "values-eks.yaml"),
            *_IMAGES, "--set", "networkPolicy.kubernetesApi.cidrs=null", "--skip-schema-validation",
        ],
        capture_output=True,
        timeout=120,
        check=False,
    )
    assert done.returncode != 0
    assert "networkPolicy.kubernetesApi.cidrs" in done.stderr.decode("utf-8", "replace")
    assert not done.stdout.strip()


# Claims: 10.10, 11.3
def test_llm_egress_is_worker_only() -> None:
    policies = _policies("values-eks.yaml", "llm.endpoint=https://llm.example.eu:8443/v1")
    assert ("TCP", 8443) in _egress_ports(policies["worker-egress"])
    for name, policy in policies.items():
        if name != "worker-egress" and "egress" in policy["spec"]:
            assert ("TCP", 8443) not in _egress_ports(policy)
    # Worker: Kubernetes API, datastores, LLM and DNS only.
    assert _egress_ports(policies["worker-egress"]) == {
        ("TCP", 443),
        ("TCP", 6443),
        ("TCP", 5432),
        ("TCP", 6379),
        ("TCP", 8443),
        ("UDP", 53),
        ("TCP", 53),
    }


# Claims: 11.5
def test_api_egress_allows_object_storage_port_and_nothing_else() -> None:
    policies = _policies("values-eks.yaml", "objectStorage.endpoint=http://minio.example:9000")
    assert _egress_ports(policies["api-egress"]) == {
        ("TCP", 443),
        ("TCP", 6443),
        ("TCP", 5432),
        ("TCP", 6379),
        ("TCP", 9000),
        ("UDP", 53),
        ("TCP", 53),
    }


# Claims: 11.6
def test_migration_egress_is_postgres_and_dns_only() -> None:
    mig = _policies()["migration-egress"]
    assert _component(mig) == "migration"
    assert _egress_ports(mig) == {("TCP", 5432), ("UDP", 53), ("TCP", 53)}


# Claims: 11.4
def test_ingress_to_api_and_web_from_ingress_controller_only() -> None:
    policies = _policies()
    controller = {"matchLabels": {"kubernetes.io/metadata.name": "ingress-nginx"}}
    (api_rule,) = policies["api-ingress"]["spec"]["ingress"]
    assert {"namespaceSelector": controller} in api_rule["from"]
    assert _ports(api_rule) == {("TCP", 8000)}
    (web_rule,) = policies["web-ingress"]["spec"]["ingress"]
    assert web_rule["from"] == [{"namespaceSelector": controller}]
    assert _ports(web_rule) == {("TCP", 3000)}
    assert "worker-ingress" not in policies


# Claims: 11.4 (OQ-4)
def test_monitoring_ingress_off_by_default_and_metrics_port_only_when_enabled() -> None:
    assert "worker-ingress" not in _policies()
    policies = _policies("values-eks.yaml", "networkPolicy.monitoringIngress.enabled=true")
    monitoring = {"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "monitoring"}}}
    for name in ("api-ingress", "worker-ingress"):
        rules = [r for r in policies[name]["spec"]["ingress"] if monitoring in r["from"]]
        (rule,) = rules
        assert rule["from"] == [monitoring]
        assert _ports(rule) == {("TCP", 9090)}


# Claims: 11.9
def test_cni_check_job_reaches_kubernetes_api_and_probes_on_8080_only() -> None:
    policy = _policies()["cni-check"]
    assert policy["spec"]["policyTypes"] == ["Egress"]
    assert _component(policy) == "cni-check"
    probe_rules = [
        r for r in policy["spec"]["egress"] if any("podSelector" in t for t in r.get("to", []))
    ]
    (probe_rule,) = probe_rules
    assert probe_rule["to"] == [
        {"podSelector": {"matchLabels": {"app.kubernetes.io/component": "cni-check-probe"}}}
    ]
    assert _ports(probe_rule) == {("TCP", 8080)}
    assert _egress_ports(policy) == {("TCP", 443), ("TCP", 6443), ("TCP", 8080)}


# Claims: 11.2
def test_dev_datastore_ingress_only_with_bundled_datastores() -> None:
    assert not {"dev-postgres-ingress", "dev-redis-ingress"} & set(_policies("values-eks.yaml"))
    dev = _policies("values-dev.yaml")
    callers = {
        name: {r["podSelector"]["matchLabels"]["app.kubernetes.io/component"] for r in rule["from"]}
        for name in ("dev-postgres-ingress", "dev-redis-ingress")
        for rule in dev[name]["spec"]["ingress"]
    }
    assert callers["dev-postgres-ingress"] == {"api", "worker", "migration"}
    assert callers["dev-redis-ingress"] == {"api", "worker"}
