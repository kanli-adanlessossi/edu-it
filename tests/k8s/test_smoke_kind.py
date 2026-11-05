"""Chart smoke test on kind (kubernetes-deployment task 6.14).

Installs the Chart with ``values-dev.yaml`` on the kind cluster of ``tools/kind_harness.py``,
passing the Kubernetes API address that the harness prints through
``--set networkPolicy.kubernetesApi.cidrs=...`` (Req 11.13), runs ``helm test`` (the Api and
Worker readiness endpoints, Req 15.4), then provisions and destroys one Lab_Pod through
``build_sandbox_provider`` and asserts that zero Lab_Pods remain (Req 1.4, 1.5, 6.3).

Image digests: the images ``localhost:5001/edu-it-{api,worker,migrations}`` must already be in the
harness registry. Their ``sha256:...`` digests come from ``EDU_IT_KIND_DIGEST_API``,
``EDU_IT_KIND_DIGEST_WORKER`` and ``EDU_IT_KIND_DIGEST_MIGRATIONS`` (the CI kind job exports them;
locally use ``docker push`` and read the printed digest). Without them, or without ``helm``, the
test is skipped; without a cluster the ``kind`` marker skips it (conftest).

First-install bootstrap (known issue of task 6.13, decision recorded in
``docs/adr/0010-helm-chart-conventions.md``): the pre-install migration hook runs before the
chart's non-hook resources exist, so on a first ``helm install`` it cannot reach PostgreSQL.
Until task 12.3 turns the bootstrap resources into hooks, this test therefore:

1. pre-creates the ``platform`` and ``labs`` namespaces with the restricted Pod Security labels
   (and installs with ``namespaces.manage=operator-managed``),
2. runs ``helm install --no-hooks`` so the dev PostgreSQL and Redis StatefulSets exist, and waits
   for them to be ready,
3. runs ``helm upgrade --wait --timeout 600s`` so the pre-upgrade migration hook runs against a
   ready database and the Api and Worker become ready.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.k8s.conftest import LABS_NAMESPACE, PSA_LABELS, kind_context_name
from tests.k8s.kind_suite_api import kind_api

REPO_ROOT = Path(__file__).resolve().parents[2]
CHART = REPO_ROOT / "deploy" / "helm" / "edu-it"
HARNESS = REPO_ROOT / "tools" / "kind_harness.py"
RELEASE = "edu-it"
PLATFORM_NAMESPACE = "platform"
WAIT_TIMEOUT = "600s"
HELM_TEST_TIMEOUT = "300s"
DIGEST_ENV = {
    "api": "EDU_IT_KIND_DIGEST_API",
    "worker": "EDU_IT_KIND_DIGEST_WORKER",
    "migrations": "EDU_IT_KIND_DIGEST_MIGRATIONS",
}


def _run(argv: list[str], *, timeout: int = 900, input_text: str | None = None) -> str:
    done = subprocess.run(  # noqa: S603 - fixed argv, no shell
        argv, input=input_text, capture_output=True, text=True, timeout=timeout, check=False
    )
    assert done.returncode == 0, (
        f"{' '.join(argv)} failed ({done.returncode}):\n{done.stdout[-3000:]}\n{done.stderr[-3000:]}"
    )
    return done.stdout


def _kubectl(context: str, *args: str, input_text: str | None = None) -> str:
    return _run(["kubectl", "--context", context, *args], input_text=input_text)


def _namespace_manifest(name: str) -> str:
    return json.dumps(
        {
            "apiVersion": "v1",
            "kind": "Namespace",
            "metadata": {"name": name, "labels": PSA_LABELS},
        }
    )


def _api_cidr() -> str:
    """The ``<ip>/32`` printed by ``tools/kind_harness.py api-address``."""

    out = _run([sys.executable, str(HARNESS), "api-address"], timeout=60).split()
    assert out, "kind_harness api-address printed nothing"
    return out[0]


def _digests() -> dict[str, str]:
    missing = [env for env in DIGEST_ENV.values() if not os.environ.get(env)]
    if missing:
        pytest.skip(f"image digests not provided: set {', '.join(missing)}")
    return {name: os.environ[env] for name, env in DIGEST_ENV.items()}


def _helm_args(context: str, digests: dict[str, str], cidr: str) -> list[str]:
    args = [
        str(CHART),
        "--kube-context", context,
        "--namespace", PLATFORM_NAMESPACE,
        "-f", str(CHART / "values-dev.yaml"),
        "--set", "namespaces.manage=operator-managed",
        "--set", f"networkPolicy.kubernetesApi.cidrs={{{cidr}}}",
    ]
    for name, digest in digests.items():
        args += ["--set", f"images.{name}.digest={digest}"]
    return args


@pytest.fixture
def installed_chart(kind_context: str) -> Iterator[str]:
    if shutil.which("helm") is None:
        pytest.skip("helm is not on PATH")
    digests = _digests()
    cidr = _api_cidr()
    for namespace in (PLATFORM_NAMESPACE, LABS_NAMESPACE):
        _kubectl(kind_context, "apply", "-f", "-", input_text=_namespace_manifest(namespace))
    args = _helm_args(kind_context, digests, cidr)
    try:
        # Phase 1: no hooks, so the dev PostgreSQL and Redis exist before the migration runs.
        _run(["helm", "install", RELEASE, *args, "--no-hooks"])
        for store in ("dev-postgres", "dev-redis"):
            _kubectl(
                kind_context, "rollout", "status", f"statefulset/{RELEASE}-{store}",
                "-n", PLATFORM_NAMESPACE, "--timeout=300s",
            )
        # Phase 2: the pre-upgrade migration hook runs, then Api and Worker must become ready.
        _run(["helm", "upgrade", RELEASE, *args, "--wait", "--timeout", WAIT_TIMEOUT], timeout=700)
        yield kind_context
    finally:
        subprocess.run(  # noqa: S603 - cleanup, failures must not mask the test result
            ["helm", "uninstall", RELEASE, "--kube-context", kind_context,
             "-n", PLATFORM_NAMESPACE, "--wait"],
            capture_output=True, timeout=300, check=False,
        )
        # Removes the hook-created ServiceAccount and Secret that `helm uninstall` leaves behind.
        subprocess.run(  # noqa: S603
            ["kubectl", "--context", kind_context, "delete", "namespace", PLATFORM_NAMESPACE,
             "--ignore-not-found", "--wait=false"],
            capture_output=True, timeout=120, check=False,
        )


def _lab_pods(context: str) -> list[str]:
    out = _kubectl(context, "get", "pods", "-n", LABS_NAMESPACE, "-o", "name")
    return [line for line in out.splitlines() if line.strip()]


# Claims: 1.4, 1.5, 6.3, 15.4, 16.1, 11.13
@pytest.mark.kind
def test_chart_installs_helm_test_passes_and_a_lab_pod_round_trips(
    installed_chart: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = installed_chart

    # Api and Worker readiness endpoints, checked by the Helm test hook pod.
    _run(
        ["helm", "test", RELEASE, "--kube-context", context, "-n", PLATFORM_NAMESPACE,
         "--timeout", HELM_TEST_TIMEOUT],
        timeout=400,
    )

    # One Lab_Pod through the real composition root, then none left.
    from edu_it_domain.labs.sandbox_spec import build_sandbox_spec
    from edu_it_infrastructure.clock import System_Clock
    from edu_it_infrastructure.composition import CompositionSettings, build_sandbox_provider

    monkeypatch.setenv("EDU_IT_KIND_CONTEXT", kind_context_name())
    settings = CompositionSettings.from_env(
        {
            **os.environ,
            "SANDBOX_PROVIDER": "kubernetes",
            "K8S_LABS_NAMESPACE": LABS_NAMESPACE,
            # kind has no gVisor or Kata: plain runc without a RuntimeClass (dev only, Req 2.12).
            "SANDBOX_RUNTIME_KIND": "runc",
            "SANDBOX_RUNTIME_CLASS": "",
        }
    )
    provider = build_sandbox_provider(settings, clock=System_Clock(), k8s_api=kind_api())

    class _Lab:
        image = "edu-it/lab-linux-base:dev"

    handle = provider.provision(build_sandbox_spec(_Lab(), "smoke-sess"))
    try:
        assert [h.sandbox_id for h in provider.list_sandboxes()] == [handle.sandbox_id]
    finally:
        provider.destroy(handle)
    assert list(provider.list_sandboxes()) == []
    assert _lab_pods(context) == []
