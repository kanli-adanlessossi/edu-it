"""Real-API contract test of ``K8sApiPort`` (kubernetes-deployment task 6.22, risk R2).

Each scenario is written once, against the port and an ``Env`` that hides only how a cluster is
set up (quota, waiting). It returns plain observations: exception type, HTTP status, the
``classify_create_rejection`` class, the ``map_api_exception`` sandbox error, and the final pod
set. The same scenario runs on ``FakeK8sApi`` (always) and on a kind API server through
``KubectlK8sApi`` (``kind`` marker; skipped when there is no cluster). Both must equal the one
``expected`` value of the scenario, so any drift of the fake from the real API server fails here.
"""

from __future__ import annotations

import json
import subprocess
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Protocol

import pytest
from edu_it_infrastructure.fakes.clock import Test_Clock
from edu_it_infrastructure.fakes.k8s_api import FakeK8sApi
from edu_it_infrastructure.sandbox.k8s_api import K8sApiError, K8sApiPort, K8sStreamClosed
from edu_it_infrastructure.sandbox.k8s_errors import classify_create_rejection, map_api_exception

from tests.k8s.kubectl_api import KubectlK8sApi, parse_kubectl_error, pod_view_from_json

NS = "labs"
RUN_LABEL = "edu-it.io/contract-run"
IMAGE_ENV = "EDU_IT_KIND_IMAGE"
DEFAULT_IMAGE = "busybox:1.36.1"
MAIN = "main"
WAIT_S = 90.0


class Env(Protocol):
    api: K8sApiPort
    namespace: str
    run: str

    def name(self, role: str) -> str: ...
    def manifest(self, role: str, **spec: Any) -> dict[str, Any]: ...
    def zero_quota(self) -> None: ...
    def wait_for(self, predicate: Callable[[], bool], what: str) -> None: ...
    def roles(self) -> list[str]: ...
    def cleanup(self) -> None: ...


class _BaseEnv:
    namespace = NS
    image = DEFAULT_IMAGE

    def __init__(self) -> None:
        self.run = uuid.uuid4().hex[:8]

    def name(self, role: str) -> str:
        return f"contract-{self.run}-{role}"

    def manifest(self, role: str, **spec: Any) -> dict[str, Any]:
        """A pod that satisfies the ``restricted`` Pod Security level; ``spec`` overrides it."""
        container: dict[str, Any] = {
            "name": MAIN,
            "image": self.image,
            "command": ["sleep", "3600"],
            "securityContext": {
                "allowPrivilegeEscalation": False,
                "capabilities": {"drop": ["ALL"]},
                "runAsNonRoot": True,
                "runAsUser": 10000,
                "seccompProfile": {"type": "RuntimeDefault"},
            },
        }
        privileged = spec.pop("privileged", False)
        if privileged:
            # ``privileged`` with ``allowPrivilegeEscalation: false`` fails field validation (422)
            # before PodSecurity admission runs, so escalation is allowed to reach the PSA 403.
            container["securityContext"]["privileged"] = True
            container["securityContext"]["allowPrivilegeEscalation"] = True
        pod_spec = {"containers": [container], "terminationGracePeriodSeconds": 1, **spec}
        return {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {"name": self.name(role), "labels": {RUN_LABEL: self.run}},
            "spec": pod_spec,
        }

    def roles(self) -> list[str]:
        prefix = f"contract-{self.run}-"
        pods = self.api.list_pods(self.namespace, f"{RUN_LABEL}={self.run}")
        return sorted(p.name.removeprefix(prefix) for p in pods)

    api: K8sApiPort


class FakeEnv(_BaseEnv):
    def __init__(self) -> None:
        super().__init__()
        self.clock = Test_Clock()
        self.fake = FakeK8sApi(self.clock, runtime_classes=["gvisor"])
        self.api = self.fake

    def zero_quota(self) -> None:
        self.fake.set_quota(self.namespace, {"pods": 0})

    def wait_for(self, predicate: Callable[[], bool], what: str) -> None:
        for _ in range(int(WAIT_S)):
            if predicate():
                return
            self.clock.advance(timedelta(seconds=1))
        raise AssertionError(f"timed out waiting for {what}")

    def cleanup(self) -> None:
        return None


class KindEnv(_BaseEnv):
    QUOTA = "contract-quota"

    def __init__(self, api: K8sApiPort, context: str, image: str) -> None:
        super().__init__()
        self.api = api
        self.context = context
        self.image = image

    def _kubectl(self, *args: str, stdin: dict[str, Any] | None = None) -> str:
        done = subprocess.run(  # noqa: S603 - fixed argv, no shell
            ["kubectl", "--context", self.context, *args],
            input=json.dumps(stdin).encode() if stdin is not None else None,
            capture_output=True,
            timeout=120,
            check=False,
        )
        assert done.returncode == 0, done.stderr.decode("utf-8", "replace")
        return done.stdout.decode("utf-8", "replace")

    def zero_quota(self) -> None:
        quota = {
            "apiVersion": "v1",
            "kind": "ResourceQuota",
            "metadata": {"name": self.QUOTA},
            "spec": {"hard": {"pods": "0"}},
        }
        self._kubectl("apply", "-n", self.namespace, "-f", "-", stdin=quota)

        def populated() -> bool:
            out = self._kubectl(
                "get", "resourcequota", self.QUOTA, "-n", self.namespace,
                "-o", "jsonpath={.status.hard.pods}",
            )  # fmt: skip
            return out.strip() == "0"

        self.wait_for(populated, "the quota status to be populated")

    def wait_for(self, predicate: Callable[[], bool], what: str) -> None:
        deadline = time.monotonic() + WAIT_S
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.5)
        raise AssertionError(f"timed out waiting for {what}")

    def cleanup(self) -> None:
        subprocess.run(  # noqa: S603 - fixed argv, no shell
            ["kubectl", "--context", self.context, "-n", self.namespace, "delete",
             "resourcequota", self.QUOTA, "--ignore-not-found"],
            capture_output=True, timeout=60, check=False,
        )  # fmt: skip
        subprocess.run(  # noqa: S603 - fixed argv, no shell
            ["kubectl", "--context", self.context, "-n", self.namespace, "delete", "pods",
             "-l", f"{RUN_LABEL}={self.run}", "--grace-period=0", "--force", "--wait=false"],
            capture_output=True, timeout=120, check=False,
        )  # fmt: skip


# ---------------------------------------------------------------------- observations


def failure(exc: BaseException) -> tuple[Any, ...]:
    """What the provider derives from an API failure: type, status, class, sandbox error."""
    status = getattr(exc, "status", None)
    reason = getattr(exc, "reason", None)
    message = getattr(exc, "message", None)
    return (
        type(exc).__name__,
        status,
        classify_create_rejection(status, reason, message).value,
        type(map_api_exception(exc)).__name__,
    )


def attempt(call: Callable[[], object]) -> tuple[Any, ...]:
    try:
        call()
    except Exception as exc:  # noqa: BLE001 - the observation is the exception
        return ("raised", *failure(exc))
    return ("ok",)


def running(env: Env, role: str) -> None:
    env.wait_for(lambda: env.api.read_pod(env.namespace, env.name(role)).phase == "Running", role)


# ---------------------------------------------------------------------- scenarios


def create_accepted(env: Env) -> list[Any]:
    created = env.api.create_pod(env.namespace, env.manifest("a"))
    seen = [
        ("created", created.name == env.name("a"), created.namespace, created.phase),
        ("label", created.labels.get(RUN_LABEL) == env.run),
    ]
    running(env, "a")
    view = env.api.read_pod(env.namespace, env.name("a"))
    seen.append(("running", view.phase, view.unschedulable, view.deletion_timestamp is None))
    return [*seen, ("pods", env.roles())]


def already_exists_adopt(env: Env) -> list[Any]:
    first = env.api.create_pod(env.namespace, env.manifest("a"))
    second = attempt(lambda: env.api.create_pod(env.namespace, env.manifest("a")))
    adopted = env.api.read_pod(env.namespace, env.name("a"))
    return [
        second,
        ("adopted", adopted.name == first.name, adopted.labels.get(RUN_LABEL) == env.run),
        ("pods", env.roles()),
    ]


def quota_forbidden(env: Env) -> list[Any]:
    env.zero_quota()
    return [
        attempt(lambda: env.api.create_pod(env.namespace, env.manifest("a"))),
        ("pods", env.roles()),
    ]


def pod_security_forbidden(env: Env) -> list[Any]:
    outcome = attempt(lambda: env.api.create_pod(env.namespace, env.manifest("a", privileged=True)))
    return [outcome, ("pods", env.roles())]


def runtime_class_missing(env: Env) -> list[Any]:
    outcome = attempt(
        lambda: env.api.create_pod(
            env.namespace, env.manifest("a", runtimeClassName="contract-missing-runtime-class")
        )
    )
    return [outcome, ("pods", env.roles())]


def pending_unschedulable(env: Env) -> list[Any]:
    env.api.create_pod(
        env.namespace, env.manifest("a", nodeSelector={"edu-it.io/contract": "no-such-node"})
    )
    env.wait_for(
        lambda: env.api.read_pod(env.namespace, env.name("a")).unschedulable, "Unschedulable"
    )
    view = env.api.read_pod(env.namespace, env.name("a"))
    return [
        ("pending", view.phase, view.unschedulable, view.reason, view.node_name),
        ("pods", env.roles()),
    ]


def delete_with_delay(env: Env) -> list[Any]:
    env.api.create_pod(env.namespace, env.manifest("a"))
    running(env, "a")
    env.api.delete_pod(env.namespace, env.name("a"))
    terminating = env.api.read_pod(env.namespace, env.name("a"))
    again = attempt(lambda: env.api.delete_pod(env.namespace, env.name("a")))
    env.wait_for(lambda: env.roles() == [], "the pod to disappear")
    gone = attempt(lambda: env.api.read_pod(env.namespace, env.name("a")))
    return [
        ("terminating", terminating.deletion_timestamp is not None),
        ("delete again", again),
        ("read after", gone),
        ("pods", env.roles()),
    ]


def exec_stream_close(env: Env) -> list[Any]:
    env.api.create_pod(env.namespace, env.manifest("a"))
    running(env, "a")
    name = env.name("a")
    ran = env.api.exec_command(env.namespace, name, MAIN, ["true"], timeout_s=30)
    stream = env.api.open_terminal(env.namespace, name, MAIN, ["sh"])
    env.api.delete_pod(env.namespace, name)
    closed: list[Any] = []

    def stream_closed() -> bool:
        try:
            stream.read(1024, 0.5)
        except K8sStreamClosed as exc:
            closed.append(failure(exc))
            return True
        return False

    try:
        env.wait_for(stream_closed, "the terminal stream to close")
    finally:
        stream.close()
    env.wait_for(lambda: env.roles() == [], "the pod to disappear")
    after = attempt(lambda: env.api.exec_command(env.namespace, name, MAIN, ["true"]))
    return [
        ("exec", ran.exit_code),
        ("stream", *closed[0]),
        ("exec after", after),
        ("pods", env.roles()),
    ]


def gone_pod_404(env: Env) -> list[Any]:
    name = env.name("never-created")
    ns, api = env.namespace, env.api
    return [
        ("read", attempt(lambda: api.read_pod(ns, name))),
        ("delete", attempt(lambda: api.delete_pod(ns, name))),
        ("label", attempt(lambda: api.label_pod(ns, name, {"edu-it.io/destroy": "pending"}))),
        ("exec", attempt(lambda: api.exec_command(ns, name, MAIN, ["true"]))),
        ("terminal", attempt(lambda: api.open_terminal(ns, name, MAIN, ["sh"]))),
        ("pods", env.roles()),
    ]


_NOT_FOUND = ("raised", "K8sApiError", 404, "Other", "SandboxGone")


@dataclass(frozen=True)
class Scenario:
    name: str
    run: Callable[[Env], list[Any]]
    expected: Callable[[Env], list[Any]]
    claims: str


SCENARIOS = [
    # Claims: 1.1, 1.5
    Scenario(
        "create_accepted",
        create_accepted,
        lambda e: [
            ("created", True, NS, "Pending"),
            ("label", True),
            ("running", "Running", False, True),
            ("pods", ["a"]),
        ],
        "1.1, 1.5",
    ),
    # Claims: 1.5, 1.9
    Scenario(
        "already_exists_adopt",
        already_exists_adopt,
        lambda e: [
            ("raised", "K8sApiError", 409, "AlreadyExists", "SandboxError"),
            ("adopted", True, True),
            ("pods", ["a"]),
        ],
        "1.5, 1.9",
    ),
    # Claims: 2.11, 22.1
    Scenario(
        "quota_forbidden",
        quota_forbidden,
        lambda e: [("raised", "K8sApiError", 403, "Capacity", "SandboxError"), ("pods", [])],
        "2.11, 22.1",
    ),
    # Claims: 2.12
    Scenario(
        "pod_security_forbidden",
        pod_security_forbidden,
        lambda e: [
            ("raised", "K8sApiError", 403, "PodSecurityViolation", "SandboxError"),
            ("pods", []),
        ],
        "2.12",
    ),
    # Claims: 2.12
    Scenario(
        "runtime_class_missing",
        runtime_class_missing,
        lambda e: [
            ("raised", "K8sApiError", 403, "RuntimeClassMissing", "SandboxError"),
            ("pods", []),
        ],
        "2.12",
    ),
    # Claims: 1.1, 1.5
    Scenario(
        "pending_unschedulable",
        pending_unschedulable,
        lambda e: [("pending", "Pending", True, "Unschedulable", None), ("pods", ["a"])],
        "1.1, 1.5",
    ),
    # Claims: 1.1, 1.5, 1.9
    Scenario(
        "delete_with_delay",
        delete_with_delay,
        lambda e: [
            ("terminating", True),
            ("delete again", ("ok",)),
            ("read after", _NOT_FOUND),
            ("pods", []),
        ],
        "1.1, 1.5, 1.9",
    ),
    # Claims: 1.5, 1.9
    Scenario(
        "exec_stream_close",
        exec_stream_close,
        lambda e: [
            ("exec", 0),
            ("stream", "K8sStreamClosed", None, "Other", "SandboxGone"),
            ("exec after", _NOT_FOUND),
            ("pods", []),
        ],
        "1.5, 1.9",
    ),
    # Claims: 1.5, 1.9
    Scenario(
        "gone_pod_404",
        gone_pod_404,
        lambda e: (
            [(op, _NOT_FOUND) for op in ("read", "delete", "label", "exec", "terminal")]
            + [("pods", [])]
        ),
        "1.5, 1.9",
    ),
]

IDS = [s.name for s in SCENARIOS]


def _check(env: Env, scenario: Scenario) -> None:
    try:
        observed = scenario.run(env)
    finally:
        env.cleanup()
    assert observed == scenario.expected(env), scenario.name


# Claims: 1.5, 2.11, 2.12, 22.1
@pytest.mark.parametrize("scenario", SCENARIOS, ids=IDS)
def test_fake_k8s_api_matches_the_contract(scenario: Scenario) -> None:
    _check(FakeEnv(), scenario)


# Claims: 1.1, 1.5, 1.9, 2.11, 2.12, 22.1
@pytest.mark.kind
@pytest.mark.parametrize("scenario", SCENARIOS, ids=IDS)
def test_kind_api_server_matches_the_contract(
    scenario: Scenario, kind_api: KubectlK8sApi, kind_context: str, kind_labs_namespace: str
) -> None:
    import os

    _check(KindEnv(kind_api, kind_context, os.environ.get(IMAGE_ENV, DEFAULT_IMAGE)), scenario)


# Claims: 1.1, 1.5, 1.9, 2.11, 2.12, 22.1
@pytest.mark.kind
@pytest.mark.parametrize("scenario", SCENARIOS, ids=IDS)
def test_production_client_matches_the_contract_on_kind(
    scenario: Scenario, kind_client_api: K8sApiPort, kind_context: str, kind_labs_namespace: str
) -> None:
    """The same scenarios through ``K8sClientApi``, the official-client adapter the Chart ships."""
    import os

    env = KindEnv(kind_client_api, kind_context, os.environ.get(IMAGE_ENV, DEFAULT_IMAGE))
    _check(env, scenario)


# ---------------------------------------------------------------------- offline adapter checks


# Claims: 1.5
def test_scenarios_cover_the_required_list() -> None:
    assert IDS == [
        "create_accepted",
        "already_exists_adopt",
        "quota_forbidden",
        "pod_security_forbidden",
        "runtime_class_missing",
        "pending_unschedulable",
        "delete_with_delay",
        "exec_stream_close",
        "gone_pod_404",
    ]


# Claims: 2.11, 22.1
def test_kubectl_error_text_becomes_the_port_exception() -> None:
    err = parse_kubectl_error(
        'Error from server (Forbidden): error when creating "STDIN": pods "p" is forbidden: '
        "exceeded quota: q, requested: pods=1, used: pods=0, limited: pods=0"
    )
    assert isinstance(err, K8sApiError)
    assert failure(err) == ("K8sApiError", 403, "Capacity", "SandboxError")
    gone = parse_kubectl_error('Error from server (NotFound): pods "p" not found')
    assert failure(gone) == ("K8sApiError", 404, "Other", "SandboxGone")  # type: ignore[arg-type]
    down = parse_kubectl_error("Unable to connect to the server: dial tcp: connection refused")
    assert isinstance(down, ConnectionError)
    assert parse_kubectl_error("command terminated with exit code 3") is None


# Claims: 1.5
def test_pod_json_maps_to_a_pod_view() -> None:
    view = pod_view_from_json(
        {
            "metadata": {"name": "p", "namespace": NS, "creationTimestamp": "2025-01-01T00:00:00Z"},
            "spec": {"containers": [{"name": MAIN}]},
            "status": {
                "phase": "Pending",
                "conditions": [
                    {"type": "PodScheduled", "status": "False", "reason": "Unschedulable",
                     "message": "0/3 nodes are available"},
                ],
            },
        }
    )  # fmt: skip
    assert (view.phase, view.unschedulable, view.reason, view.container_names) == (
        "Pending",
        True,
        "Unschedulable",
        (MAIN,),
    )
