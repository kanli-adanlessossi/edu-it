"""Secrets and per-Target configuration (kubernetes-deployment Req 10.1, 10.2, 10.5, 10.7, 10.8, 10.9, 23.5).

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
PROD_VALUES = ["values-eks.yaml", "values-aks.yaml", "values-onprem.yaml"]
# Secrets the Api, Worker or Migration_Job read (edu-it.secretCatalog).
READ_SECRETS = {"database", "redis", "session", "llm", "object-storage"}
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


def _docs(values_file: str, *sets: str) -> list[dict[str, Any]]:
    done = _helm(values_file, *sets)
    assert done.returncode == 0, done.stderr.decode("utf-8", "replace")
    return [d for d in yaml.safe_load_all(done.stdout) if d]


def _suffix(doc: dict[str, Any]) -> str:
    return doc["metadata"]["name"].removeprefix("t-edu-it-")


# Claims: 10.1, 10.2, 10.5, 10.7
@pytest.mark.parametrize("values_file", PROD_VALUES)
def test_one_external_secret_per_read_secret_and_no_plain_secret(values_file: str) -> None:
    docs = _docs(values_file)
    external = [d for d in docs if d["kind"] == "ExternalSecret"]
    assert {_suffix(d) for d in external} == READ_SECRETS
    assert not [d for d in docs if d["kind"] == "Secret"]
    values = yaml.safe_load((CHART_DIR / values_file).read_text(encoding="utf-8"))["externalSecrets"]
    remote_keys = set(values["remoteKeys"].values())
    for d in external:
        assert d["spec"]["secretStoreRef"] == {"name": values["storeName"], "kind": values["storeKind"]}
        assert {item["remoteRef"]["key"] for item in d["spec"]["data"]} <= remote_keys
    # Every secret volume the workloads mount has an ExternalSecret behind it.
    api = next(d for d in docs if d["kind"] == "Deployment" and _suffix(d) == "api")
    mounted = {v["secret"]["secretName"].removeprefix("t-edu-it-") for v in api["spec"]["template"]["spec"]["volumes"] if "secret" in v}
    assert mounted == READ_SECRETS


# Claims: 10.9
def test_dev_target_renders_plain_secrets_only() -> None:
    docs = _docs("values-dev.yaml")
    assert not [d for d in docs if d["kind"] == "ExternalSecret"]
    assert READ_SECRETS <= {_suffix(d) for d in docs if d["kind"] == "Secret"}


# Claims: 10.7
def test_remote_keys_and_store_come_from_values() -> None:
    docs = _docs(
        "values-eks.yaml",
        "externalSecrets.storeName=other-store",
        "externalSecrets.storeKind=SecretStore",
        "externalSecrets.remoteKeys.session=custom/session",
    )
    session = next(d for d in docs if d["kind"] == "ExternalSecret" and _suffix(d) == "session")
    assert session["spec"]["secretStoreRef"] == {"name": "other-store", "kind": "SecretStore"}
    assert session["spec"]["data"][0]["remoteRef"]["key"] == "custom/session"


# Claims: 10.7
def test_empty_remote_key_fails_rendering_and_names_the_key() -> None:
    done = _helm("values-eks.yaml", "externalSecrets.remoteKeys.llm=")
    assert done.returncode != 0
    assert "externalSecrets.remoteKeys.llm" in done.stderr.decode("utf-8", "replace")


# Claims: 10.8
@pytest.mark.parametrize(
    ("values_file", "annotation"),
    [("values-eks.yaml", "eks.amazonaws.com/role-arn"), ("values-aks.yaml", "azure.workload.identity/client-id")],
)
def test_workload_identity_annotations_on_api_and_worker_service_accounts(values_file: str, annotation: str) -> None:
    accounts = {_suffix(d): d for d in _docs(values_file) if d["kind"] == "ServiceAccount"}
    for name in ("api", "worker"):
        assert annotation in accounts[name]["metadata"]["annotations"]


# Claims: 10.8
def test_onprem_service_accounts_carry_no_workload_identity() -> None:
    accounts = [d for d in _docs("values-onprem.yaml") if d["kind"] == "ServiceAccount"]
    for d in accounts:
        assert not {k for k in d["metadata"].get("annotations", {}) if not k.startswith("helm.sh/")}
