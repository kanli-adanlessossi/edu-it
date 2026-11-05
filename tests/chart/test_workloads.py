"""Api, Worker and Web Deployments (kubernetes-deployment Req 8.1, 8.5, 8.6, 8.7, 8.12, 14.4, 14.12,
23.1, 25.6; task 12.1).

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
_DIGEST = "sha256:" + "a" * 64
_BASE = [
    arg
    for name in ("web", "api", "worker", "migrations")
    for arg in ("--set", f"images.{name}.digest={_DIGEST}")
] + ["--set", "networkPolicy.kubernetesApi.cidrs={10.0.0.1/32}"]
VALUES_FILES = ["values-dev.yaml", "values-eks.yaml", "values-aks.yaml", "values-onprem.yaml"]
COMPONENTS = ["api", "worker", "web"]

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm is not installed")


def _helm(values_file: str, *sets: str) -> subprocess.CompletedProcess[bytes]:
    extra = [arg for s in sets for arg in ("--set", s)]
    return subprocess.run(
        ["helm", "template", "t", str(CHART_DIR), "-f", str(CHART_DIR / values_file), *_BASE, *extra],
        capture_output=True,
        timeout=120,
        check=False,
    )


def _render(values_file: str = "values-eks.yaml", *sets: str) -> list[dict[str, Any]]:
    done = _helm(values_file, *sets)
    assert done.returncode == 0, done.stderr.decode("utf-8", "replace")
    return [doc for doc in yaml.safe_load_all(done.stdout) if doc]


def _deployment(docs: list[dict[str, Any]], component: str) -> dict[str, Any]:
    for doc in docs:
        if doc["kind"] == "Deployment" and doc["metadata"]["name"].endswith(f"-{component}"):
            return doc
    raise AssertionError(f"no Deployment named *-{component}")


def _container(docs: list[dict[str, Any]], component: str) -> dict[str, Any]:
    return _deployment(docs, component)["spec"]["template"]["spec"]["containers"][0]  # type: ignore[no-any-return]


def _env(container: dict[str, Any]) -> dict[str, str]:
    return {e["name"]: e["value"] for e in container["env"] if "value" in e}


# Claims: 8.1
@pytest.mark.parametrize("values_file", VALUES_FILES)
@pytest.mark.parametrize("component", COMPONENTS)
def test_every_workload_has_three_probes_within_the_bounds(values_file: str, component: str) -> None:
    container = _container(_render(values_file), component)
    for key in ("startupProbe", "livenessProbe", "readinessProbe"):
        probe = container[key]
        assert probe["httpGet"]["path"].startswith("/")
        assert 1 <= probe["periodSeconds"] <= 60
        assert 1 <= probe["timeoutSeconds"] <= 30
        assert probe["timeoutSeconds"] <= probe["periodSeconds"]
        assert 1 <= probe["failureThreshold"] <= 30


# Claims: 8.1
def test_probe_paths_match_what_the_apps_serve() -> None:
    docs = _render()
    for component in ("api", "worker"):
        container = _container(docs, component)
        assert container["livenessProbe"]["httpGet"]["path"] == "/health"
        assert container["startupProbe"]["httpGet"]["path"] == "/health"
        assert container["readinessProbe"]["httpGet"]["path"] == "/ready"


# Claims: 8.1
@pytest.mark.parametrize(
    "setting",
    [
        "api.probes.liveness.timeoutSeconds=11",  # above the 10 s period
        "worker.probes.readiness.periodSeconds=61",  # above the 60 s bound
        "web.probes.startup.failureThreshold=31",  # above the 30 bound
    ],
)
def test_probe_values_outside_the_bounds_fail_rendering(setting: str) -> None:
    done = _helm("values-eks.yaml", setting)
    assert done.returncode != 0
    assert done.stdout.strip() == b""


# Claims: 8.5, 8.6
@pytest.mark.parametrize("component", COMPONENTS)
def test_resources_and_security_contexts(component: str) -> None:
    spec = _deployment(_render(), component)["spec"]["template"]["spec"]
    assert spec["securityContext"]["runAsNonRoot"] is True
    assert spec["securityContext"]["seccompProfile"] == {"type": "RuntimeDefault"}
    for container in spec["containers"]:
        resources = container["resources"]
        for name in ("cpu", "memory"):
            assert name in resources["requests"] and name in resources["limits"]
        sc = container["securityContext"]
        assert sc["allowPrivilegeEscalation"] is False
        assert sc["readOnlyRootFilesystem"] is True
        assert sc["runAsNonRoot"] is True
        assert sc["capabilities"] == {"drop": ["ALL"]}
        assert sc["seccompProfile"] == {"type": "RuntimeDefault"}


# Claims: 8.7
def test_api_spreads_across_nodes_with_max_skew_one() -> None:
    (constraint,) = _deployment(_render(), "api")["spec"]["template"]["spec"]["topologySpreadConstraints"]
    assert constraint["maxSkew"] == 1
    assert constraint["topologyKey"] == "kubernetes.io/hostname"
    assert constraint["labelSelector"]["matchLabels"]["app.kubernetes.io/component"] == "api"


# Claims: 25.6
@pytest.mark.parametrize("component", COMPONENTS)
def test_rolling_update_keeps_every_replica_ready(component: str) -> None:
    strategy = _deployment(_render(), component)["spec"]["strategy"]
    assert strategy == {"type": "RollingUpdate", "rollingUpdate": {"maxUnavailable": 0, "maxSurge": 1}}


# Claims: 25.6
def test_api_defaults_to_two_replicas_without_an_hpa() -> None:
    assert _deployment(_render("values-eks.yaml", "api.hpa.enabled=false"), "api")["spec"]["replicas"] == 2


# Claims: 8.2 (carry-over from 12.2: the HPA owns the replica count)
@pytest.mark.parametrize("component", COMPONENTS)
def test_replicas_omitted_while_the_hpa_is_enabled(component: str) -> None:
    assert "replicas" not in _deployment(_render(), component)["spec"]
    docs = _render("values-eks.yaml", f"{component}.hpa.enabled=false")
    assert _deployment(docs, component)["spec"]["replicas"] == 2


# Claims: 8.12
@pytest.mark.parametrize("component", COMPONENTS)
def test_platform_priority_class_on_every_workload(component: str) -> None:
    docs = _render()
    spec = _deployment(docs, component)["spec"]["template"]["spec"]
    classes = {d["metadata"]["name"] for d in docs if d["kind"] == "PriorityClass"}
    assert spec["priorityClassName"] in classes


# Claims: 14.12, 14.4
def test_api_drains_through_the_admin_port_before_sigterm() -> None:
    docs = _render()
    spec = _deployment(docs, "api")["spec"]["template"]["spec"]
    container = spec["containers"][0]
    ports = {p["name"]: p["containerPort"] for p in container["ports"]}
    hook = container["lifecycle"]["preStop"]["httpGet"]
    assert hook["path"] == "/internal/drain"
    assert hook["port"] == "admin"
    assert ports["admin"] == 8001 and ports["metrics"] == 9090
    env = _env(container)
    assert env["API_ADMIN_PORT"] == "8001"
    assert env["API_PORT"] == "8000"
    assert env["API_DRAIN_PERIOD_SECONDS"] == "30"
    assert env["API_PRESTOP_DELAY_SECONDS"] == "5"
    assert env["API_METRICS_PORT"] == "9090" and env["API_METRICS_HOST"] == "0.0.0.0"
    assert spec["terminationGracePeriodSeconds"] >= (
        int(env["API_PRESTOP_DELAY_SECONDS"]) + int(env["API_DRAIN_PERIOD_SECONDS"]) + 10
    )


# Claims: 14.4
def test_api_grace_period_shorter_than_prestop_plus_drain_fails_rendering() -> None:
    done = _helm("values-eks.yaml", "api.terminationGracePeriodSeconds=40")
    assert done.returncode != 0
    assert b"api.terminationGracePeriodSeconds" in done.stderr


# Claims: 14.10, 23.1
def test_worker_env_health_surface_and_required_secret_mounts() -> None:
    spec = _deployment(_render(), "worker")["spec"]["template"]["spec"]
    container = spec["containers"][0]
    env = _env(container)
    assert env["WORKER_HEALTH_HOST"] == "0.0.0.0"
    assert env["WORKER_HEALTH_PORT"] == "8081"
    assert env["WORKER_METRICS_HOST"] == "0.0.0.0" and env["WORKER_METRICS_PORT"] == "9090"
    assert env["WORKER_TERMINATION_GRACE_SECONDS"] == str(spec["terminationGracePeriodSeconds"])
    ports = {p["name"]: p["containerPort"] for p in container["ports"]}
    assert ports == {"health": 8081, "metrics": 9090}
    assert container["readinessProbe"]["httpGet"]["port"] == "health"


# Claims: 23.1
@pytest.mark.parametrize("component", ["api", "worker"])
def test_secret_volumes_are_optional_and_mounted_where_secretsource_reads(component: str) -> None:
    spec = _deployment(_render(), component)["spec"]["template"]["spec"]
    volumes = {v["name"]: v for v in spec["volumes"] if "secret" in v}
    mounts = {m["name"]: m for m in spec["containers"][0]["volumeMounts"]}
    for secret in ("database", "redis"):  # REQUIRED_SECRET_KEYS: database/url, redis/url
        assert volumes[f"secret-{secret}"]["secret"]["optional"] is True
        assert mounts[f"secret-{secret}"]["mountPath"] == f"/var/run/secrets/edu-it/{secret}"
        assert mounts[f"secret-{secret}"]["readOnly"] is True
    assert all(v["secret"]["optional"] is True for v in volumes.values())


# Claims: 13.1, 13.5 (carry-over from 12.7)
def test_scrape_annotations_and_collector_env_are_kept() -> None:
    docs = _render("values-eks.yaml", "observability.scrapeMode=annotations")
    for component in ("api", "worker"):
        deployment = _deployment(docs, component)
        annotations = deployment["spec"]["template"]["metadata"]["annotations"]
        assert annotations["prometheus.io/scrape"] == "true"
        assert annotations["prometheus.io/port"] == "9090"
        assert _env(_container(docs, component))["OTEL_EXPORTER_OTLP_ENDPOINT"]
    default = _render()
    assert "annotations" not in _deployment(default, "api")["spec"]["template"]["metadata"]


# Claims: 8.1 (carry-over: ingress, gateway and network policies refer to the web Service)
def test_web_service_exposes_web_port_and_selects_web_pods() -> None:
    docs = _render()
    (service,) = [
        d for d in docs if d["kind"] == "Service" and d["metadata"]["name"].endswith("-web")
    ]
    assert service["spec"]["ports"][0]["port"] == 3000
    assert service["spec"]["selector"]["app.kubernetes.io/component"] == "web"
    assert _container(docs, "web")["ports"][0]["containerPort"] == 3000
