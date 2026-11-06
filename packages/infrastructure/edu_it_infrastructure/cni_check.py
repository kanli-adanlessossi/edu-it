"""The post-install CNI check: ``python -m edu_it_infrastructure.cni_check``.

Proves that the cluster's CNI enforces NetworkPolicy (kubernetes-deployment Req 11.9 to 11.12,
design "Post-install CNI check"). The command:

1. creates two probe pods in the platform namespace, ``probe-allowed`` and ``probe-denied``,
   each listening on one TCP port, and two NetworkPolicies: one isolates both probes (ingress
   denied), one re-allows ingress to ``probe-allowed`` from the check Job's own pod only;
2. tries to connect to both from the Job pod;
3. hands the facts to the domain rule ``cni_check_outcome`` and acts on the result.

``Pass`` exits 0. ``Warn`` (enforcement absent, not required) writes a warning and exits 0.
``Fail`` (enforcement absent, required) writes an error and exits 1, so a Helm install with
``--wait`` fails. A check that cannot finish inside its budget, or cannot create its objects, counts
as ``timed_out``, which the domain rule treats as enforcement absent (Req 11.12).

The cluster sits behind the narrow :class:`CniCheckApi` port, so the logic runs over
``FakeCniCheckApi`` in unit tests. The real adapter imports the ``kubernetes`` package lazily.
The probe objects are removed on every exit path; they also carry ``activeDeadlineSeconds`` so a
killed Job (hook deadline) cannot leave a probe pod behind.
"""

from __future__ import annotations

import argparse
import logging
import os
import socket
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Final, Protocol, runtime_checkable

from edu_it_domain.labs.k8s_rules import cni_check_outcome
from edu_it_domain.labs.k8s_types import CniOutcome

__all__ = [
    "ALLOWED_PROBE",
    "CLIENT_LABEL",
    "DENIED_PROBE",
    "CniCheckApi",
    "CniCheckError",
    "CniCheckResult",
    "ProbePod",
    "build_network_policies",
    "build_probe_pod",
    "main",
    "run_check",
]

_LOG = logging.getLogger("edu_it_infrastructure.cni_check")

EXIT_OK: Final[int] = 0
EXIT_ENFORCEMENT_ABSENT: Final[int] = 1
EXIT_USAGE: Final[int] = 2

ALLOWED_PROBE: Final[str] = "probe-allowed"
DENIED_PROBE: Final[str] = "probe-denied"
ALLOW_POLICY: Final[str] = "cni-check-allow"
DENY_POLICY: Final[str] = "cni-check-deny"
#: Label of the check Job's pod; the allow policy admits ingress from pods carrying it.
CLIENT_LABEL: Final[tuple[str, str]] = ("edu-it.io/cni-check-client", "true")
PROBE_ROLE_LABEL: Final[str] = "edu-it.io/cni-probe"
PROBE_PORT: Final[int] = 8080
#: Interpreter of the production images (deploy/images/*/Dockerfile): virtualenv, no shell.
PYTHON: Final[str] = "/opt/venv/bin/python"
#: A probe pod removes itself after this long if the Job was killed before its cleanup ran.
PROBE_DEADLINE_SECONDS: Final[int] = 300
#: Total budget of one run. Below the 120 s Job deadline so the verdict is written before it hits.
DEFAULT_BUDGET_S: Final[float] = 100.0
CONNECT_TIMEOUT_S: Final[float] = 2.0
POLL_INTERVAL_S: Final[float] = 2.0

# One line, no shell: the api image is distroless. Accepts TCP connections and closes them.
_LISTENER: Final[str] = (
    "import socketserver;"
    f"socketserver.TCPServer(('',{PROBE_PORT}),socketserver.BaseRequestHandler).serve_forever()"
)

ENFORCEMENT_ABSENT_MESSAGE: Final[str] = "NetworkPolicy enforcement is absent"


class CniCheckError(Exception):
    """The check could not create or read its objects (not a verdict on the CNI)."""


@dataclass(frozen=True, slots=True)
class ProbePod:
    """What the check reads from a probe pod."""

    name: str
    ready: bool
    ip: str | None


@runtime_checkable
class CniCheckApi(Protocol):
    """The cluster operations of the check. No Kubernetes client type crosses the port."""

    def create_pod(self, namespace: str, manifest: Mapping[str, object]) -> None:
        """Create a pod. Raises :class:`CniCheckError` on any rejection."""
        ...

    def create_network_policy(self, namespace: str, manifest: Mapping[str, object]) -> None:
        """Create a NetworkPolicy. Raises :class:`CniCheckError` on any rejection."""
        ...

    def read_pod(self, namespace: str, name: str) -> ProbePod | None:
        """Return the pod, or ``None`` when it does not exist."""
        ...

    def delete_pod(self, namespace: str, name: str) -> None:
        """Delete the pod; a missing pod is not an error."""
        ...

    def delete_network_policy(self, namespace: str, name: str) -> None:
        """Delete the policy; a missing policy is not an error."""
        ...

    def can_connect(self, ip: str, port: int, timeout_s: float) -> bool:
        """One TCP connection attempt from the Job pod; ``True`` when it completes."""
        ...


@dataclass(frozen=True, slots=True)
class CniCheckResult:
    """The facts observed and the domain verdict."""

    outcome: CniOutcome
    allowed_reachable: bool
    denied_blocked: bool
    timed_out: bool


# ---------------------------------------------------------------------- manifests


def build_probe_pod(name: str, role: str, image: str) -> dict[str, Any]:
    """Probe pod manifest that passes restricted Pod Security (non-root, no capabilities)."""
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": name,
            "labels": {
                "app.kubernetes.io/name": "edu-it",
                "app.kubernetes.io/component": "cni-check-probe",
                "app.kubernetes.io/part-of": "edu-it",
                PROBE_ROLE_LABEL: role,
            },
        },
        "spec": {
            "restartPolicy": "Never",
            "automountServiceAccountToken": False,
            "activeDeadlineSeconds": PROBE_DEADLINE_SECONDS,
            "securityContext": {"runAsNonRoot": True, "seccompProfile": {"type": "RuntimeDefault"}},
            "containers": [
                {
                    "name": "probe",
                    "image": image,
                    "imagePullPolicy": "IfNotPresent",
                    "command": [PYTHON, "-c", _LISTENER],
                    "ports": [{"containerPort": PROBE_PORT, "protocol": "TCP"}],
                    "readinessProbe": {
                        "tcpSocket": {"port": PROBE_PORT},
                        "periodSeconds": 1,
                    },
                    "securityContext": {
                        "allowPrivilegeEscalation": False,
                        "readOnlyRootFilesystem": True,
                        "runAsNonRoot": True,
                        "capabilities": {"drop": ["ALL"]},
                        "seccompProfile": {"type": "RuntimeDefault"},
                    },
                    "resources": {
                        "requests": {"cpu": "10m", "memory": "32Mi"},
                        "limits": {"cpu": "100m", "memory": "64Mi"},
                    },
                }
            ],
        },
    }


def build_network_policies() -> tuple[dict[str, Any], dict[str, Any]]:
    """``(deny, allow)``: isolate both probes, then admit the Job pod to ``probe-allowed`` only."""
    managed = {"app.kubernetes.io/component": "cni-check-probe"}
    deny = {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": {"name": DENY_POLICY, "labels": dict(managed)},
        "spec": {"podSelector": {"matchLabels": dict(managed)}, "policyTypes": ["Ingress"]},
    }
    allow = {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": {"name": ALLOW_POLICY, "labels": dict(managed)},
        "spec": {
            "podSelector": {"matchLabels": {PROBE_ROLE_LABEL: "allowed"}},
            "policyTypes": ["Ingress"],
            "ingress": [
                {
                    "from": [{"podSelector": {"matchLabels": {CLIENT_LABEL[0]: CLIENT_LABEL[1]}}}],
                    "ports": [{"protocol": "TCP", "port": PROBE_PORT}],
                }
            ],
        },
    }
    return deny, allow


# ---------------------------------------------------------------------- the check


def _wait_ready(
    api: CniCheckApi,
    namespace: str,
    name: str,
    deadline: float,
    monotonic: Callable[[], float],
    sleep: Callable[[float], None],
) -> str | None:
    """IP of the pod once it is Ready, or ``None`` when the deadline passes first."""
    while True:
        pod = api.read_pod(namespace, name)
        if pod is not None and pod.ready and pod.ip:
            return pod.ip
        if monotonic() >= deadline:
            return None
        sleep(POLL_INTERVAL_S)


def _poll(
    condition: Callable[[], bool],
    deadline: float,
    monotonic: Callable[[], float],
    sleep: Callable[[float], None],
) -> bool:
    """``True`` once ``condition`` holds; ``False`` when the deadline passes first."""
    while not condition():
        if monotonic() >= deadline:
            return False
        sleep(POLL_INTERVAL_S)
    return True


def _cleanup(api: CniCheckApi, namespace: str) -> None:
    for policy in (ALLOW_POLICY, DENY_POLICY):
        try:
            api.delete_network_policy(namespace, policy)
        except Exception:
            _LOG.warning("could not delete NetworkPolicy %s", policy, exc_info=True)
    for pod in (ALLOWED_PROBE, DENIED_PROBE):
        try:
            api.delete_pod(namespace, pod)
        except Exception:
            _LOG.warning("could not delete pod %s", pod, exc_info=True)


def run_check(
    api: CniCheckApi,
    *,
    namespace: str,
    image: str,
    required: bool,
    budget_s: float = DEFAULT_BUDGET_S,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> CniCheckResult:
    """Run the probes and return the facts with the verdict of ``cni_check_outcome``.

    The probe objects are removed before returning, whatever happens.
    """
    deadline = monotonic() + budget_s
    allowed_reachable = False
    denied_blocked = False
    timed_out = False
    # Remove leftovers of an earlier run first (a hook can be re-run), then create.
    _cleanup(api, namespace)
    try:
        deny, allow = build_network_policies()
        # Policies first, so no probe is ever reachable before it is isolated.
        api.create_network_policy(namespace, deny)
        api.create_network_policy(namespace, allow)
        api.create_pod(namespace, build_probe_pod(ALLOWED_PROBE, "allowed", image))
        api.create_pod(namespace, build_probe_pod(DENIED_PROBE, "denied", image))

        allowed_ip = _wait_ready(api, namespace, ALLOWED_PROBE, deadline, monotonic, sleep)
        denied_ip = _wait_ready(api, namespace, DENIED_PROBE, deadline, monotonic, sleep)
        if allowed_ip is None or denied_ip is None:
            _LOG.error("probe pods were not Ready within the budget")
            timed_out = True
        else:
            # The allowed probe must become reachable: this also gives the CNI time to program
            # the policies. Then the denied probe must refuse, within the same budget.
            allowed_reachable = _poll(
                lambda: api.can_connect(allowed_ip, PROBE_PORT, CONNECT_TIMEOUT_S),
                deadline,
                monotonic,
                sleep,
            )
            if not allowed_reachable:
                timed_out = True
            else:
                # Reachable to the end means enforcement absent, not a timeout.
                denied_blocked = _poll(
                    lambda: not api.can_connect(denied_ip, PROBE_PORT, CONNECT_TIMEOUT_S),
                    deadline,
                    monotonic,
                    sleep,
                )
    except CniCheckError as exc:
        _LOG.error("CNI check could not run: %s", exc)
        timed_out = True
    finally:
        _cleanup(api, namespace)

    outcome = cni_check_outcome(allowed_reachable, denied_blocked, timed_out, required)
    return CniCheckResult(outcome, allowed_reachable, denied_blocked, timed_out)


# ---------------------------------------------------------------------- real adapter


class KubernetesCniCheckApi:
    """:class:`CniCheckApi` over ``kubernetes.client`` (CoreV1Api and NetworkingV1Api)."""

    def __init__(
        self, core_v1: Any, networking_v1: Any, *, request_timeout_s: float = 30.0
    ) -> None:
        self._core = core_v1
        self._net = networking_v1
        self._timeout = request_timeout_s

    def create_pod(self, namespace: str, manifest: Mapping[str, object]) -> None:
        try:
            self._core.create_namespaced_pod(
                namespace, dict(manifest), _request_timeout=self._timeout
            )
        except Exception as exc:
            raise CniCheckError(f"create pod failed: {_describe(exc)}") from None

    def create_network_policy(self, namespace: str, manifest: Mapping[str, object]) -> None:
        try:
            self._net.create_namespaced_network_policy(
                namespace, dict(manifest), _request_timeout=self._timeout
            )
        except Exception as exc:
            raise CniCheckError(f"create NetworkPolicy failed: {_describe(exc)}") from None

    def read_pod(self, namespace: str, name: str) -> ProbePod | None:
        try:
            pod = self._core.read_namespaced_pod(name, namespace, _request_timeout=self._timeout)
        except Exception as exc:
            if getattr(exc, "status", None) == 404:
                return None
            raise CniCheckError(f"read pod failed: {_describe(exc)}") from None
        conditions = {c.type: c.status for c in (pod.status.conditions or [])}
        return ProbePod(name, conditions.get("Ready") == "True", pod.status.pod_ip)

    def delete_pod(self, namespace: str, name: str) -> None:
        try:
            self._core.delete_namespaced_pod(
                name, namespace, grace_period_seconds=0, _request_timeout=self._timeout
            )
        except Exception as exc:
            if getattr(exc, "status", None) != 404:
                raise

    def delete_network_policy(self, namespace: str, name: str) -> None:
        try:
            self._net.delete_namespaced_network_policy(
                name, namespace, _request_timeout=self._timeout
            )
        except Exception as exc:
            if getattr(exc, "status", None) != 404:
                raise

    def can_connect(self, ip: str, port: int, timeout_s: float) -> bool:
        try:
            with socket.create_connection((ip, port), timeout=timeout_s):
                return True
        except OSError:
            return False


def _describe(exc: BaseException) -> str:
    """Status and reason of an API failure, without the response body."""
    status = getattr(exc, "status", None)
    reason = getattr(exc, "reason", None)
    return f"{status} {reason}" if status else type(exc).__name__


def build_kubernetes_cni_check_api() -> KubernetesCniCheckApi:
    """Build the adapter from the in-cluster ServiceAccount credentials."""
    from kubernetes import client, config

    configuration = client.Configuration()
    config.load_incluster_config(client_configuration=configuration)  # type: ignore[no-untyped-call]
    api_client = client.ApiClient(configuration)
    return KubernetesCniCheckApi(client.CoreV1Api(api_client), client.NetworkingV1Api(api_client))


# ---------------------------------------------------------------------- entry point


def _env_bool(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes"}


def main(
    argv: list[str] | None = None,
    *,
    api_factory: Callable[[], CniCheckApi] = build_kubernetes_cni_check_api,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Run the check; return the process exit status."""
    parser = argparse.ArgumentParser(
        prog="python -m edu_it_infrastructure.cni_check",
        description="Check that the cluster CNI enforces NetworkPolicy.",
    )
    parser.add_argument("--namespace", default=os.environ.get("CNI_CHECK_NAMESPACE"))
    parser.add_argument("--image", default=os.environ.get("CNI_CHECK_IMAGE"))
    parser.add_argument(
        "--required",
        choices=("true", "false"),
        default=None,
        help="fail (instead of warn) when enforcement is absent; default: CNI_CHECK_REQUIRED",
    )
    parser.add_argument("--budget", type=float, default=DEFAULT_BUDGET_S)
    args = parser.parse_args(argv)

    logging.basicConfig(
        stream=sys.stderr, level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    if not args.namespace or not args.image:
        _LOG.error(
            "--namespace and --image (or CNI_CHECK_NAMESPACE and CNI_CHECK_IMAGE) are required"
        )
        return EXIT_USAGE
    required = (
        args.required == "true"
        if args.required is not None
        else _env_bool(os.environ.get("CNI_CHECK_REQUIRED"))
    )

    try:
        api = api_factory()
    except Exception as exc:
        # Cannot even reach the cluster: counts as "did not complete" (Req 11.12).
        _LOG.error("cluster client could not be built: %s", type(exc).__name__)
        outcome = cni_check_outcome(False, False, True, required)
    else:
        result = run_check(
            api,
            namespace=args.namespace,
            image=args.image,
            required=required,
            budget_s=args.budget,
            monotonic=monotonic,
            sleep=sleep,
        )
        _LOG.info(
            "allowed_reachable=%s denied_blocked=%s timed_out=%s outcome=%s",
            result.allowed_reachable,
            result.denied_blocked,
            result.timed_out,
            result.outcome.value,
        )
        outcome = result.outcome

    if outcome is CniOutcome.FAIL:
        _LOG.error("%s", ENFORCEMENT_ABSENT_MESSAGE)
        return EXIT_ENFORCEMENT_ABSENT
    if outcome is CniOutcome.WARN:
        _LOG.warning("%s; the rendered NetworkPolicies have no effect", ENFORCEMENT_ABSENT_MESSAGE)
        return EXIT_OK
    _LOG.info("NetworkPolicy enforcement confirmed")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
