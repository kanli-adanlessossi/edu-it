"""Api and Worker get a projected, short-lived ServiceAccount token (kubernetes-deployment Req 4.6,
4.7). The ServiceAccounts set ``automountServiceAccountToken: false``; the two workloads that call
the Kubernetes API project their own bound token instead of the default-mounted one.

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
TOKEN_DIR = "/var/run/secrets/kubernetes.io/serviceaccount"
#: Smallest ``expirationSeconds`` the API server accepts for a projected token.
MIN_EXPIRATION_SECONDS = 600

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm is not installed")


def _render() -> list[dict[str, Any]]:
    done = subprocess.run(
        ["helm", "template", "t", str(CHART_DIR), "-f", str(CHART_DIR / "values-dev.yaml")],
        capture_output=True,
        timeout=120,
        check=False,
    )
    assert done.returncode == 0, done.stderr.decode("utf-8", "replace")
    return [doc for doc in yaml.safe_load_all(done.stdout) if doc]


def _pod_spec(docs: list[dict[str, Any]], kind: str, suffix: str) -> dict[str, Any]:
    for doc in docs:
        if doc["kind"] == kind and doc["metadata"]["name"].endswith(suffix):
            return doc["spec"]["template"]["spec"]  # type: ignore[no-any-return]
    raise AssertionError(f"no {kind} named *{suffix}")


# Claims: 4.6, 4.7
@pytest.mark.parametrize("component", ["api", "worker"])
def test_workloads_that_call_the_api_project_a_short_lived_token(component: str) -> None:
    spec = _pod_spec(_render(), "Deployment", f"-{component}")
    # The pod spec opts in (the ServiceAccount stays false); the projected token is what the client reads.
    assert spec["automountServiceAccountToken"] is True

    projected = next(v for v in spec["volumes"] if v["name"] == "kube-api-access")["projected"]
    token = next(
        s["serviceAccountToken"] for s in projected["sources"] if "serviceAccountToken" in s
    )
    assert token["path"] == "token"
    assert token["expirationSeconds"] == MIN_EXPIRATION_SECONDS
    assert "audience" not in token  # the API server audience: not replayable against other services
    assert {"configMap", "downwardAPI"} <= {k for s in projected["sources"] for k in s}

    mount = next(m for m in spec["containers"][0]["volumeMounts"] if m["name"] == "kube-api-access")
    assert (mount["mountPath"], mount["readOnly"]) == (TOKEN_DIR, True)


# Claims: 4.6
def test_every_service_account_web_and_migration_pod_spec_mounts_no_token() -> None:
    docs = _render()
    for doc in docs:
        if doc["kind"] == "ServiceAccount":
            assert doc["automountServiceAccountToken"] is False, doc["metadata"]["name"]
    web = _pod_spec(docs, "Deployment", "-web")  # the Web calls no Kubernetes API
    assert web["automountServiceAccountToken"] is False
    migration = [d for d in docs if d["kind"] == "Job"]
    for job in migration:
        assert job["spec"]["template"]["spec"]["automountServiceAccountToken"] is False
