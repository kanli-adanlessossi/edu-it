"""HorizontalPodAutoscalers and PodDisruptionBudgets (kubernetes-deployment Req 8.2, 8.4, 8.10,
8.11, 19.6, 20.8).

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


def _render(values_file: str = "values-eks.yaml", *sets: str) -> list[dict[str, Any]]:
    done = _helm(values_file, *sets)
    assert done.returncode == 0, done.stderr.decode("utf-8", "replace")
    return [doc for doc in yaml.safe_load_all(done.stdout) if doc]


def _of(docs: list[dict[str, Any]], kind: str) -> dict[str, dict[str, Any]]:
    return {d["metadata"]["name"].rsplit("-", 1)[-1]: d for d in docs if d["kind"] == kind}


# Claims: 8.2, 8.11
def test_cpu_hpa_for_api_and_worker_without_metrics_adapter() -> None:
    hpas = _of(_render(), "HorizontalPodAutoscaler")
    for component in ("api", "worker"):
        spec = hpas[component]["spec"]
        assert spec["scaleTargetRef"]["name"].endswith(f"-{component}")
        (metric,) = spec["metrics"]
        assert metric["type"] == "Resource" and metric["resource"]["name"] == "cpu"
        assert 1 <= metric["resource"]["target"]["averageUtilization"] <= 100


# Claims: 8.10
def test_worker_hpa_scales_on_queue_depth_when_metrics_adapter_enabled() -> None:
    hpas = _of(_render("values-eks.yaml", "metricsAdapter.enabled=true"), "HorizontalPodAutoscaler")
    (worker_metric,) = hpas["worker"]["spec"]["metrics"]
    assert worker_metric["type"] == "External"
    assert worker_metric["external"]["target"] == {"type": "AverageValue", "averageValue": "10"}
    (api_metric,) = hpas["api"]["spec"]["metrics"]
    assert api_metric["type"] == "Resource"  # the Api always scales on CPU


# Claims: 8.10, 8.11
def test_queue_depth_metric_without_adapter_fails_rendering() -> None:
    done = _helm("values-eks.yaml", "worker.hpa.metric=queueDepth")
    assert done.returncode != 0
    assert "metricsAdapter.enabled" in done.stderr.decode("utf-8", "replace")


# Claims: 8.4
def test_api_and_worker_pdbs_select_their_pods() -> None:
    pdbs = _of(_render(), "PodDisruptionBudget")
    for component in ("api", "worker"):
        pdb = pdbs[component]
        assert pdb["spec"]["minAvailable"] >= 1
        assert pdb["spec"]["selector"]["matchLabels"]["app.kubernetes.io/component"] == component
        assert pdb["metadata"]["namespace"] == "platform"


# Claims: 19.6, 20.8
def test_lab_drain_protection_pdb_blocks_all_voluntary_disruption() -> None:
    pdb = _of(_render(), "PodDisruptionBudget")["protection"]
    assert pdb["metadata"]["namespace"] == "labs"
    assert pdb["spec"]["maxUnavailable"] == 0
    assert pdb["spec"]["selector"]["matchLabels"] == {"edu-it.managed": "1"}


# Claims: 19.6
def test_lab_drain_protection_pdb_absent_when_disabled() -> None:
    docs = _render("values-eks.yaml", "labs.drainProtection.enabled=false")
    assert "protection" not in _of(docs, "PodDisruptionBudget")


# Claims: 8.2, 8.4
def test_disabled_hpa_and_pdb_render_nothing() -> None:
    docs = _render(
        "values-eks.yaml",
        "api.hpa.enabled=false",
        "worker.hpa.enabled=false",
        "api.pdb.enabled=false",
        "worker.pdb.enabled=false",
    )
    assert not _of(docs, "HorizontalPodAutoscaler")
    assert set(_of(docs, "PodDisruptionBudget")) == {"protection"}
