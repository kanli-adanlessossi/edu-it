"""Ingress and Gateway API routing detail (kubernetes-deployment, Req 9.7, 9.10).

**Validates: Requirements 9.7, 9.10**

Renders the Chart with ``helm template``. Skipped when ``helm`` is not installed.
"""

# Claims: 9.7, 9.10

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
_BASE = [
    arg
    for name in ("web", "api", "worker", "migrations")
    for arg in ("--set", f"images.{name}.digest={_DIGEST}")
]
PREFIX = "t-edu-it-"

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm is not installed")


def _docs(values_file: str, *sets: str) -> list[dict[str, Any]]:
    extra = [arg for s in sets for arg in ("--set", s)]
    done = subprocess.run(
        [
            "helm", "template", "t", str(CHART_DIR),
            "-f", str(CHART_DIR / values_file), *_BASE, *extra,
        ],  # fmt: skip
        capture_output=True,
        timeout=180,
        check=False,
    )
    assert done.returncode == 0, done.stderr.decode("utf-8", "replace")
    return [d for d in yaml.safe_load_all(done.stdout) if d]


def _one(docs: list[dict[str, Any]], kind: str) -> dict[str, Any]:
    (doc,) = [d for d in docs if d["kind"] == kind]
    return doc


# Claims: 9.7
@pytest.mark.parametrize("values_file", VALUES_FILES)
def test_httproute_routes_the_terminal_websocket_path_to_api_and_outlasts_both_timeouts(
    values_file: str,
) -> None:
    docs = _docs(
        values_file,
        "ingress.mode=gateway-api",
        "ingress.websocket.readTimeoutSeconds=5400",
        "ingress.websocket.sendTimeoutSeconds=4800",
    )
    route = _one(docs, "HTTPRoute")
    (api_rule,) = [
        r
        for r in route["spec"]["rules"]
        if any(b["name"] == f"{PREFIX}api" for b in r["backendRefs"])
    ]
    assert {m["path"]["value"] for m in api_rule["matches"]} == {"/v1"}
    assert [b["name"] for b in api_rule["backendRefs"]] == [f"{PREFIX}api"]
    # The connection stays open for at least the larger of the two timeouts.
    assert api_rule["timeouts"]["request"] == "5400s"


# Claims: 9.10
@pytest.mark.parametrize("values_file", VALUES_FILES)
def test_ingress_exposes_fr_en_and_api_routes_on_the_single_configured_host(
    values_file: str,
) -> None:
    host = "learn.example.org"
    docs = _docs(values_file, "ingress.mode=ingress-nginx", f"ingress.host={host}")
    ingress = _one(docs, "Ingress")
    (rule,) = ingress["spec"]["rules"]
    assert rule["host"] == host
    routes = {
        p["path"]: p["backend"]["service"]["name"].removeprefix(PREFIX)
        for p in rule["http"]["paths"]
    }
    assert routes == {"/fr": "web", "/en": "web", "/v1": "api"}


# Claims: 9.10
@pytest.mark.parametrize("values_file", VALUES_FILES)
def test_httproute_exposes_fr_en_and_api_routes_on_the_single_configured_host(
    values_file: str,
) -> None:
    host = "learn.example.org"
    docs = _docs(values_file, "ingress.mode=gateway-api", f"ingress.host={host}")
    route = _one(docs, "HTTPRoute")
    assert route["spec"]["hostnames"] == [host]
    routes = {
        m["path"]["value"]: rule["backendRefs"][0]["name"].removeprefix(PREFIX)
        for rule in route["spec"]["rules"]
        for m in rule["matches"]
    }
    assert routes == {"/fr": "web", "/en": "web", "/v1": "api"}
