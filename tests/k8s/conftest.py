"""Fixtures of the kind-backed Kubernetes tests (task 6.22).

Tests marked ``kind`` need a reachable kind cluster (the ``edu-it`` cluster of task 6.1, context
``kind-edu-it``, or the one named by ``EDU_IT_KIND_CONTEXT``) and ``kubectl`` on PATH. When there
is none they are skipped with the reason, so the default run is unaffected. The ``labs`` namespace
is created here with the restricted Pod Security labels; the Helm chart is not needed.
"""

from __future__ import annotations

import functools
import json
import os
import shutil
import subprocess

import pytest
from edu_it_infrastructure.sandbox.k8s_api import K8sApiPort

from tests.k8s.kubectl_api import KubectlK8sApi

KIND_CONTEXT_ENV = "EDU_IT_KIND_CONTEXT"
DEFAULT_KIND_CONTEXT = "kind-edu-it"
LABS_NAMESPACE = "labs"
PSA_LABELS = {
    "pod-security.kubernetes.io/enforce": "restricted",
    "pod-security.kubernetes.io/enforce-version": "latest",
}


def kind_context_name() -> str:
    return os.environ.get(KIND_CONTEXT_ENV, DEFAULT_KIND_CONTEXT)


@functools.cache
def kind_unavailable_reason() -> str | None:
    """Why no kind cluster can be used, or ``None`` when one answers."""

    if shutil.which("kubectl") is None:
        return "kubectl is not on PATH"
    context = kind_context_name()
    try:
        done = subprocess.run(  # noqa: S603 - fixed argv, no shell
            ["kubectl", "--context", context, "--request-timeout=5s", "get", "--raw", "/readyz"],
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"kubectl could not reach {context}: {exc}"
    if done.returncode != 0:
        detail = done.stderr.decode("utf-8", "replace").strip().splitlines()[:1]
        return f"no reachable kind cluster for context {context} ({''.join(detail)})"
    return None


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "kind: needs a reachable kind cluster (skipped otherwise)")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    kind_items = [item for item in items if item.get_closest_marker("kind")]
    if not kind_items:
        return
    reason = kind_unavailable_reason()
    if reason is None:
        return
    skip = pytest.mark.skip(reason=f"kind cluster not available: {reason}")
    for item in kind_items:
        item.add_marker(skip)


@pytest.fixture(scope="session")
def kind_context() -> str:
    reason = kind_unavailable_reason()
    if reason is not None:
        pytest.skip(f"kind cluster not available: {reason}")
    return kind_context_name()


@pytest.fixture(scope="session")
def kind_labs_namespace(kind_context: str) -> str:
    """The ``labs`` namespace with restricted PSA labels (idempotent; never deleted)."""

    manifests = {
        "apiVersion": "v1",
        "kind": "List",
        "items": [
            {
                "apiVersion": "v1",
                "kind": "Namespace",
                "metadata": {"name": LABS_NAMESPACE, "labels": PSA_LABELS},
            },
            labs_default_deny_policy(),
        ],
    }
    done = subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["kubectl", "--context", kind_context, "apply", "-f", "-"],
        input=json.dumps(manifests).encode(),
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert done.returncode == 0, done.stderr.decode("utf-8", "replace")
    return LABS_NAMESPACE


def labs_default_deny_policy() -> dict:
    """The default-deny NetworkPolicy of the Chart (``networkpolicy-labs.yaml``, Req 3.1).

    It selects every pod of ``labs`` and denies all ingress and egress. The harness cluster runs
    Calico, which enforces it, so the outbound-connect tests measure the real policy and not an
    open network. Exec, copy and the terminal go through the API server and are not affected.
    """

    return {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": {"name": "labs-default-deny", "namespace": LABS_NAMESPACE},
        "spec": {"podSelector": {}, "policyTypes": ["Ingress", "Egress"]},
    }


@pytest.fixture(scope="session")
def kind_api(kind_context: str, kind_labs_namespace: str) -> KubectlK8sApi:
    return KubectlK8sApi(kind_context)


@pytest.fixture(scope="session")
def kind_client_api(kind_context: str, kind_labs_namespace: str) -> K8sApiPort:
    """The production adapter (official Kubernetes client) bound to the kind kubeconfig context."""

    from edu_it_infrastructure.sandbox.k8s_client import build_k8s_api_from_kubeconfig

    return build_k8s_api_from_kubeconfig(kind_context)
