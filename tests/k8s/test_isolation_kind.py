"""Isolation suite on a kind cluster (kubernetes-deployment task 14.2).

Order of the checks (Req 16.3 asks for the control first):

1. ``cni_enforces`` (module fixture, used by every test through ``pytestmark``) and
   ``test_deny_all_policy_blocks_probe``: a deny-all NetworkPolicy blocks a probe between two test
   pods, after a control connection proved the probe works. If the CNI does not enforce policy,
   every later test errors instead of passing against an open network.
2. Lab_Pod assertions (Req 16.4): non-root, no service account token, no internet and no other
   namespace within 10 s, no other Lab_Pod, deleted within 60 s after its TTL.
3. RBAC (Req 16.7, 4.4, 4.5, 4.8): the Worker and Api ServiceAccount tokens of the Chart's own
   rendered ``serviceaccounts.yaml`` and ``rbac.yaml`` get ``Forbidden`` outside their grant.
4. Platform_Policies (Req 11.7, 16.8): the Chart's rendered ``networkpolicy-platform.yaml`` is
   applied to a scratch namespace that holds stand-in pods with the component labels. Every
   (source, destination, port) triple not listed in Req 11.2 to 11.6 must fail within 10 s.
5. Pod_Layout under restricted PSA (Req 16.10) and the fork-bomb PID limit (Req 2.6).

The tests use the Chart only through ``helm template`` (no install, no images). They need the
cluster of ``tools/kind_harness.py up`` (Calico, lab node pool, ``podPidsLimit``) and the lab image
``edu-it/lab-linux-base:dev`` preloaded into its nodes. Without a reachable cluster every test is
skipped by ``tests/k8s/conftest.py``; without ``helm`` the Chart-based tests are skipped with that
reason.

Short TTL: only ``test_lab_pod_is_deleted_within_60s_of_ttl`` uses the TTL of
``deploy/helm/edu-it/ci/values-kind-test.yaml`` (``kindTest.sessionTtlSeconds``); every other test
uses the production TTL. The scratch namespaces ``edu-it-iso-*`` are removed at the end.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import uuid
from collections.abc import Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml
from edu_it_domain import config as cfg
from edu_it_domain.labs.k8s_rules import select_reaper_actions
from edu_it_domain.labs.sandbox_spec import build_sandbox_spec
from edu_it_domain.ports.sandbox import SandboxHandle, SandboxProvider
from edu_it_infrastructure.sandbox.k8s_api import PHASE_FAILED, K8sApiError
from edu_it_infrastructure.sandbox.k8s_pod_spec import K8sSettings, Toleration, build_lab_pod

from tests.k8s.conftest import LABS_NAMESPACE, PSA_LABELS
from tests.k8s.kubectl_api import KubectlK8sApi

REPO_ROOT = Path(__file__).resolve().parents[2]
CHART = REPO_ROOT / "deploy" / "helm" / "edu-it"
KIND_TEST_VALUES = CHART / "ci" / "values-kind-test.yaml"

LAB_IMAGE = "edu-it/lab-linux-base:dev"
RELEASE = "iso"
PROBE_NAMESPACE = "edu-it-iso-probe"
PLATFORM_NAMESPACE = "edu-it-iso-platform"
OUTSIDE_NAMESPACE = "edu-it-iso-outside"
SCRATCH_NAMESPACES = (PROBE_NAMESPACE, PLATFORM_NAMESPACE, OUTSIDE_NAMESPACE)
#: Rendered with release name ``iso`` (chart ``edu-it``): the Chart's names are ``iso-edu-it-*``.
WORKER_SA = f"{RELEASE}-edu-it-worker"
API_SA = f"{RELEASE}-edu-it-api"

CONNECT_TIMEOUT_S = 10
#: ``kubectl exec`` and wrapper overhead allowed on top of the 10 s connect timeout.
EXEC_OVERHEAD_S = 5
DELETE_AFTER_TTL_S = 60

PG_PORT = 5432
REDIS_PORT = 6379
UNLISTED_PORT = 9999
OUTSIDE_SERVICE_PORT = 8080

#: Environment of ``provider.exec`` (exactly this plus the provider marker reaches the command).
EXEC_ENV = {"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"}
#: Pod PIDs outside the learner container (pause, grader and owner helpers) plus slack, as in
#: ``tests/labs/test_docker_isolation.py``.
POD_OTHER_PIDS = 10

pytestmark = [pytest.mark.kind, pytest.mark.usefixtures("cni_enforces")]


# --------------------------------------------------------------------------- kubectl helpers


def _kubectl(
    context: str,
    *args: str,
    input_text: str | None = None,
    check: bool = True,
    timeout: int = 180,
    kubeconfig: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run ``kubectl``; with ``kubeconfig`` the given file is the only credential source."""

    base = ["kubectl"]
    base += ["--kubeconfig", str(kubeconfig)] if kubeconfig else ["--context", context]
    argv = [*base, "--request-timeout=30s", *args]
    done = subprocess.run(
        argv, input=input_text, capture_output=True, text=True, timeout=timeout, check=False
    )
    if check:
        assert done.returncode == 0, (
            f"{' '.join(args)} failed ({done.returncode}):\n"
            f"{done.stdout[-2000:]}\n{done.stderr[-2000:]}"
        )
    return done


def _helm() -> str:
    path = shutil.which("helm")
    if path is None:
        pytest.skip("helm is not on PATH")
    return path


def _namespace_manifest(name: str) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "Namespace",
        "metadata": {"name": name, "labels": dict(PSA_LABELS)},
    }


def _apply(context: str, *objects: dict[str, Any]) -> None:
    manifest = {"apiVersion": "v1", "kind": "List", "items": list(objects)}
    _kubectl(context, "apply", "-f", "-", input_text=json.dumps(manifest))


def _standin_pod(
    name: str,
    namespace: str,
    labels: dict[str, str],
    listen_ports: Sequence[int] = (),
) -> dict[str, Any]:
    """A restricted-PSA pod of the lab image that listens on ``listen_ports`` and can be exec'd in.

    It runs on the regular workers (no lab-pool toleration), so the policies under test are the
    only thing between it and its peers.
    """

    listeners = " ".join(
        f"(while :; do echo ok | nc -l -p {port} >/dev/null 2>&1; done) &" for port in listen_ports
    )
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {"name": name, "namespace": namespace, "labels": labels},
        "spec": {
            "restartPolicy": "Never",
            "automountServiceAccountToken": False,
            "terminationGracePeriodSeconds": 1,
            "activeDeadlineSeconds": 3600,
            "securityContext": {
                "runAsNonRoot": True,
                "runAsUser": 1000,
                "seccompProfile": {"type": "RuntimeDefault"},
            },
            "containers": [
                {
                    "name": "main",
                    "image": LAB_IMAGE,
                    "imagePullPolicy": "IfNotPresent",
                    "command": ["/bin/bash", "-c", f"{listeners} exec sleep 7200"],
                    "securityContext": {
                        "allowPrivilegeEscalation": False,
                        "capabilities": {"drop": ["ALL"]},
                    },
                    "resources": {
                        "requests": {"cpu": "10m", "memory": "16Mi"},
                        "limits": {"cpu": "200m", "memory": "64Mi"},
                    },
                }
            ],
        },
    }


def _wait_ready(context: str, namespace: str, *names: str) -> None:
    for name in names:
        _kubectl(
            context,
            "wait",
            "--for=condition=Ready",
            f"pod/{name}",
            "-n",
            namespace,
            "--timeout=180s",
            timeout=240,
        )


def _pod_ip(context: str, namespace: str, name: str) -> str:
    out = _kubectl(
        context, "get", "pod", name, "-n", namespace, "-o", "jsonpath={.status.podIP}"
    ).stdout.strip()
    assert out, f"pod {namespace}/{name} has no IP"
    return out


def _connect_from_pod(
    context: str, namespace: str, pod: str, host: str, port: int
) -> tuple[bool, float]:
    """(connected, seconds) of one TCP connect attempt from inside ``pod``.

    bash opens ``/dev/tcp/<host>/<port>``; ``timeout`` ends the attempt after 10 s. Positional
    arguments of ``bash -c`` start at ``$0`` (here ``_``), so host and port are ``$1`` and ``$2``.
    """

    started = time.monotonic()
    done = _kubectl(
        context,
        "exec",
        "-n",
        namespace,
        pod,
        "--",
        "/bin/bash",
        "-c",
        'timeout 10 bash -c \'exec 3<>/dev/tcp/$0/$1\' "$1" "$2" >/dev/null 2>&1',
        "_",
        host,
        str(port),
        check=False,
    )
    return done.returncode == 0, time.monotonic() - started


def _connect_batch(
    context: str, namespace: str, pod: str, targets: Sequence[tuple[str, int]]
) -> dict[tuple[str, int], bool]:
    """Attempt every ``(ip, port)`` from ``pod`` in parallel batches of 15; True means connected."""

    script = (
        'probe() { timeout 10 bash -c \'exec 3<>/dev/tcp/$0/$1\' "$1" "$2" >/dev/null 2>&1; '
        'echo "$1 $2 $?"; }; i=0; '
        'for t in "$@"; do probe "${t%:*}" "${t#*:}" & i=$((i+1)); '
        "if [ $((i % 15)) -eq 0 ]; then wait; fi; done; wait"
    )
    done = _kubectl(
        context,
        "exec",
        "-n",
        namespace,
        pod,
        "--",
        "/bin/bash",
        "-c",
        script,
        "_",
        *[f"{ip}:{port}" for ip, port in targets],
        check=False,
        timeout=300,
    )
    assert done.returncode == 0, f"probe batch in {namespace}/{pod} failed: {done.stderr[-1000:]}"
    outcome: dict[tuple[str, int], bool] = {}
    for line in done.stdout.splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[2].isdigit():
            outcome[(parts[0], int(parts[1]))] = parts[2] == "0"
    missing = [t for t in targets if t not in outcome]
    assert not missing, f"no result for {missing} from {namespace}/{pod}"
    return outcome


# --------------------------------------------------------------------------- namespaces


def _delete_scratch_namespaces(context: str) -> None:
    _kubectl(
        context,
        "delete",
        "namespace",
        *SCRATCH_NAMESPACES,
        "--ignore-not-found",
        "--wait=true",
        "--timeout=170s",
        check=False,
        timeout=200,
    )


@pytest.fixture(scope="module")
def scratch_namespaces(kind_context: str) -> Iterator[str]:
    _delete_scratch_namespaces(kind_context)
    _apply(kind_context, *[_namespace_manifest(name) for name in SCRATCH_NAMESPACES])
    try:
        yield kind_context
    finally:
        _delete_scratch_namespaces(kind_context)


# --------------------------------------------------------------------------- 16.3 control


@pytest.fixture(scope="module")
def cni_enforces(kind_context: str, kind_labs_namespace: str, scratch_namespaces: str) -> bool:
    """A deny-all NetworkPolicy blocks a probe between two pods; runs before any other check.

    Control first: the same probe connects while no policy exists, so a failure afterwards is the
    policy and not a broken probe.
    """

    context = kind_context
    labels = {"edu-it.io/iso": "probe"}
    _apply(
        context,
        _standin_pod("server", PROBE_NAMESPACE, labels, [OUTSIDE_SERVICE_PORT]),
        _standin_pod("client", PROBE_NAMESPACE, {"edu-it.io/iso": "client"}),
    )
    _wait_ready(context, PROBE_NAMESPACE, "server", "client")
    server_ip = _pod_ip(context, PROBE_NAMESPACE, "server")

    deadline = time.monotonic() + 30
    control = False
    while not control and time.monotonic() < deadline:  # the listener starts a moment after Ready
        control, _ = _connect_from_pod(
            context, PROBE_NAMESPACE, "client", server_ip, OUTSIDE_SERVICE_PORT
        )
        if not control:
            time.sleep(1)
    assert control, "control connection failed before any policy: the probe itself is broken"

    _apply(
        context,
        {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "NetworkPolicy",
            "metadata": {"name": "deny-all", "namespace": PROBE_NAMESPACE},
            "spec": {"podSelector": {}, "policyTypes": ["Ingress", "Egress"]},
        },
    )
    deadline = time.monotonic() + 30  # the CNI programs the policy within a few seconds
    connected = True
    elapsed = 0.0
    while connected and time.monotonic() < deadline:
        connected, elapsed = _connect_from_pod(
            context, PROBE_NAMESPACE, "client", server_ip, OUTSIDE_SERVICE_PORT
        )
    assert not connected, (
        "assertion 'deny-all policy blocks a probe' failed: the CNI does not enforce "
        "NetworkPolicy, so no other isolation result can be trusted"
    )
    assert elapsed <= CONNECT_TIMEOUT_S + EXEC_OVERHEAD_S, f"blocked probe took {elapsed:.1f} s"
    return True


# Claims: 16.3
def test_deny_all_policy_blocks_probe(cni_enforces: bool) -> None:
    assert cni_enforces is True


# --------------------------------------------------------------------------- Lab_Pod fixtures


class _Lab:
    image = LAB_IMAGE


def _settings_env() -> dict[str, str]:
    return {
        **os.environ,
        "SANDBOX_PROVIDER": "kubernetes",
        "K8S_LABS_NAMESPACE": LABS_NAMESPACE,
        # kind has no gVisor or Kata: plain runc without a RuntimeClass (dev only, Req 2.12).
        "SANDBOX_RUNTIME_KIND": "runc",
        "SANDBOX_RUNTIME_CLASS": "",
        # The lab node pool of deploy/kind/kind-config.yaml, as in production.
        "SANDBOX_NODE_SELECTOR": "edu-it.io/pool=labs",
        "SANDBOX_TOLERATIONS": "edu-it.io/labs=true:NoSchedule",
    }


@pytest.fixture(scope="module")
def provider(kind_context: str, kind_api: KubectlK8sApi) -> SandboxProvider:
    from edu_it_infrastructure.clock import System_Clock
    from edu_it_infrastructure.composition import CompositionSettings, build_sandbox_provider

    settings = CompositionSettings.from_env(_settings_env())
    return build_sandbox_provider(settings, clock=System_Clock(), k8s_api=kind_api)


def _provision(provider: SandboxProvider) -> SandboxHandle:
    return provider.provision(build_sandbox_spec(_Lab(), f"iso-{uuid.uuid4().hex[:10]}"))


@pytest.fixture(scope="module")
def lab(provider: SandboxProvider) -> Iterator[SandboxHandle]:
    handle = _provision(provider)
    try:
        yield handle
    finally:
        provider.destroy(handle)


@pytest.fixture(scope="module")
def other_lab(provider: SandboxProvider) -> Iterator[SandboxHandle]:
    handle = _provision(provider)
    try:
        yield handle
    finally:
        provider.destroy(handle)


def _run(
    provider: SandboxProvider,
    handle: SandboxHandle,
    argv: Sequence[str],
    user: str = "learner",
    *,
    timeout_s: float | None = 30,
    env: dict[str, str] | None = None,
) -> Any:
    return provider.exec(
        handle, list(argv), as_user=user, env=env or EXEC_ENV, cwd="/", timeout_s=timeout_s
    )


def _sh(
    provider: SandboxProvider,
    handle: SandboxHandle,
    script: str,
    user: str = "learner",
    **kwargs: Any,
) -> Any:
    return _run(provider, handle, ["/bin/bash", "-c", script], user, **kwargs)


def _lab_connect(
    provider: SandboxProvider, handle: SandboxHandle, host: str, port: int
) -> tuple[bool, float]:
    """(connected, seconds) of one connect from a Lab_Pod learner container, 10 s timeout."""

    started = time.monotonic()
    result = _run(
        provider,
        handle,
        [
            "/usr/bin/timeout",
            str(CONNECT_TIMEOUT_S),
            "/bin/bash",
            "-c",
            "exec 3<>/dev/tcp/$0/$1",
            host,
            str(port),
        ],
        timeout_s=CONNECT_TIMEOUT_S + EXEC_OVERHEAD_S + 10,
    )
    return result.exit_code == 0, time.monotonic() - started


def _pod_json(context: str, namespace: str, name: str) -> dict[str, Any]:
    pod: dict[str, Any] = json.loads(
        _kubectl(context, "get", "pod", name, "-n", namespace, "-o", "json").stdout
    )
    return pod


# --------------------------------------------------------------------------- 16.4 Lab_Pod


# Claims: 2.2, 16.4, 16.5
def test_lab_pod_runs_every_container_non_root(
    kind_context: str, provider: SandboxProvider, lab: SandboxHandle
) -> None:
    pod = _pod_json(kind_context, LABS_NAMESPACE, lab.sandbox_id)
    containers = pod["spec"]["containers"] + pod["spec"].get("initContainers", [])
    for container in containers:
        ctx = container["securityContext"]
        where = f"assertion 'non-root' failed for container {container['name']}"
        assert ctx["runAsNonRoot"] is True, where
        assert ctx["runAsUser"] > 0, where
        assert ctx["allowPrivilegeEscalation"] is False, where
        assert ctx["capabilities"]["drop"] == ["ALL"], where
        assert ctx["seccompProfile"]["type"] == "RuntimeDefault" or (
            pod["spec"]["securityContext"]["seccompProfile"]["type"] == "RuntimeDefault"
        ), where
    for user in ("learner", "grader", "root"):  # root is the non-root owner container (ADR 0009)
        uid = _run(provider, lab, ["/usr/bin/id", "-u"], user)
        assert uid.exit_code == 0, uid.stderr
        assert int(uid.stdout.decode().strip()) != 0, (
            f"assertion 'non-root' failed: container of as_user={user} runs as UID 0"
        )
        caps = _sh(provider, lab, "grep ^CapEff: /proc/self/status", user)
        assert caps.stdout.decode().split()[-1] == "0" * 16, (
            f"assertion 'all capabilities dropped' failed for as_user={user}"
        )


# Claims: 2.3, 16.4, 16.5
def test_lab_pod_has_no_service_account_token(
    kind_context: str, provider: SandboxProvider, lab: SandboxHandle
) -> None:
    pod = _pod_json(kind_context, LABS_NAMESPACE, lab.sandbox_id)
    assert pod["spec"]["automountServiceAccountToken"] is False, (
        "assertion 'no service account token' failed: automountServiceAccountToken is not false"
    )
    projected = [
        v["name"]
        for v in pod["spec"].get("volumes", [])
        if "serviceAccountToken" in json.dumps(v.get("projected", {}))
    ]
    assert not projected, f"assertion 'no service account token' failed: volumes {projected}"
    for user in ("learner", "grader", "root"):
        found = _sh(
            provider,
            lab,
            "test -e /var/run/secrets/kubernetes.io/serviceaccount/token && echo PRESENT; true",
            user,
        )
        assert b"PRESENT" not in found.stdout, (
            f"assertion 'no service account token' failed: token file in as_user={user}"
        )


# Claims: 3.6, 16.4, 16.5
def test_lab_pod_cannot_reach_the_internet(provider: SandboxProvider, lab: SandboxHandle) -> None:
    for host, port in (("1.1.1.1", 80), ("8.8.8.8", 53)):
        connected, elapsed = _lab_connect(provider, lab, host, port)
        assert not connected, f"assertion 'no internet' failed: connected to {host}:{port}"
        assert elapsed <= CONNECT_TIMEOUT_S + EXEC_OVERHEAD_S, (
            f"assertion 'no internet within 10 s' failed: {host}:{port} took {elapsed:.1f} s"
        )


@pytest.fixture(scope="module")
def outside_service(scratch_namespaces: str) -> str:
    """ClusterIP of a Service in a namespace other than ``labs``; a probe pod reaches it."""

    context = scratch_namespaces
    labels = {"edu-it.io/iso": "outside"}
    _apply(
        context,
        _standin_pod("outside", OUTSIDE_NAMESPACE, labels, [OUTSIDE_SERVICE_PORT]),
        _standin_pod("outside-client", OUTSIDE_NAMESPACE, {"edu-it.io/iso": "client"}),
        {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {"name": "outside", "namespace": OUTSIDE_NAMESPACE},
            "spec": {
                "selector": labels,
                "ports": [{"port": OUTSIDE_SERVICE_PORT, "targetPort": OUTSIDE_SERVICE_PORT}],
            },
        },
    )
    _wait_ready(context, OUTSIDE_NAMESPACE, "outside", "outside-client")
    cluster_ip = _kubectl(
        context,
        "get",
        "svc",
        "outside",
        "-n",
        OUTSIDE_NAMESPACE,
        "-o",
        "jsonpath={.spec.clusterIP}",
    ).stdout.strip()
    assert cluster_ip
    # Control: the Service answers a pod of its own namespace, so a blocked Lab_Pod is the policy.
    deadline = time.monotonic() + 30
    reached = False
    while not reached and time.monotonic() < deadline:
        reached, _ = _connect_from_pod(
            context, OUTSIDE_NAMESPACE, "outside-client", cluster_ip, OUTSIDE_SERVICE_PORT
        )
        if not reached:
            time.sleep(1)
    assert reached, "control failed: the Service in the other namespace does not answer"
    return cluster_ip


# Claims: 3.6, 16.4, 16.5
def test_lab_pod_cannot_reach_a_service_in_another_namespace(
    provider: SandboxProvider, lab: SandboxHandle, outside_service: str
) -> None:
    connected, elapsed = _lab_connect(provider, lab, outside_service, OUTSIDE_SERVICE_PORT)
    assert not connected, (
        f"assertion 'no other namespace' failed: connected to {outside_service}"
        f":{OUTSIDE_SERVICE_PORT}"
    )
    assert elapsed <= CONNECT_TIMEOUT_S + EXEC_OVERHEAD_S, (
        f"assertion 'no other namespace within 10 s' failed: took {elapsed:.1f} s"
    )


# Claims: 3.6
def test_lab_pod_cannot_reach_another_lab_pod(
    kind_context: str, provider: SandboxProvider, lab: SandboxHandle, other_lab: SandboxHandle
) -> None:
    port = OUTSIDE_SERVICE_PORT
    listener = (
        f"(while :; do echo ok | nc -l -p {port} >/dev/null 2>&1; done) "
        ">/dev/null 2>&1 </dev/null & disown"
    )
    assert _sh(provider, other_lab, listener).exit_code == 0
    time.sleep(1)
    local, _ = _lab_connect(provider, other_lab, "127.0.0.1", port)
    assert local, "control failed: the listener in the other Lab_Pod does not answer locally"
    ip = _pod_ip(kind_context, LABS_NAMESPACE, other_lab.sandbox_id)
    connected, elapsed = _lab_connect(provider, lab, ip, port)
    assert not connected, "assertion 'no other Lab_Pod' failed: connected to another Lab_Pod"
    assert elapsed <= CONNECT_TIMEOUT_S + EXEC_OVERHEAD_S


def _kind_ttl_seconds() -> int:
    """The short TTL of the kind test values override; the only user of that file here."""

    values = yaml.safe_load(KIND_TEST_VALUES.read_text(encoding="utf-8"))
    ttl = int(values["kindTest"]["sessionTtlSeconds"])
    assert 0 < ttl <= 120, f"kindTest.sessionTtlSeconds {ttl} is not a short TTL"
    return ttl


# Claims: 16.4, 16.5
def test_lab_pod_is_deleted_within_60s_of_ttl(
    kind_context: str, kind_api: KubectlK8sApi, provider: SandboxProvider
) -> None:
    """The kubelet ends the pod at ``activeDeadlineSeconds``; the Reaper then deletes it.

    The pod is the production manifest of ``build_lab_pod`` with only the deadline replaced by the
    kind test TTL. The Reaper step is the real selection (``select_reaper_actions``) followed by
    the provider's ``destroy``; the Worker process itself is not part of this suite.
    """

    ttl = _kind_ttl_seconds()
    spec = build_sandbox_spec(_Lab(), f"iso-ttl-{uuid.uuid4().hex[:8]}")
    settings = K8sSettings(
        namespace=LABS_NAMESPACE,
        runtime_class_name=None,
        runtime_kind="runc",
        node_selector={"edu-it.io/pool": "labs"},
        tolerations=(
            Toleration(key="edu-it.io/labs", operator="Equal", value="true", effect="NoSchedule"),
        ),
        node_loss_toleration_seconds=300,
        grader_gid=10001,
    )
    manifest = build_lab_pod(spec, settings)
    assert manifest["spec"]["activeDeadlineSeconds"] == int(cfg.LAB_SESSION_TTL.total_seconds())
    manifest["spec"]["activeDeadlineSeconds"] = ttl
    name = manifest["metadata"]["name"]

    started = time.monotonic()
    kind_api.create_pod(LABS_NAMESPACE, manifest)
    try:
        limit = started + ttl + DELETE_AFTER_TTL_S
        while kind_api.read_pod(LABS_NAMESPACE, name).phase != PHASE_FAILED:
            assert time.monotonic() < limit, (
                f"assertion 'pod ends at its TTL' failed: still not Failed {ttl} s + "
                f"{DELETE_AFTER_TTL_S} s after creation"
            )
            time.sleep(2)

        # Reaper tick: no session row for this pod, so the pod is not of a live session.
        handles = [h for h in provider.list_sandboxes() if h.sandbox_id == name]
        assert handles, "the expired pod is not listed by the provider"
        actions = select_reaper_actions([], handles, datetime.now(UTC))
        assert [h.sandbox_id for h in actions.delete_pods] == [name]
        for handle in actions.delete_pods:
            provider.destroy(handle)

        while True:
            try:
                kind_api.read_pod(LABS_NAMESPACE, name)
            except K8sApiError as exc:
                assert exc.status == 404
                break
            assert time.monotonic() < limit, (
                f"assertion 'deleted within 60 s after TTL' failed for pod {name}"
            )
            time.sleep(1)
    finally:
        _kubectl(
            kind_context,
            "delete",
            "pod",
            name,
            "-n",
            LABS_NAMESPACE,
            "--ignore-not-found",
            "--wait=false",
            check=False,
        )


# --------------------------------------------------------------------------- Chart objects


@dataclass(frozen=True)
class RenderedChart:
    context: str
    documents: tuple[dict[str, Any], ...]


def _render_chart(*templates: str) -> tuple[dict[str, Any], ...]:
    argv = [
        _helm(),
        "template",
        RELEASE,
        str(CHART),
        "-f",
        str(CHART / "values-dev.yaml"),
        "--set",
        f"namespaces.platform={PLATFORM_NAMESPACE}",
        "--set",
        "namespaces.manage=operator-managed",
        # The value is a provider specific CIDR; any valid one renders the rule (Req 11.13).
        "--set",
        "networkPolicy.kubernetesApi.cidrs={10.0.0.1/32}",
    ]
    for template in templates:
        argv += ["-s", f"templates/{template}"]
    done = subprocess.run(argv, capture_output=True, text=True, timeout=120, check=False)
    assert done.returncode == 0, f"helm template failed:\n{done.stderr[-2000:]}"
    return tuple(d for d in yaml.safe_load_all(done.stdout) if d)


@pytest.fixture(scope="module")
def chart(scratch_namespaces: str, kind_labs_namespace: str) -> Iterator[RenderedChart]:
    """ServiceAccounts, namespaced RBAC and Platform_Policies exactly as the Chart renders them."""

    context = scratch_namespaces
    documents = _render_chart("serviceaccounts.yaml", "rbac.yaml", "networkpolicy-platform.yaml")
    kinds = {d["kind"] for d in documents}
    assert {"ServiceAccount", "Role", "RoleBinding", "NetworkPolicy"} <= kinds
    _apply(context, *documents)
    try:
        yield RenderedChart(context, documents)
    finally:
        _kubectl(
            context,
            "delete",
            "-f",
            "-",
            "--ignore-not-found",
            "--wait=false",
            input_text=json.dumps({"apiVersion": "v1", "kind": "List", "items": list(documents)}),
            check=False,
        )


def _token_kubeconfig(context: str, service_account: str, directory: Path) -> Path:
    """A kubeconfig whose only credential is the ServiceAccount token.

    ``kubectl --token`` on top of the admin context would still authenticate with the admin client
    certificate, so the user entry is replaced instead.
    """

    token = _kubectl(
        context, "create", "token", service_account, "-n", PLATFORM_NAMESPACE, "--duration=600s"
    ).stdout.strip()
    config = json.loads(
        _kubectl(context, "config", "view", "--minify", "--flatten", "-o", "json").stdout
    )
    config["users"] = [{"name": u["name"], "user": {"token": token}} for u in config["users"]]
    path = directory / f"{service_account}.kubeconfig"
    path.write_text(json.dumps(config), encoding="utf-8")
    return path


# An RBAC denial. Older kubectl prints "Error from server (Forbidden): ..."; newer clients
# print "error: failed to create <kind>: <resource> is forbidden: User "..." cannot <verb> ...".
_RBAC_DENIED = re.compile(r"\(Forbidden\)|is forbidden: User \"[^\"]+\" cannot ")


def _assert_forbidden(done: subprocess.CompletedProcess[str], what: str) -> None:
    assert done.returncode != 0 and _RBAC_DENIED.search(done.stderr), (
        f"assertion '{what} is Forbidden' failed (exit {done.returncode}):\n"
        f"{done.stdout[-500:]}{done.stderr[-500:]}"
    )
    assert not done.stdout.strip(), f"assertion '{what}' returned data: {done.stdout[:200]}"


@pytest.fixture(scope="module")
def worker_config(chart: RenderedChart, tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _token_kubeconfig(chart.context, WORKER_SA, tmp_path_factory.mktemp("worker"))


@pytest.fixture(scope="module")
def api_config(chart: RenderedChart, tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _token_kubeconfig(chart.context, API_SA, tmp_path_factory.mktemp("api"))


def _pod_manifest(namespace: str) -> str:
    return json.dumps(_standin_pod("rbac-probe", namespace, {"edu-it.io/iso": "rbac"}))


# Claims: 4.4, 16.7
@pytest.mark.parametrize(
    "args",
    [
        ("get", "pods", "-n", "default"),
        ("get", "secrets", "-n", PLATFORM_NAMESPACE),
        ("get", "nodes"),
    ],
    ids=["read-pods-default", "read-secrets-platform", "read-cluster-scoped"],
)
def test_worker_is_forbidden_to_read_outside_labs(
    chart: RenderedChart, worker_config: Path, args: tuple[str, ...]
) -> None:
    done = _kubectl(chart.context, *args, check=False, kubeconfig=worker_config)
    _assert_forbidden(done, f"Worker {' '.join(args)}")


# Claims: 4.4, 16.7
@pytest.mark.parametrize(
    "namespace", ["default", PLATFORM_NAMESPACE], ids=["create-pod-default", "create-pod-platform"]
)
def test_worker_is_forbidden_to_create_outside_labs(
    chart: RenderedChart, worker_config: Path, namespace: str
) -> None:
    done = _kubectl(
        chart.context,
        "create",
        "-n",
        namespace,
        "-f",
        "-",
        input_text=_pod_manifest(namespace),
        check=False,
        kubeconfig=worker_config,
    )
    _assert_forbidden(done, f"Worker create pod in {namespace}")
    config_map = _kubectl(
        chart.context,
        "create",
        "configmap",
        "iso-probe",
        "-n",
        namespace,
        check=False,
        kubeconfig=worker_config,
    )
    _assert_forbidden(config_map, f"Worker create configmap in {namespace}")


# Claims: 4.5
def test_worker_is_forbidden_other_verbs_and_resources_in_labs(
    chart: RenderedChart, worker_config: Path
) -> None:
    # kubectl patch GETs the object before it sends the PATCH, and the Worker may get pods in
    # labs: the patch target must exist, or the GET's NotFound hides the PATCH authorization.
    _kubectl(chart.context, "create", "-n", LABS_NAMESPACE, "-f", "-",
             input_text=_pod_manifest(LABS_NAMESPACE))
    try:
        for args in (
            ("create", "configmap", "iso-probe", "-n", LABS_NAMESPACE),
            ("get", "secrets", "-n", LABS_NAMESPACE),
            ("patch", "pod", "rbac-probe", "-n", LABS_NAMESPACE, "--type=merge",
             "-p", '{"metadata":{"labels":{"edu-it.io/patched":"yes"}}}'),
            # deletecollection: "kubectl delete pods --all" lists and deletes pod by pod, which
            # the Worker may do in labs; a DELETE on the collection is the deletecollection verb.
            ("delete", "--raw", f"/api/v1/namespaces/{LABS_NAMESPACE}/pods"),
        ):
            done = _kubectl(chart.context, *args, check=False, kubeconfig=worker_config)
            _assert_forbidden(done, f"Worker {' '.join(args)}")
    finally:
        _kubectl(chart.context, "delete", "pod", "rbac-probe", "-n", LABS_NAMESPACE,
                 "--ignore-not-found", "--wait=false", check=False)


# Claims: 4.8
def test_api_is_forbidden_to_create_or_delete_pods(chart: RenderedChart, api_config: Path) -> None:
    for namespace in (LABS_NAMESPACE, "default", PLATFORM_NAMESPACE):
        create = _kubectl(
            chart.context,
            "create",
            "-n",
            namespace,
            "-f",
            "-",
            input_text=_pod_manifest(namespace),
            check=False,
            kubeconfig=api_config,
        )
        _assert_forbidden(create, f"Api create pod in {namespace}")
        delete = _kubectl(
            chart.context,
            "delete",
            "pod",
            "iso-absent",
            "-n",
            namespace,
            check=False,
            kubeconfig=api_config,
        )
        _assert_forbidden(delete, f"Api delete pod in {namespace}")
    collection = _kubectl(
        chart.context,
        "delete",
        "pods",
        "--all",
        "-n",
        LABS_NAMESPACE,
        check=False,
        kubeconfig=api_config,
    )
    _assert_forbidden(collection, "Api deletecollection of pods in labs")


# --------------------------------------------------------------------------- 11.7 / 16.8


COMPONENTS = ("api", "worker", "web", "migration", "dev-postgres", "dev-redis")
#: The only paths of Req 11.2 to 11.6 that stay inside the scratch Platform_Namespace.
LISTED_PATHS = frozenset(
    {
        ("api", "dev-postgres", PG_PORT),
        ("api", "dev-redis", REDIS_PORT),
        ("worker", "dev-postgres", PG_PORT),
        ("worker", "dev-redis", REDIS_PORT),
        ("migration", "dev-postgres", PG_PORT),
        ("web", "api", 8000),  # the web to api arrow of the design; rendered by the Chart
    }
)


def _platform_labels(component: str) -> dict[str, str]:
    return {
        "app.kubernetes.io/name": "edu-it",
        "app.kubernetes.io/instance": RELEASE,
        "app.kubernetes.io/component": component,
    }


def _chart_ports() -> list[int]:
    values = yaml.safe_load((CHART / "values-dev.yaml").read_text(encoding="utf-8"))
    base = yaml.safe_load((CHART / "values.yaml").read_text(encoding="utf-8"))
    api_port = int((values.get("api") or {}).get("port") or base["api"]["port"])
    web_port = int((values.get("web") or {}).get("port") or base["web"]["port"])
    return sorted({PG_PORT, REDIS_PORT, api_port, web_port, UNLISTED_PORT})


@pytest.fixture(scope="module")
def platform_pods(chart: RenderedChart) -> dict[str, str]:
    """Stand-in pods (listening on every port under test) for each Platform component + outsider."""

    context = chart.context
    ports = _chart_ports()
    pods = [
        _standin_pod(component, PLATFORM_NAMESPACE, _platform_labels(component), ports)
        for component in COMPONENTS
    ]
    # The outsider has no NetworkPolicy of its own and tries to reach every component.
    pods.append(_standin_pod("outsider", OUTSIDE_NAMESPACE, {"edu-it.io/iso": "x"}, ports))
    _apply(context, *pods)
    _wait_ready(context, PLATFORM_NAMESPACE, *COMPONENTS)
    _wait_ready(context, OUTSIDE_NAMESPACE, "outsider")
    ips = {c: _pod_ip(context, PLATFORM_NAMESPACE, c) for c in COMPONENTS}
    ips["outsider"] = _pod_ip(context, OUTSIDE_NAMESPACE, "outsider")
    time.sleep(5)  # listeners and the CNI programming of the new policies
    return ips


def _probe_source(
    context: str, source: str, ips: dict[str, str], ports: Sequence[int]
) -> dict[tuple[str, str, int], bool]:
    namespace = OUTSIDE_NAMESPACE if source == "outsider" else PLATFORM_NAMESPACE
    targets = [(ips[d], p) for d in COMPONENTS if d != source for p in ports]
    connected = _connect_batch(context, namespace, source, targets)
    by_ip = {ip: name for name, ip in ips.items()}
    return {(source, by_ip[ip], port): ok for (ip, port), ok in connected.items()}


# Claims: 11.7, 16.8
def test_every_unlisted_platform_path_is_blocked_within_10s(
    chart: RenderedChart, platform_pods: dict[str, str]
) -> None:
    ports = _chart_ports()
    sources = [*COMPONENTS, "outsider"]
    with ThreadPoolExecutor(max_workers=len(sources)) as pool:
        results = list(
            pool.map(lambda s: _probe_source(chart.context, s, platform_pods, ports), sources)
        )
    outcome = {key: ok for part in results for key, ok in part.items()}

    # Controls: the listed paths connect (retry once: a one-shot listener may be restarting).
    broken = []
    for path in sorted(LISTED_PATHS):
        source, _destination, port = path
        if path in outcome and not outcome[path]:
            again = _probe_source(chart.context, source, platform_pods, [port])
            if not again[path]:
                broken.append(path)
    assert not broken, f"control failed: listed paths do not connect: {broken}"

    accepted = sorted(path for path, ok in outcome.items() if ok and path not in LISTED_PATHS)
    assert not accepted, (
        f"assertion 'every unlisted Platform_Policies path is blocked' failed: {accepted}"
    )
    # Each component skips itself; the outsider reaches for all six components.
    expected = (len(COMPONENTS) * (len(COMPONENTS) - 1) + len(COMPONENTS)) * len(ports)
    assert len(outcome) == expected, "the matrix did not cover every (source, destination, port)"


# --------------------------------------------------------------------------- 16.10 Pod_Layout


# Claims: 16.10
def test_learner_cannot_read_the_grader_or_owner_mounts(
    provider: SandboxProvider, lab: SandboxHandle
) -> None:
    # The owner owns the /grader mount (init-grader dir, mode 2750); write a canary there.
    written = _sh(provider, lab, "echo grader-secret > /grader/canary && echo ok", "root")
    assert written.stdout.decode().strip() == "ok", written.stderr.decode()
    # Layout B gives the learner container no /grader mount; the lab image ships a bare /grader
    # directory (0700 root) in the learner container only to satisfy the baked image. The learner
    # must not be able to list it, read anything inside it, or reach the grader/owner copy through
    # /proc. The grader has written a canary into its own /grader before this test (see the
    # grader_canary fixture); the learner must never see it.
    script = (
        # 2>/dev/null drops the "Permission denied" diagnostic: only real listed names count.
        "ls /grader 2>/dev/null | grep -q . && echo LISTED; "
        "cat /grader/canary 2>/dev/null && echo READ; "
        # The learner's own /proc/<pid>/root/grader is the empty image dir, not the grader mount:
        # reach for the canary through every visible proc root instead.
        "for p in /proc/[0-9]*; do cat $p/root/grader/canary 2>/dev/null && echo VIA_PROC_$p; done; "
        "echo end"
    )
    result = _sh(provider, lab, script)
    out = result.stdout.decode()
    assert out.split() == ["end"], (
        f"assertion 'learner cannot read grader or owner mounts' failed: {out!r}"
    )


# Claims: 16.10
def test_learner_cannot_see_signal_or_read_the_environment_of_helpers(
    provider: SandboxProvider, lab: SandboxHandle
) -> None:
    markers = {"grader": "3701", "root": "3702"}  # as_user root is the owner container
    for user, seconds in markers.items():
        env = {**EXEC_ENV, "ISO_SECRET": f"secret-of-{user}"}
        started = _sh(
            provider, lab, f"sleep {seconds} >/dev/null 2>&1 </dev/null & disown", user, env=env
        )
        assert started.exit_code == 0, started.stderr
    time.sleep(1)
    for user, seconds in markers.items():
        # Visible in its own container, invisible and unsignalable from the learner container.
        # "[s]leep N" matches the marker process but not the probe's own bash and timeout
        # wrappers, whose command lines carry the literal pattern text.
        marker = f"'[s]leep {seconds}'"
        own = _sh(provider, lab, f"pgrep -f {marker} >/dev/null", user)
        assert own.exit_code == 0, f"control failed: the {user} marker process does not run"
        seen = _sh(provider, lab, f"pgrep -f {marker}; true")
        assert seen.stdout.decode().strip() == "", (
            f"assertion 'no cross-container process visibility' failed for {user}"
        )
        killed = _sh(provider, lab, f"pkill -9 -f {marker}")
        assert killed.exit_code != 0, f"assertion 'cannot signal {user} processes' failed"
        environ = _sh(
            provider,
            lab,
            f"grep -l 'secret-of-{user}' /proc/[0-9]*/environ 2>/dev/null; true",
        )
        assert environ.stdout.decode().strip() == "", (
            f"assertion 'cannot read the environment of {user} processes' failed"
        )
        still = _sh(provider, lab, f"pgrep -f {marker} >/dev/null", user)
        assert still.exit_code == 0, f"the {user} process did not survive the learner's kill"


# Claims: 16.10
def test_freeze_of_learner_processes_succeeds_with_all_capabilities_dropped(
    provider: SandboxProvider, lab: SandboxHandle
) -> None:
    caps = _sh(provider, lab, "grep ^CapEff: /proc/self/status")
    assert caps.stdout.decode().split()[-1] == "0" * 16
    detach = "sleep 301 >/dev/null 2>&1 </dev/null & disown"
    assert _sh(provider, lab, detach).exit_code == 0

    def state() -> str:
        listing = _sh(provider, lab, "ps -eo stat=,args= | grep '[s]leep 301'; true")
        return str(listing.stdout.decode().strip())

    before = state()
    assert before.startswith("S"), f"control failed: learner process not running: {before!r}"
    # Sent without timeout_s, as GradeRun does (lab-images/README.md): kill -1 also signals the
    # provider's /usr/bin/timeout wrapper, so with a timeout the exec would stop itself.
    stop = _run(provider, lab, ["/bin/kill", "-s", "STOP", "-1"], timeout_s=None)
    try:
        assert stop.exit_code == 0, f"assertion 'freeze succeeds' failed: {stop.stderr!r}"
        assert state().startswith("T"), (
            f"assertion 'freeze stops the learner processes' failed: {state()!r}"
        )
    finally:
        _run(provider, lab, ["/bin/kill", "-s", "CONT", "-1"], timeout_s=None)
        _sh(provider, lab, "pkill -9 -f 'sleep 301'; true")


# --------------------------------------------------------------------------- PID limit


# Claims: 2.6, 16.10
def test_fork_bomb_is_stopped_by_the_pod_pid_limit(provider: SandboxProvider) -> None:
    """A fork bomb in a fresh Lab_Pod cannot use more PIDs than the node's ``podPidsLimit``.

    The Kubernetes pod API has no per-pod PID field (ADR 0009, OQ-6); the pod cgroup limit comes
    from the kubelet of the lab nodes. The bomb asks for three times the limit and the pod must
    stop it at the limit less the pod's other processes.
    """

    limit = cfg.SANDBOX_PID_LIMIT
    handle = _provision(provider)
    try:
        # At the ceiling even ``ps`` cannot fork, so read the cgroup with shell builtins and wait on
        # a pre-made fifo instead of forking ``sleep``.
        script = (
            "mkfifo /tmp/f; exec 3<>/tmp/f; "
            f"(seq 1 {limit * 3} | /usr/bin/xargs -P {limit * 3} -n1 /bin/sleep 60) 2>/dev/null & "
            "read -t 3 -u 3; read -r cur < /sys/fs/cgroup/pids.current; echo cur=$cur"
        )
        result = _sh(provider, handle, script, timeout_s=30)
        out = dict(line.split("=", 1) for line in result.stdout.decode().split() if "=" in line)
        assert "cur" in out, f"no PID count: {result.stdout!r} {result.stderr!r}"
        current = int(out["cur"])
        assert limit - POD_OTHER_PIDS <= current <= limit, (
            f"assertion 'fork bomb stopped at the PID limit' failed: {current} PIDs, "
            f"limit {limit}, the bomb requested {limit * 3}"
        )
        pod = _pod_json(
            os.environ.get("EDU_IT_KIND_CONTEXT", "kind-edu-it"), LABS_NAMESPACE, handle.sandbox_id
        )
        assert pod["metadata"]["annotations"]["edu-it.io/pid-limit"] == str(limit)
    finally:
        provider.destroy(handle)
