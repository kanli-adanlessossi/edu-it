"""Cross-key validation rules of the Chart (kubernetes-deployment task 12.11).

Every rejected Values_File must fail ``helm template`` with a message that names the offending
key and renders no manifest. Skipped when ``helm`` is not installed.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
from check_llm_region import EU_LLM_REGIONS

CHART_DIR = Path(__file__).resolve().parents[2] / "deploy" / "helm" / "edu-it"
VALUES_FILES = ["values-dev.yaml", "values-eks.yaml", "values-aks.yaml", "values-onprem.yaml"]
_DIGEST = "sha256:" + "a" * 64
_IMAGES = [
    arg
    for name in ("web", "api", "worker", "migrations")
    for arg in ("--set", f"images.{name}.digest={_DIGEST}")
]

requires_helm = pytest.mark.skipif(shutil.which("helm") is None, reason="helm is not installed")


def _helm(values_file: str, *sets: str) -> subprocess.CompletedProcess[str]:
    extra = [arg for s in sets for arg in ("--set", s)]
    return subprocess.run(
        ["helm", "template", "t", str(CHART_DIR), "-f", str(CHART_DIR / values_file), *_IMAGES, *extra],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


@requires_helm
@pytest.mark.parametrize("values_file", VALUES_FILES)
def test_values_files_pass_every_rule(values_file: str) -> None:
    done = _helm(values_file)
    assert done.returncode == 0, done.stderr


# (Values_File, --set overrides, text the error must contain)
_REJECTED = [
    ("values-eks.yaml", ["api.replicas=0"], "api.replicas"),
    ("values-eks.yaml", ["web.replicas=101"], "web.replicas"),
    ("values-eks.yaml", ["ingress.mode=traefik"], "ingress.mode"),
    ("values-eks.yaml", ["ingress.tls.secretName="], "ingress.tls.secretName"),
    ("values-eks.yaml", ["ingress.mode=gateway-api", "ingress.tls.secretName="], "ingress.tls.secretName"),
    ("values-eks.yaml", ["ingress.websocket.readTimeoutSeconds=3599"], "ingress.websocket.readTimeoutSeconds"),
    ("values-eks.yaml", ["ingress.websocket.sendTimeoutSeconds=60"], "ingress.websocket.sendTimeoutSeconds"),
    ("values-eks.yaml", ["api.terminationGracePeriodSeconds=44"], "api.terminationGracePeriodSeconds"),
    ("values-eks.yaml", ["worker.terminationGracePeriodSeconds=9"], "worker.terminationGracePeriodSeconds"),
    ("values-eks.yaml", ["migration.backoffLimit=7"], "migration.backoffLimit"),
    ("values-eks.yaml", ["migration.activeDeadlineSeconds=29"], "migration.activeDeadlineSeconds"),
    ("values-eks.yaml", ["migration.activeDeadlineSeconds=3601"], "migration.activeDeadlineSeconds"),
    ("values-eks.yaml", ["externalSecrets.storeName="], "externalSecrets.storeName"),
    ("values-eks.yaml", ["externalSecrets.remoteKeys.llm="], "externalSecrets.remoteKeys.llm"),
    ("values-eks.yaml", ["externalSecrets.remoteKeys.database="], "externalSecrets.remoteKeys.database"),
    ("values-eks.yaml", ["llm.region=us-east-1"], "us-east-1"),
    ("values-eks.yaml", ["observability.scrapeMode=push"], "observability.scrapeMode"),
    ("values-eks.yaml", ["api.probes.readiness.timeoutSeconds=9"], "api.probes.readiness.timeoutSeconds"),
    ("values-eks.yaml", ["worker.probes.liveness.periodSeconds=61"], "worker.probes.liveness.periodSeconds"),
    ("values-eks.yaml", ["images.api.digest=latest"], "images.api.digest"),
    ("values-eks.yaml", ["images.migrations.digest="], "images.migrations.digest"),
]


# Claims: 7.7, 7.8, 8.3, 8.8, 9.3, 9.5, 9.9, 10.4, 10.6, 11.14, 13.7, 14.4, 21.4
@requires_helm
@pytest.mark.parametrize(("values_file", "sets", "expected"), _REJECTED, ids=lambda v: str(v))
def test_invalid_values_name_the_key_and_render_nothing(
    values_file: str, sets: list[str], expected: str
) -> None:
    done = _helm(values_file, *sets)
    assert done.returncode != 0
    # The schema reports a JSON pointer (/api/replicas); the `fail` helpers report the dotted key.
    assert expected in done.stderr or "/" + expected.replace(".", "/") in done.stderr
    assert "kind:" not in done.stdout


@requires_helm
def test_dev_target_accepts_a_non_eu_llm_region() -> None:
    assert _helm("values-dev.yaml", "llm.region=us-east-1").returncode == 0


def test_template_region_list_matches_the_tool() -> None:
    text = (CHART_DIR / "templates" / "_validation.tpl").read_text(encoding="utf-8")
    match = re.search(r'\$eu := list ((?:"[a-z0-9-]+" ?)+)', text)
    assert match is not None
    assert set(re.findall(r'"([a-z0-9-]+)"', match.group(1))) == set(EU_LLM_REGIONS)
