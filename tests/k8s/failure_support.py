"""Shared helpers of the kind failure scenario tests (kubernetes-deployment task 14.6).

The scenarios run the real application code (``ProvisionSandbox``, ``WatchLabPods``,
``ReapSessions``, ``EndByInfrastructure``, ``TerminalGatewayCore``) in the test process, over the
real ``KubernetesSandboxProvider`` and ``KubernetesPodWatcher`` talking to the kind API server.
Session rows live in the in-memory repositories: the point under test is how the application
reacts to what the cluster does to Lab_Pods (node drained, failed, deleted; Api and Worker
restarted), not the PostgreSQL schema, which other suites cover.

Nothing here runs without a cluster: every test module that imports it is marked ``kind`` and is
skipped by ``tests/k8s/conftest.py`` when no kind cluster answers.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from edu_it_application.labs import (
    EndByInfrastructure,
    Frame,
    ProvisionOutcome,
    ProvisionSandbox,
    ReapSessions,
    TerminalGatewayCore,
    WatchLabPods,
)
from edu_it_domain import config
from edu_it_domain.labs.models import LabSession, SessionStatus
from edu_it_domain.learning.models import Mastery, MasteryEvent, MasteryState
from edu_it_domain.ports.lab_catalog import LabDefinition
from edu_it_domain.ports.sandbox import SandboxHandle, SandboxProvider
from edu_it_infrastructure.clock import System_Clock
from edu_it_infrastructure.composition import CompositionSettings, build_sandbox_provider
from edu_it_infrastructure.fakes import Fake_LabCatalog
from edu_it_infrastructure.fakes.lab_metrics import NoopLabMetrics
from edu_it_infrastructure.fakes.lab_repositories import (
    In_Memory_GradingRunRepository,
    In_Memory_LabSessionRepository,
)
from edu_it_infrastructure.fakes.learning_repositories import In_Memory_MasteryRepository
from edu_it_infrastructure.sandbox.k8s_api import K8sApiPort
from edu_it_infrastructure.sandbox.k8s_pod_watcher import KubernetesPodWatcher
from edu_it_infrastructure.sandbox.k8s_provider import LABEL_MANAGED, LABEL_SESSION

from tests.k8s.conftest import LABS_NAMESPACE

LAB_IMAGE = "edu-it/lab-linux-base:dev"
LAB_POOL_LABEL = "edu-it.io/pool=labs"
PLATFORM_NAMESPACE = "platform"
PROVIDER = "kubernetes"

#: Slack on top of a requirement bound for measuring from outside the process (``kubectl``
#: start-up, polling granularity of the test itself). Never applied to the bound itself.
MEASURE_SLACK_S = 3.0

_LAB = LabDefinition(
    lab_id="lab1", kind="terminal", image=LAB_IMAGE, concept_ids=(), tasks=(), checks=()
)


# --------------------------------------------------------------------------- command helpers


def run(
    argv: list[str], *, timeout: float = 180, check: bool = True, input_text: str | None = None
):
    """Run a command without a shell; with ``check`` a failure asserts with its output."""

    done = subprocess.run(
        argv, input=input_text, capture_output=True, text=True, timeout=timeout, check=False
    )
    if check:
        assert done.returncode == 0, (
            f"{' '.join(argv)} failed ({done.returncode}):\n"
            f"{done.stdout[-2000:]}\n{done.stderr[-2000:]}"
        )
    return done


def kubectl(
    context: str,
    *args: str,
    timeout: float = 180,
    check: bool = True,
    input_text: str | None = None,
):
    return run(
        ["kubectl", "--context", context, "--request-timeout=30s", *args],
        timeout=timeout,
        check=check,
        input_text=input_text,
    )


def kubectl_json(context: str, *args: str) -> Any:
    return json.loads(kubectl(context, *args, "-o", "json").stdout)


def pod_exists(context: str, namespace: str, name: str) -> bool:
    done = kubectl(context, "get", "pod", name, "-n", namespace, "-o", "name", check=False)
    return done.returncode == 0 and bool(done.stdout.strip())


def pods_of_session(context: str, session_id: str) -> list[dict[str, Any]]:
    """Every Lab_Pod object (any phase) labelled with ``session_id``."""

    doc = kubectl_json(
        context, "get", "pods", "-n", LABS_NAMESPACE, "-l", f"{LABEL_SESSION}={session_id}"
    )
    return list(doc["items"])


def wait_until(
    predicate: Callable[[], bool], timeout_s: float, *, interval_s: float = 0.5, what: str = ""
) -> float:
    """Poll ``predicate``; return the monotonic time it first held, or fail after ``timeout_s``."""

    deadline = time.monotonic() + timeout_s
    while True:
        if predicate():
            return time.monotonic()
        if time.monotonic() >= deadline:
            raise AssertionError(f"timed out after {timeout_s:.0f}s waiting for {what}")
        time.sleep(interval_s)


def require_disposable_cluster(context: str) -> None:
    """Node disruption is only ever run against a kind cluster (context ``kind-*``)."""

    import pytest

    if not context.startswith("kind-"):
        pytest.skip(f"refusing to disrupt nodes of non-kind context {context}")


# --------------------------------------------------------------------------- application env


class CountingMetrics(NoopLabMetrics):
    """Counts the Lab_Metrics calls the failure scenarios assert on (Req 19.7)."""

    def __init__(self) -> None:
        self.infra_loss = 0
        self.deletions: list[str] = []

    def inc_infrastructure_loss(self) -> None:
        self.infra_loss += 1

    def inc_reaper_deletion(self, *, reason: str) -> None:
        self.deletions.append(reason)


class RecordingSocket:
    """A scripted ``SocketPort`` that timestamps its close."""

    def __init__(self) -> None:
        self.inbox: asyncio.Queue[Frame | None] = asyncio.Queue()
        self.sent: list[Frame] = []
        self.closed: tuple[int, str] | None = None
        self.closed_at: float | None = None

    async def receive(self) -> Frame | None:
        return await self.inbox.get()

    async def send(self, frame: Frame) -> None:
        self.sent.append(frame)
        # Mirror the real web client (apps/web terminal): reply to a server ping with a pong, so
        # the heartbeat watcher does not close a healthy connection while the cluster is still
        # detecting a node loss (which takes far longer than the ping interval).
        if frame.text:
            with suppress(json.JSONDecodeError):
                if json.loads(frame.text).get("type") == "ping":
                    await self.inbox.put(Frame(text=json.dumps({"type": "pong"})))

    async def close(self, code: int, reason: str = "") -> None:
        if self.closed is None:
            self.closed = (code, reason)
            self.closed_at = time.monotonic()

    def texts(self) -> list[dict[str, Any]]:
        return [json.loads(f.text) for f in self.sent if f.text]

    def output(self) -> bytes:
        return b"".join(f.data for f in self.sent if f.data)


def provider_env() -> dict[str, str]:
    import os

    return {
        **os.environ,
        "SANDBOX_PROVIDER": "kubernetes",
        "K8S_LABS_NAMESPACE": LABS_NAMESPACE,
        # kind has no gVisor or Kata: plain runc without a RuntimeClass (dev only, Req 2.12).
        "SANDBOX_RUNTIME_KIND": "runc",
        "SANDBOX_RUNTIME_CLASS": "",
        # The lab node pool of deploy/kind/kind-config.yaml, as in production.
        "SANDBOX_NODE_SELECTOR": LAB_POOL_LABEL,
        "SANDBOX_TOLERATIONS": "edu-it.io/labs=true:NoSchedule",
    }


@dataclass
class LabEnv:
    """The Worker and Api parts of the lab path, wired over the real cluster.

    ``new_worker()`` builds fresh use-case objects over the same persisted state and provider,
    which is exactly what a restarted Worker pod has: nothing in memory, everything persisted.
    """

    context: str
    api: K8sApiPort
    sandbox: SandboxProvider
    clock: System_Clock = field(default_factory=System_Clock)
    runs: In_Memory_GradingRunRepository = field(default_factory=In_Memory_GradingRunRepository)
    metrics: CountingMetrics = field(default_factory=CountingMetrics)
    mastery: In_Memory_MasteryRepository = field(default_factory=In_Memory_MasteryRepository)
    session_ids: list[str] = field(default_factory=list)
    _stop: threading.Event = field(default_factory=threading.Event)
    _threads: list[threading.Thread] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.sessions = In_Memory_LabSessionRepository(self.runs)
        self.mastery.save(
            Mastery(
                user_id="u1",
                concept_id="c1",
                score=62,
                state=MasteryState.PRACTICED,
                events=(MasteryEvent(delta=62, source="quiz:a1", occurred_at=self.clock.now()),),
            )
        )
        self.mastery_before = self.mastery.get("u1", "c1")

    # -- use cases (all built from persisted state only) -----------------------------------

    def end_use_case(self) -> EndByInfrastructure:
        return EndByInfrastructure(sessions=self.sessions, clock=self.clock, metrics=self.metrics)

    def watcher(self, stop: threading.Event | None = None) -> KubernetesPodWatcher:
        return KubernetesPodWatcher(self.api, LABS_NAMESPACE, stop=stop or self._stop)

    def reaper(self) -> ReapSessions:
        return ReapSessions(
            sessions=self.sessions,
            runs=self.runs,
            sandbox=self.sandbox,
            clock=self.clock,
            provider_name=PROVIDER,
            end_by_infrastructure=self.end_use_case(),
            pod_watcher=self.watcher(),
            metrics=self.metrics,
        )

    def provisioner(self) -> ProvisionSandbox:
        return ProvisionSandbox(
            sessions=self.sessions,
            catalog=Fake_LabCatalog([_LAB]),
            sandbox=self.sandbox,
            clock=self.clock,
        )

    def gateway(self) -> TerminalGatewayCore:
        return TerminalGatewayCore(
            sessions=self.sessions,
            sandbox=self.sandbox,
            clock=self.clock,
            provider_name=PROVIDER,
        )

    # -- sessions ---------------------------------------------------------------------------

    def add_provisioning(self, user: str = "u1") -> str:
        sid = f"fail-{uuid.uuid4().hex[:10]}"
        now = self.clock.now()
        self.sessions.add_if_no_active(
            LabSession(
                stable_id=sid,
                user_id=user,
                lab_id="lab1",
                status=SessionStatus.PROVISIONING,
                created_at=now,
                ttl_at=now + config.LAB_SESSION_TTL,
            )
        )
        self.session_ids.append(sid)
        return sid

    def ready_session(self, user: str = "u1") -> str:
        sid = self.add_provisioning(user)
        outcome = self.provisioner().execute(sid)
        assert outcome is ProvisionOutcome.READY, outcome
        return sid

    def get(self, sid: str) -> LabSession:
        session = self.sessions.get(sid)
        assert session is not None
        return session

    def handle(self, sid: str) -> SandboxHandle:
        ref = self.get(sid).sandbox_ref
        assert ref is not None
        return SandboxHandle(ref, sid, PROVIDER)

    def pod_name(self, sid: str) -> str:
        return self.handle(sid).sandbox_id

    # -- background jobs (the Worker's watch_lab_pods job and reaper schedule) --------------

    def start_worker_jobs(self) -> None:
        """Start ``watch_lab_pods`` and the reaper on ``LAB_REAPER_INTERVAL``."""

        self._stop.clear()
        watch = WatchLabPods(
            watcher=self.watcher(),
            end=self.end_use_case(),
            sandbox=self.sandbox,
            metrics=self.metrics,
            # The Worker composition waits on its stop event between reconnects; so does this.
            sleep=self._stop.wait,
        )
        reaper = self.reaper()

        def reap_loop() -> None:
            while not self._stop.is_set():
                with suppress(Exception):
                    reaper.execute()
                self._stop.wait(config.LAB_REAPER_INTERVAL.total_seconds())

        for target in (lambda: watch.run(self._stop.is_set), reap_loop):
            thread = threading.Thread(target=target, daemon=True)
            thread.start()
            self._threads.append(thread)

    def stop_worker_jobs(self) -> None:
        self._stop.set()
        for thread in self._threads:
            thread.join(timeout=15)
        self._threads.clear()

    def cleanup(self) -> None:
        self.stop_worker_jobs()
        for sid in self.session_ids:
            with suppress(Exception):
                kubectl(
                    self.context,
                    "delete",
                    "pods",
                    "-n",
                    LABS_NAMESPACE,
                    "-l",
                    f"{LABEL_SESSION}={sid}",
                    "--wait=false",
                    "--ignore-not-found",
                    check=False,
                )


@contextmanager
def lab_env(context: str, api: K8sApiPort) -> Iterator[LabEnv]:
    settings = CompositionSettings.from_env(provider_env())
    sandbox = build_sandbox_provider(settings, clock=System_Clock(), k8s_api=api)
    env = LabEnv(context=context, api=api, sandbox=sandbox)
    try:
        yield env
    finally:
        env.cleanup()


def managed_labels(session_id: str) -> dict[str, str]:
    return {LABEL_MANAGED: "1", LABEL_SESSION: session_id}


def unschedulable_managed_pod(name: str, session_id: str) -> dict[str, Any]:
    """A managed, restricted-PSA pod that no node can run.

    It stays ``Pending`` and unscheduled, so no kubelet ever touches its status: a test can set
    the status through the status subresource (an eviction) or use it as an orphan Lab_Pod.
    """

    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": name,
            "namespace": LABS_NAMESPACE,
            "labels": managed_labels(session_id),
        },
        "spec": {
            "restartPolicy": "Never",
            "automountServiceAccountToken": False,
            "nodeSelector": {"edu-it.io/never-schedulable": "true"},
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
                    "command": ["/bin/sleep", "3600"],
                    "securityContext": {
                        "allowPrivilegeEscalation": False,
                        "capabilities": {"drop": ["ALL"]},
                    },
                    "resources": {
                        "requests": {"cpu": "10m", "memory": "16Mi"},
                        "limits": {"cpu": "100m", "memory": "32Mi"},
                    },
                }
            ],
        },
    }


def sec(delta: timedelta) -> float:
    return delta.total_seconds()
