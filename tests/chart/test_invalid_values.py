"""Invalid-values tests of the Chart (kubernetes-deployment task 12.14).

One key is mutated at a time in a copy of a Values_File (missing key, wrong type, or a bad value);
``helm template`` must fail, the error must name the key, and no manifest may be rendered.
Complements ``test_validation.py`` (``--set`` overrides) with mutated values copies and with the
rules that live outside ``_validation.tpl``. Skipped when ``helm`` is not installed.
"""

from __future__ import annotations

import copy
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

CHART_DIR = Path(__file__).resolve().parents[2] / "deploy" / "helm" / "edu-it"
_DIGEST = "sha256:" + "a" * 64

requires_helm = pytest.mark.skipif(shutil.which("helm") is None, reason="helm is not installed")

_MISSING = object()


def _load(values_file: str) -> dict[str, Any]:
    data = yaml.safe_load((CHART_DIR / values_file).read_text(encoding="utf-8"))
    for name in ("web", "api", "worker", "migrations"):
        data["images"][name]["digest"] = _DIGEST
    return data


def _mutate(data: dict[str, Any], dotted: str, value: object) -> dict[str, Any]:
    out = copy.deepcopy(data)
    *parents, leaf = dotted.split(".")
    node = out
    for part in parents:
        node = node[part]
    if value is _MISSING:
        del node[leaf]
    else:
        node[leaf] = value
    return out


def _render(
    tmp_path: Path, data: dict[str, Any], chart: Path = CHART_DIR
) -> subprocess.CompletedProcess[str]:
    path = tmp_path / "mutated-values.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return subprocess.run(
        ["helm", "template", "t", str(chart), "-f", str(path)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def _render_without_key(tmp_path: Path, values_file: str, key: str) -> subprocess.CompletedProcess[str]:
    """Render with ``key`` absent from the effective values (chart defaults and Values_File)."""
    chart = tmp_path / "chart"
    shutil.copytree(CHART_DIR, chart)
    defaults = yaml.safe_load((chart / "values.yaml").read_text(encoding="utf-8"))
    *parents, leaf = key.split(".")
    node = defaults
    for part in parents:
        node = node[part]
    del node[leaf]
    (chart / "values.yaml").write_text(yaml.safe_dump(defaults), encoding="utf-8")
    return _render(tmp_path, _mutate(_load(values_file), key, _MISSING), chart)


def _assert_missing_rejected(done: subprocess.CompletedProcess[str], key: str) -> None:
    """The schema reports a missing property as the parent pointer plus the property name."""
    *parents, leaf = key.split(".")
    assert done.returncode != 0, f"expected a rejection for {key}"
    assert f"missing property '{leaf}'" in done.stderr or key in done.stderr, done.stderr
    assert not parents or "/" + "/".join(parents) in done.stderr or key in done.stderr, done.stderr
    assert "kind:" not in done.stdout


def _assert_rejected(done: subprocess.CompletedProcess[str], key: str) -> None:
    assert done.returncode != 0, f"expected a rejection for {key}"
    # The schema reports a JSON pointer (/api/replicas); the `fail` helpers report the dotted key.
    assert key in done.stderr or "/" + key.replace(".", "/") in done.stderr, done.stderr
    assert "kind:" not in done.stdout


# Claims: 7.7, 7.12, 8.3, 8.8, 9.3, 9.5, 10.4, 13.7, 14.4
@requires_helm
@pytest.mark.parametrize(
    "key",
    [
        "environment",
        "api.replicas",
        "ingress.mode",
        "ingress.tls.secretName",
        "ingress.websocket.readTimeoutSeconds",
        "api.terminationGracePeriodSeconds",
        "migration.backoffLimit",
        "migration.activeDeadlineSeconds",
        "observability.scrapeMode",
        "llm.region",
        "priorityClass.value",
        "images.api.digest",
    ],
)
def test_missing_key_is_rejected_and_named(tmp_path: Path, key: str) -> None:
    _assert_missing_rejected(_render_without_key(tmp_path, "values-eks.yaml", key), key)


# Claims: 7.7, 7.12, 8.3, 8.8, 9.3, 9.9, 13.7, 14.4, 21.4
@requires_helm
@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("api.replicas", "three"),
        ("web.replicas", 2.5),
        ("ingress.mode", 7),
        ("ingress.websocket.sendTimeoutSeconds", "long"),
        ("api.terminationGracePeriodSeconds", "soon"),
        ("migration.backoffLimit", "many"),
        ("migration.activeDeadlineSeconds", [30]),
        ("observability.scrapeMode", True),
        ("images.worker.digest", 12345),
        ("devDatastores.enabled", "yes-please"),
    ],
)
def test_wrong_type_is_rejected_and_named(tmp_path: Path, key: str, value: object) -> None:
    data = _mutate(_load("values-eks.yaml"), key, value)
    _assert_rejected(_render(tmp_path, data), key)


# Claims: 7.12, 21.4
@requires_helm
@pytest.mark.parametrize(
    "digest",
    ["sha256:" + "a" * 63, "sha256:" + "g" * 64, "sha1:" + "a" * 40, "a" * 64, "web:latest", ""],
)
def test_bad_digest_is_rejected_and_named(tmp_path: Path, digest: str) -> None:
    data = _mutate(_load("values-eks.yaml"), "images.web.digest", digest)
    _assert_rejected(_render(tmp_path, data), "images.web.digest")


# Claims: 11.14
@requires_helm
@pytest.mark.parametrize("values_file", ["values-eks.yaml", "values-aks.yaml", "values-onprem.yaml"])
def test_empty_kubernetes_api_cidrs_is_rejected_and_named(tmp_path: Path, values_file: str) -> None:
    data = _mutate(_load(values_file), "networkPolicy.kubernetesApi.cidrs", [])
    _assert_rejected(_render(tmp_path, data), "networkPolicy.kubernetesApi.cidrs")


# Claims: 11.14
@requires_helm
def test_missing_kubernetes_api_cidrs_is_rejected_and_named(tmp_path: Path) -> None:
    done = _render_without_key(tmp_path, "values-eks.yaml", "networkPolicy.kubernetesApi.cidrs")
    _assert_missing_rejected(done, "networkPolicy.kubernetesApi.cidrs")


# Claims: 8.12
@requires_helm
@pytest.mark.parametrize(
    ("platform_value", "lab_value"),
    [(0, 0), (100, 100), (99, 100), (-5, 0)],
)
def test_priority_class_value_not_above_lab_pods_is_rejected(
    tmp_path: Path, platform_value: int, lab_value: int
) -> None:
    data = _mutate(_load("values-eks.yaml"), "priorityClass.value", platform_value)
    data = _mutate(data, "labs.podPriorityValue", lab_value)
    _assert_rejected(_render(tmp_path, data), "priorityClass.value")


# Claims: 8.12
@requires_helm
def test_priority_class_value_above_lab_pods_is_accepted(tmp_path: Path) -> None:
    data = _mutate(_load("values-eks.yaml"), "priorityClass.value", 1)
    data = _mutate(data, "labs.podPriorityValue", 0)
    done = _render(tmp_path, data)
    assert done.returncode == 0, done.stderr


# Claims: 7.7, 16.9
@requires_helm
@pytest.mark.parametrize("values_file", ["values-eks.yaml", "values-aks.yaml", "values-onprem.yaml"])
def test_dev_datastores_in_production_are_rejected_and_named(tmp_path: Path, values_file: str) -> None:
    data = _mutate(_load(values_file), "environment", "production")
    data = _mutate(data, "devDatastores.enabled", True)
    _assert_rejected(_render(tmp_path, data), "devDatastores.enabled")


# Claims: 8.3, 8.8
@requires_helm
@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("migration.backoffLimit", 7),
        ("migration.backoffLimit", -1),
        ("migration.activeDeadlineSeconds", 29),
        ("migration.activeDeadlineSeconds", 3601),
        ("api.replicas", 0),
        ("worker.replicas", 101),
    ],
)
def test_out_of_range_bounds_are_rejected_and_named(tmp_path: Path, key: str, value: int) -> None:
    data = _mutate(_load("values-eks.yaml"), key, value)
    _assert_rejected(_render(tmp_path, data), key)


# Claims: 9.3, 9.5, 9.9, 10.6, 13.7, 14.4
@requires_helm
@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("ingress.mode", "traefik"),
        ("ingress.tls.secretName", ""),
        ("ingress.websocket.readTimeoutSeconds", 1),
        ("llm.region", "us-east-1"),
        ("observability.scrapeMode", "push"),
        ("api.terminationGracePeriodSeconds", 1),
    ],
)
@pytest.mark.parametrize("values_file", ["values-aks.yaml", "values-onprem.yaml"])
def test_rules_reject_on_every_production_target(
    tmp_path: Path, values_file: str, key: str, value: object
) -> None:
    data = _mutate(_load(values_file), key, value)
    _assert_rejected(_render(tmp_path, data), key)
