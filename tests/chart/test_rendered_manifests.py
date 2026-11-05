"""Rendered-manifest tests over all four Values_Files (kubernetes-deployment task 12.13).

Renders the Chart with ``helm template`` for the dev, eks, aks and onprem Values_Files and
checks the cross-cutting rules: kind allowlist, RBAC, NetworkPolicies, quota and limit range,
probes, resources, security contexts, PriorityClass, hook ordering, termination grace, ingress
modes, secrets, ``helm lint`` and ``kubeconform``. Per-feature detail tests live in the sibling
files; this file runs the shared rules against every Values_File. Skipped when ``helm`` is not
installed.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from functools import cache
from pathlib import Path
from typing import Any

import pytest
import yaml
from edu_it_domain.termination import check_termination_grace

CHART_DIR = Path(__file__).resolve().parents[2] / "deploy" / "helm" / "edu-it"
VALUES_FILES = ["values-dev.yaml", "values-eks.yaml", "values-aks.yaml", "values-onprem.yaml"]
_DIGEST = "sha256:" + "a" * 64
_BASE = [
    arg
    for name in ("web", "api", "worker", "migrations")
    for arg in ("--set", f"images.{name}.digest={_DIGEST}")
]
PREFIX = "t-edu-it-"

# Upstream kinds the Chart may render, plus the named extension kinds (Req 7.2).
UPSTREAM_KINDS = {
    "ConfigMap", "Deployment", "HorizontalPodAutoscaler", "Ingress", "Job", "LimitRange",
    "Namespace", "NetworkPolicy", "Pod", "PodDisruptionBudget", "PriorityClass", "ResourceQuota",
    "Role", "RoleBinding",
    "RuntimeClass", "Secret", "Service", "ServiceAccount", "StatefulSet",
}  # fmt: skip
EXTENSION_KINDS = {
    "ExternalSecret",
    "Gateway",
    "HTTPRoute",
    "ServiceMonitor",
    "PrometheusRule",
    "ClusterPolicy",
}
CLOUD_SPECIFIC_GROUPS = (
    "amazonaws.com",
    "azure.com",
    "azure.microsoft.com",
    "cloud.google.com",
    "k8s.aws",
)

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm is not installed")


def _helm(args: list[str], values_file: str, *sets: str) -> subprocess.CompletedProcess[bytes]:
    extra = [arg for s in sets for arg in ("--set", s)]
    return subprocess.run(
        ["helm", *args, "t", str(CHART_DIR), "-f", str(CHART_DIR / values_file), *_BASE, *extra],
        capture_output=True,
        timeout=180,
        check=False,
    )


@cache
def _rendered(values_file: str, *sets: str) -> tuple[bytes, tuple[dict[str, Any], ...]]:
    done = _helm(["template"], values_file, *sets)
    assert done.returncode == 0, done.stderr.decode("utf-8", "replace")
    return done.stdout, tuple(d for d in yaml.safe_load_all(done.stdout) if d)


def _docs(values_file: str, *sets: str) -> list[dict[str, Any]]:
    return list(_rendered(values_file, *sets)[1])


def _values(values_file: str) -> dict[str, Any]:
    return yaml.safe_load((CHART_DIR / values_file).read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def _of_kind(docs: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [d for d in docs if d["kind"] == kind]


def _name(doc: dict[str, Any]) -> str:
    return str(doc["metadata"]["name"]).removeprefix(PREFIX)


def _pod_spec(doc: dict[str, Any]) -> dict[str, Any]:
    if doc["kind"] == "Pod":
        return doc["spec"]  # type: ignore[no-any-return]
    return doc["spec"]["template"]["spec"]  # type: ignore[no-any-return]


def _platform_workloads(docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every pod-bearing platform object (Deployments, Jobs, the smoke-test Pod, dev datastores)."""
    return [d for d in docs if d["kind"] in {"Deployment", "Job", "Pod", "StatefulSet"}]


def _containers(spec: dict[str, Any]) -> list[dict[str, Any]]:
    return [*spec.get("initContainers", []), *spec["containers"]]


# Claims: 7.2
@pytest.mark.parametrize("values_file", VALUES_FILES)
def test_only_allowlisted_kinds_and_no_cloud_specific_kinds(values_file: str) -> None:
    docs = _docs(values_file)
    kinds = {d["kind"] for d in docs}
    assert kinds <= UPSTREAM_KINDS | EXTENSION_KINDS, kinds - (UPSTREAM_KINDS | EXTENSION_KINDS)
    # The image policy kind renders only when imagePolicy.enabled is true (default false).
    assert "ClusterPolicy" not in kinds
    for doc in docs:
        group = str(doc["apiVersion"]).split("/")[0]
        assert not any(group.endswith(cloud) for cloud in CLOUD_SPECIFIC_GROUPS), (
            doc["kind"],
            group,
        )


# Claims: 7.2
def test_image_policy_kind_renders_only_when_enabled() -> None:
    docs = _docs("values-eks.yaml", "imagePolicy.enabled=true")
    (policy,) = _of_kind(docs, "ClusterPolicy")
    assert policy["apiVersion"].startswith("kyverno.io/")


# Claims: 4.1, 4.2, 4.3
@pytest.mark.parametrize("values_file", VALUES_FILES)
def test_worker_has_one_role_and_one_binding_in_labs_with_exact_rules(values_file: str) -> None:
    docs = _docs(values_file)
    labs = _values(values_file)["namespaces"]["labs"]
    (role,) = [d for d in _of_kind(docs, "Role") if _name(d) == "worker"]
    assert role["metadata"]["namespace"] == labs
    rules = {(tuple(r["apiGroups"]), tuple(r["resources"])): set(r["verbs"]) for r in role["rules"]}
    assert rules == {
        (("",), ("pods",)): {"create", "get", "list", "watch", "delete"},
        (("",), ("pods/exec",)): {"create", "get"},
    }
    assert all("resourceNames" not in r for r in role["rules"])
    assert "*" not in str(role["rules"])
    (binding,) = [d for d in _of_kind(docs, "RoleBinding") if _name(d) == "worker"]
    assert binding["metadata"]["namespace"] == labs
    assert binding["roleRef"]["name"] == role["metadata"]["name"]
    assert len(binding["subjects"]) == 1
    assert binding["subjects"][0]["kind"] == "ServiceAccount"
    assert binding["subjects"][0]["name"] == f"{PREFIX}worker"
    # No cluster-scoped grants and no Worker binding outside the labs namespace.
    assert not _of_kind(docs, "ClusterRole") and not _of_kind(docs, "ClusterRoleBinding")
    for other in _of_kind(docs, "RoleBinding"):
        if any(s["name"] == f"{PREFIX}worker" for s in other["subjects"]):
            assert other["metadata"]["namespace"] == labs


# Claims: 4.6, 4.7
@pytest.mark.parametrize("values_file", VALUES_FILES)
def test_service_accounts_and_api_rbac(values_file: str) -> None:
    docs = _docs(values_file)
    accounts = {_name(d): d for d in _of_kind(docs, "ServiceAccount")}
    assert {"api", "worker", "migration"} <= accounts.keys()
    for name in ("api", "worker", "migration"):
        assert accounts[name]["automountServiceAccountToken"] is False
    (api_role,) = [d for d in _of_kind(docs, "Role") if _name(d) == "api"]
    assert api_role["metadata"]["namespace"] == _values(values_file)["namespaces"]["labs"]
    rules = {
        (tuple(r["apiGroups"]), tuple(r["resources"])): set(r["verbs"]) for r in api_role["rules"]
    }
    assert rules == {(("",), ("pods",)): {"get"}, (("",), ("pods/exec",)): {"create"}}
    forbidden = {"create", "update", "patch", "delete", "deletecollection"}
    for role in _of_kind(docs, "Role"):
        for rule in role["rules"]:
            if _name(role) == "api" and "pods" in rule["resources"]:
                assert not forbidden & set(rule["verbs"])
    # Pod specs mount the token only where the workload calls the Kubernetes API.
    for doc in _of_kind(docs, "Deployment"):
        calls_api = _name(doc) in {"api", "worker"}
        assert _pod_spec(doc).get("automountServiceAccountToken", False) is calls_api, _name(doc)


# Claims: 3.1, 3.2, 3.7
@pytest.mark.parametrize("values_file", VALUES_FILES)
def test_labs_policies_default_deny_allowlist_and_dns_off_by_default(values_file: str) -> None:
    docs = _docs(values_file)
    labs = _values(values_file)["namespaces"]["labs"]
    policies = {
        _name(d): d for d in _of_kind(docs, "NetworkPolicy") if d["metadata"]["namespace"] == labs
    }
    assert set(policies) == {"labs-default-deny", "labs-allow-api", "labs-allow-worker"}
    deny = policies["labs-default-deny"]["spec"]
    assert deny["podSelector"] == {} and set(deny["policyTypes"]) == {"Ingress", "Egress"}
    assert "ingress" not in deny and "egress" not in deny
    for name, component in (("labs-allow-api", "api"), ("labs-allow-worker", "worker")):
        spec = policies[name]["spec"]
        assert spec["policyTypes"] == ["Ingress"] and "egress" not in spec
        (rule,) = spec["ingress"]
        (source,) = rule["from"]
        assert source["podSelector"]["matchLabels"]["app.kubernetes.io/component"] == component
    # DNS egress is off by default and renders only on request.
    assert all("egress" not in p["spec"] for p in policies.values())
    with_dns = _docs(values_file, "labs.dnsEgress=true")
    (dns_policy,) = [
        d
        for d in _of_kind(with_dns, "NetworkPolicy")
        if d["metadata"]["namespace"] == labs and "egress" in d["spec"]
    ]
    ports = {
        (p["protocol"], p["port"]) for rule in dns_policy["spec"]["egress"] for p in rule["ports"]
    }
    assert ports == {("UDP", 53), ("TCP", 53)}


# Claims: 3.3, 3.4, 3.5
@pytest.mark.parametrize("values_file", VALUES_FILES)
def test_quota_and_limit_range_keys_are_rendered_from_values(values_file: str) -> None:
    docs = _docs(values_file)
    labs_values = _values(values_file)["labs"]
    (quota,) = _of_kind(docs, "ResourceQuota")
    assert set(quota["spec"]["hard"]) == {
        "pods", "requests.cpu", "limits.cpu", "requests.memory", "limits.memory",
        "requests.ephemeral-storage", "limits.ephemeral-storage",
    }  # fmt: skip
    assert len(labs_values["quota"]) == 7
    assert all(str(v) for v in quota["spec"]["hard"].values())
    (limit_range,) = _of_kind(docs, "LimitRange")
    (limit,) = limit_range["spec"]["limits"]
    assert limit["type"] == "Container"
    for section in ("default", "defaultRequest", "max"):
        assert set(limit[section]) == {"cpu", "memory", "ephemeral-storage"}
    assert len(labs_values["limitRange"]) == 9
    # Every key flows into the manifest: changing a value changes what is rendered.
    changed = _docs(values_file, "labs.quota.pods=7", "labs.limitRange.maxMemory=3Gi")
    assert _of_kind(changed, "ResourceQuota")[0]["spec"]["hard"]["pods"] == "7"
    assert _of_kind(changed, "LimitRange")[0]["spec"]["limits"][0]["max"]["memory"] == "3Gi"


def _platform_policies(values_file: str) -> dict[str, dict[str, Any]]:
    platform = _values(values_file)["namespaces"]["platform"]
    return {
        _name(d).removeprefix("platform-"): d
        for d in _of_kind(_docs(values_file), "NetworkPolicy")
        if d["metadata"]["namespace"] == platform
    }


def _egress_ports(policy: dict[str, Any]) -> set[int]:
    return {p["port"] for rule in policy["spec"]["egress"] for p in rule.get("ports", [])}


# Claims: 11.1, 11.2, 11.3, 11.5, 11.6, 11.13
@pytest.mark.parametrize("values_file", VALUES_FILES)
def test_platform_policies_default_deny_and_api_server_ipblock(values_file: str) -> None:
    policies = _platform_policies(values_file)
    deny = policies["default-deny"]["spec"]
    assert deny["podSelector"] == {} and set(deny["policyTypes"]) == {"Ingress", "Egress"}
    cidrs = _values(values_file)["networkPolicy"]["kubernetesApi"]["cidrs"]
    assert cidrs
    for name in ("api-egress", "worker-egress"):
        blocks = [
            to["ipBlock"]["cidr"]
            for rule in policies[name]["spec"]["egress"]
            for to in rule.get("to", [])
            if "ipBlock" in to
        ]
        assert set(cidrs) <= set(blocks), name
    assert {5432, 6379} <= _egress_ports(policies["api-egress"])
    assert {5432, 6379} <= _egress_ports(policies["worker-egress"])
    # The Migration_Job reaches PostgreSQL and DNS only.
    assert _egress_ports(policies["migration-egress"]) == {5432, 53}
    assert not any(
        "ipBlock" in to
        for rule in policies["migration-egress"]["spec"]["egress"]
        for to in rule.get("to", [])
    )


# Claims: 11.13
def test_api_server_ipblock_follows_the_cidr_values() -> None:
    policies = {
        _name(d): d
        for d in _of_kind(
            _docs("values-eks.yaml", "networkPolicy.kubernetesApi.cidrs={192.0.2.0/24}"),
            "NetworkPolicy",
        )
    }
    for name in ("platform-api-egress", "platform-worker-egress"):
        first = policies[name]["spec"]["egress"][0]["to"]
        assert first == [{"ipBlock": {"cidr": "192.0.2.0/24"}}]


# Claims: 11.2, 11.4
@pytest.mark.parametrize("values_file", VALUES_FILES)
def test_datastore_ports_and_api_ingress_are_restricted(values_file: str) -> None:
    policies = _platform_policies(values_file)
    for name, policy in policies.items():
        component = (
            policy["spec"]["podSelector"].get("matchLabels", {}).get("app.kubernetes.io/component")
        )
        if "egress" in policy["spec"] and _egress_ports(policy) & {5432, 6379}:
            assert component in {"api", "worker", "migration"}, name
    (rule,) = policies["api-ingress"]["spec"]["ingress"]
    assert {p["port"] for p in rule["ports"]} == {_values(values_file)["api"]["port"]}


# Claims: 10.10, 11.3
@pytest.mark.parametrize("values_file", VALUES_FILES)
def test_only_the_worker_has_llm_egress(values_file: str) -> None:
    policies = _platform_policies(values_file)
    selecting = {
        policy["spec"]["podSelector"]["matchLabels"]["app.kubernetes.io/component"]
        for policy in policies.values()
        if any("llm" in str(rule).lower() for rule in policy["spec"].get("egress", []))
    }
    assert selecting <= {"worker"}
    labs = _values(values_file)["namespaces"]["labs"]
    for doc in _of_kind(_docs(values_file), "NetworkPolicy"):
        if doc["metadata"]["namespace"] == labs:
            assert "egress" not in doc["spec"]


# Claims: 8.1
@pytest.mark.parametrize("values_file", VALUES_FILES)
def test_probes_on_every_long_running_workload(values_file: str) -> None:
    for doc in _of_kind(_docs(values_file), "Deployment"):
        container = _pod_spec(doc)["containers"][0]
        for key in ("startupProbe", "livenessProbe", "readinessProbe"):
            probe = container[key]
            assert 1 <= probe["periodSeconds"] <= 60
            assert 1 <= probe["timeoutSeconds"] <= min(30, probe["periodSeconds"])
            assert 1 <= probe["failureThreshold"] <= 30


def _quantity(value: str) -> float:
    units = {
        "m": 1e-3,
        "Ki": 2**10,
        "Mi": 2**20,
        "Gi": 2**30,
        "Ti": 2**40,
        "k": 1e3,
        "M": 1e6,
        "G": 1e9,
    }
    match = re.fullmatch(r"(\d+(?:\.\d+)?)([A-Za-z]*)", str(value))
    assert match, value
    return float(match.group(1)) * units.get(match.group(2), 1)


# Claims: 8.5
@pytest.mark.parametrize("values_file", VALUES_FILES)
def test_every_platform_container_has_cpu_memory_requests_and_limits(values_file: str) -> None:
    workloads = _platform_workloads(_docs(values_file))
    assert workloads
    for doc in workloads:
        for container in _containers(_pod_spec(doc)):
            resources = container["resources"]
            for name in ("cpu", "memory"):
                assert _quantity(resources["limits"][name]) >= _quantity(
                    resources["requests"][name]
                ), (
                    _name(doc),
                    container["name"],
                    name,
                )


# Claims: 8.6
@pytest.mark.parametrize("values_file", VALUES_FILES)
def test_every_platform_container_is_hardened(values_file: str) -> None:
    dev_datastores = {"dev-postgres", "dev-redis"}
    for doc in _platform_workloads(_docs(values_file)):
        if _name(doc) in dev_datastores:
            continue  # dev-only StatefulSets run the upstream datastore images
        for container in _containers(_pod_spec(doc)):
            sc = container["securityContext"]
            assert sc["runAsNonRoot"] is True, (_name(doc), container["name"])
            assert sc["readOnlyRootFilesystem"] is True
            assert sc["allowPrivilegeEscalation"] is False
            assert sc["capabilities"]["drop"] == ["ALL"]
            assert sc["seccompProfile"] == {"type": "RuntimeDefault"}


# Claims: 8.12
@pytest.mark.parametrize("values_file", VALUES_FILES)
def test_priority_class_is_above_lab_pods_and_assigned_to_api_worker_web(values_file: str) -> None:
    docs = _docs(values_file)
    values = _values(values_file)
    (priority,) = _of_kind(docs, "PriorityClass")
    assert priority["value"] == values["priorityClass"]["value"]
    assert priority["value"] > values["labs"]["podPriorityValue"]
    for doc in _of_kind(docs, "Deployment"):
        assert _pod_spec(doc)["priorityClassName"] == priority["metadata"]["name"], _name(doc)


# Claims: 8.8, 8.9, 21.1, 21.2
@pytest.mark.parametrize("values_file", VALUES_FILES)
def test_migration_hook_weights_and_orphan_creation_policy(values_file: str) -> None:
    docs = _docs(values_file)
    by_kind = {(d["kind"], _name(d)): d for d in docs}

    def hook(kind: str, name: str) -> dict[str, str]:
        return by_kind[(kind, name)]["metadata"]["annotations"]  # type: ignore[no-any-return]

    database_kind = "Secret" if values_file == "values-dev.yaml" else "ExternalSecret"
    for kind, name in (
        ("ServiceAccount", "migration"),
        (database_kind, "database"),
        ("ConfigMap", "migration"),
    ):
        annotations = hook(kind, name)
        assert annotations["helm.sh/hook"] == "pre-install,pre-upgrade", (kind, name)
        assert annotations["helm.sh/hook-weight"] == "-10"
        assert annotations["helm.sh/hook-delete-policy"] == "before-hook-creation"
    job = hook("Job", "migration")
    assert job["helm.sh/hook"] == "pre-install,pre-upgrade"
    assert job["helm.sh/hook-weight"] == "0"
    assert job["helm.sh/hook-delete-policy"] == "before-hook-creation"
    if database_kind == "ExternalSecret":
        assert (
            by_kind[("ExternalSecret", "database")]["spec"]["target"]["creationPolicy"] == "Orphan"
        )
        for other in _of_kind(docs, "ExternalSecret"):
            if _name(other) != "database":
                assert other["spec"]["target"]["creationPolicy"] != "Orphan"


# Claims: 14.4, 14.12
@pytest.mark.parametrize("values_file", VALUES_FILES)
def test_rendered_termination_grace_satisfies_property_34(values_file: str) -> None:
    docs = _docs(values_file)
    (api,) = [d for d in _of_kind(docs, "Deployment") if _name(d) == "api"]
    env = {e["name"]: e["value"] for e in _pod_spec(api)["containers"][0]["env"] if "value" in e}
    check_termination_grace(
        int(env["API_DRAIN_PERIOD_SECONDS"]),
        int(env["API_PRESTOP_DELAY_SECONDS"]),
        int(_pod_spec(api)["terminationGracePeriodSeconds"]),
    )
    (worker,) = [d for d in _of_kind(docs, "Deployment") if _name(d) == "worker"]
    assert _pod_spec(worker)["terminationGracePeriodSeconds"] >= 10


def _route_targets(doc: dict[str, Any]) -> set[str]:
    return {
        ref["name"].removeprefix(PREFIX)
        for rule in doc["spec"]["rules"]
        for ref in rule.get("backendRefs", [])
    }


# Claims: 9.1, 9.2
@pytest.mark.parametrize("values_file", VALUES_FILES)
def test_ingress_modes_render_exactly_one_routing_kind(values_file: str) -> None:
    docs = _docs(values_file, "ingress.mode=ingress-nginx")
    kinds = {d["kind"] for d in docs}
    assert "Ingress" in kinds and not kinds & {"Gateway", "HTTPRoute"}
    (ingress,) = _of_kind(docs, "Ingress")
    backends = {
        path["backend"]["service"]["name"].removeprefix(PREFIX)
        for rule in ingress["spec"]["rules"]
        for path in rule["http"]["paths"]
    }
    assert backends == {"web", "api"}

    docs = _docs(values_file, "ingress.mode=gateway-api")
    kinds = {d["kind"] for d in docs}
    assert "HTTPRoute" in kinds and "Ingress" not in kinds
    (route,) = _of_kind(docs, "HTTPRoute")
    assert route["spec"]["parentRefs"]  # the Gateway reference
    assert _route_targets(route) == {"web", "api"}


_SECRET_PATTERNS = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    re.compile(
        r"(?i)\b(?:password|passwd|secret|token|api[-_]?key)\b\s*[:=]\s*[\"']?[A-Za-z0-9/+_-]{12,}"
    ),
]


def _scan(text: str) -> list[str]:
    return [p.pattern for p in _SECRET_PATTERNS if p.search(text)]


# Claims: 10.1, 10.3
@pytest.mark.parametrize("values_file", VALUES_FILES)
def test_rendered_manifests_and_values_carry_no_secret_values(values_file: str) -> None:
    rendered, docs = _rendered(values_file)
    # Dev-only plain Secret placeholders are permitted (Req 10.9); nothing else may match.
    permitted = [d for d in docs if d["kind"] == "Secret"]
    if values_file != "values-dev.yaml":
        assert not permitted
    for secret in permitted:
        for value in secret.get("stringData", {}).values():
            credential_free_url = re.fullmatch(r"[a-z]+://[^@/\s]+(/\S*)?", value) is not None
            assert "placeholder" in value or credential_free_url, _name(secret)
    text = "\n".join(yaml.safe_dump(d) for d in docs if d["kind"] != "Secret")
    assert _scan(text) == []
    assert _scan((CHART_DIR / values_file).read_text(encoding="utf-8")) == []
    assert _scan((CHART_DIR / "values.yaml").read_text(encoding="utf-8")) == []
    assert rendered  # sanity: something was scanned


# Claims: 10.1
@pytest.mark.parametrize(
    "values_file", ["values-eks.yaml", "values-aks.yaml", "values-onprem.yaml"]
)
def test_external_secret_per_read_secret_in_each_production_target(values_file: str) -> None:
    names = {_name(d) for d in _of_kind(_docs(values_file), "ExternalSecret")}
    assert names == {"database", "redis", "session", "llm", "object-storage"}


# Claims: 7.5
@pytest.mark.parametrize("values_file", VALUES_FILES)
def test_helm_lint_reports_zero_errors_and_zero_warnings(values_file: str) -> None:
    done = subprocess.run(
        ["helm", "lint", "--strict", str(CHART_DIR), "-f", str(CHART_DIR / values_file), *_BASE],
        capture_output=True,
        timeout=180,
        check=False,
    )
    output = done.stdout.decode("utf-8", "replace") + done.stderr.decode("utf-8", "replace")
    assert done.returncode == 0, output
    assert "[ERROR]" not in output and "[WARNING]" not in output, output
    assert "0 chart(s) failed" in output


# Claims: 7.6
@pytest.mark.parametrize("values_file", VALUES_FILES)
def test_rendered_objects_pass_kubeconform(values_file: str) -> None:
    binary = shutil.which("kubeconform")
    if binary is None:
        pytest.skip("kubeconform is not installed")
    kube_version = re.sub(
        r"[^0-9.]", "", yaml.safe_load((CHART_DIR / "Chart.yaml").read_text("utf-8"))["kubeVersion"]
    )
    rendered, _ = _rendered(values_file, "ingress.mode=ingress-nginx", "imagePolicy.enabled=false")
    done = subprocess.run(
        [
            binary,
            "-strict",
            "-summary",
            "-ignore-missing-schemas",
            "-kubernetes-version",
            kube_version,
            "-",
        ],
        input=rendered,
        capture_output=True,
        timeout=180,
        check=False,
    )
    output = done.stdout.decode("utf-8", "replace") + done.stderr.decode("utf-8", "replace")
    assert done.returncode == 0, output
