"""Failure scenario: Api or Worker pod restart during active sessions, on kind (task 14.6).

Requirement 20 against the real kind API server. Two layers, both marked ``kind`` (skipped with the
reason when no cluster answers, see ``tests/k8s/conftest.py``; nothing here has been run on a
cluster unless a report says so):

* Application behavior over real Lab_Pods (``failure_support.LabEnv``). A restarted Api or Worker
  pod is modelled as what it is: a process with no memory that rebuilds its use cases from the
  persisted session state and the cluster. ``TerminalGatewayCore`` objects (Api) and
  ``ProvisionSandbox``/``ReapSessions`` objects (Worker) are discarded and rebuilt, while
  the Lab_Pod keeps running in the cluster.
* Platform pods of an installed Chart (release in the ``platform`` namespace, see
  ``test_smoke_kind.py``): the Api and Worker pods are really killed and must come back, the
  PodDisruptionBudgets must hold, and a restarted Worker must resume the Reaper. These tests skip
  with a reason when the Chart is not installed.

The Api WebSocket itself (HTTP upgrade, cookies) is covered by the application and web tests; here
the gateway core is exercised over a recording socket.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable, Iterator
from datetime import timedelta
from typing import Any

import pytest
from edu_it_application.labs import (
    AuthorizeTerminal,
    Frame,
    ProvisionOutcome,
    TerminalAuthorization,
    TerminalRefusal,
)
from edu_it_application.labs.terminal import CLOSE_DRAIN, CLOSE_SESSION_REFUSED
from edu_it_domain import config
from edu_it_domain.labs.k8s_rules import reconnect_delays
from edu_it_domain.labs.models import GradingRun, RunStatus, SessionStatus
from edu_it_domain.labs.sandbox_spec import LEARNER_USER, build_sandbox_spec
from edu_it_infrastructure.fakes import In_Memory_SessionStore
from edu_it_infrastructure.sandbox.k8s_api import K8sApiPort

from tests.k8s.conftest import LABS_NAMESPACE
from tests.k8s.failure_support import (
    LAB_IMAGE,
    MEASURE_SLACK_S,
    PLATFORM_NAMESPACE,
    LabEnv,
    RecordingSocket,
    kubectl,
    kubectl_json,
    lab_env,
    pod_exists,
    pods_of_session,
    unschedulable_managed_pod,
    wait_until,
)

pytestmark = pytest.mark.kind

REATTACH_BOUND_S = 5.0  # Req 20.1: re-attach within 5 s of a successful reconnect
REAPER_RESUME_BOUND_S = 60.0  # Req 20.7: within 60 s of the Worker becoming ready
RECONNECT_WINDOW_S = 60.0  # Req 20.1, 20.2
MAX_RETRY_GAP_S = 5.0  # Req 20.1
ROLLOUT_S = 180.0


@pytest.fixture
def env(kind_context: str, kind_client_api: K8sApiPort) -> Iterator[LabEnv]:
    with lab_env(kind_context, kind_client_api) as lab:
        yield lab


# --------------------------------------------------------------------------- helpers


async def _until(predicate: Callable[[], bool], timeout_s: float, what: str) -> float:
    deadline = time.monotonic() + timeout_s
    while True:
        if predicate():
            return time.monotonic()
        if time.monotonic() >= deadline:
            raise AssertionError(f"timed out after {timeout_s:.0f}s waiting for {what}")
        await asyncio.sleep(0.1)


async def _attach(gateway: Any, sid: str, user: str = "u1") -> tuple[RecordingSocket, Any, float]:
    """Connect a terminal; return the socket, the gateway task and the seconds to first output."""

    sock = RecordingSocket()
    started = time.monotonic()
    task = asyncio.create_task(gateway.run(TerminalAuthorization(sid, user), sock))
    await sock.inbox.put(Frame(data=b"echo reattach-$((6*7))\n"))
    reached = await _until(lambda: b"reattach-42" in sock.output(), 30, "terminal output")
    return sock, task, reached - started


def _pod_uid(context: str, sid: str) -> str:
    pods = pods_of_session(context, sid)
    assert len(pods) == 1, f"expected exactly one Lab_Pod for {sid}, found {len(pods)}"
    assert pods[0]["status"]["phase"] == "Running"
    assert "deletionTimestamp" not in pods[0]["metadata"]
    return pods[0]["metadata"]["uid"]


def _session_store(env: LabEnv) -> In_Memory_SessionStore:
    store = In_Memory_SessionStore(env.clock)
    for user in ("u1", "u2"):
        store.create(f"ref-{user}", user, expires_at=env.clock.now() + timedelta(days=1))
    return store


# --------------------------------------------------------------------------- Api restart


# Claims: 20.1, 20.2
def test_restart_schedule_retries_at_most_5s_apart_for_60s() -> None:
    delays = [d.total_seconds() for d in reconnect_delays("restart")]
    assert delays and all(d <= MAX_RETRY_GAP_S for d in delays)
    # The schedule stops just before the running total would exceed the 60 s window, so the sum
    # is the time of the last attempt (<= 60). The window is covered because one more gap (at most
    # 5 s) would carry past 60: the client keeps retrying at <=5 s intervals through to 60 s.
    assert sum(delays) <= RECONNECT_WINDOW_S
    assert sum(delays) + MAX_RETRY_GAP_S >= RECONNECT_WINDOW_S


# Claims: 20.1, 20.3
def test_api_killed_without_a_drain_leaves_the_lab_pod_running_and_it_reattaches(
    kind_context: str, env: LabEnv
) -> None:
    async def scenario() -> None:
        sid = env.ready_session()
        uid = _pod_uid(kind_context, sid)
        _, task, _ = await _attach(env.gateway(), sid)

        # The Api pod is killed: no drain, no close frame, its gateway object is simply gone.
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        # The Reaper (a Worker concern) must not touch an active session's pod (Req 20.3).
        report = env.reaper().execute()
        assert report.destroyed == 0 and report.expired == 0 and report.infra_lost == 0
        assert env.get(sid).status is SessionStatus.READY
        assert _pod_uid(kind_context, sid) == uid

        # A new Api pod: new gateway, no memory. The browser reconnects, the terminal re-attaches.
        _, task2, seconds = await _attach(env.gateway(), sid)
        assert seconds <= REATTACH_BOUND_S + MEASURE_SLACK_S
        task2.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task2
        assert _pod_uid(kind_context, sid) == uid

    asyncio.run(scenario())


# Claims: 20.1, 20.2, 14.5
def test_api_restarted_with_a_drain_sends_reconnect_and_the_terminal_reattaches(
    kind_context: str, env: LabEnv
) -> None:
    async def scenario() -> None:
        sid = env.ready_session()
        uid = _pod_uid(kind_context, sid)
        old_gateway = env.gateway()
        sock, task, _ = await _attach(old_gateway, sid)

        # SIGTERM on the Api pod: stop accepting, tell every terminal to reconnect, close.
        old_gateway.registry.stop_accepting()
        await old_gateway.registry.drain()
        await asyncio.wait_for(task, 10)
        assert sock.closed is not None and sock.closed[0] == CLOSE_DRAIN
        assert any(t.get("type") == "reconnect" for t in sock.texts())
        assert env.get(sid).status is SessionStatus.READY  # a drain is not a session end
        assert _pod_uid(kind_context, sid) == uid  # the Lab_Pod survived the Api restart

        _, task2, seconds = await _attach(env.gateway(), sid)
        assert seconds <= REATTACH_BOUND_S + MEASURE_SLACK_S
        task2.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task2

    asyncio.run(scenario())


# Claims: 20.4
def test_failed_ownership_check_on_reconnect_refuses_and_leaves_the_pod_unchanged(
    kind_context: str, env: LabEnv
) -> None:
    async def scenario() -> None:
        sid = env.ready_session()
        uid = _pod_uid(kind_context, sid)
        auth = AuthorizeTerminal(
            sessions=env.sessions, session_store=_session_store(env), clock=env.clock
        )
        origin = config.LAB_ALLOWED_WEB_ORIGINS[0]
        owner = auth.execute(session_ref="ref-u1", origin=origin, session_id=sid)
        assert owner == TerminalAuthorization(sid, "u1")
        stranger = auth.execute(session_ref="ref-u2", origin=origin, session_id=sid)
        assert isinstance(stranger, TerminalRefusal) and stranger.code == CLOSE_SESSION_REFUSED

        # Even if a stranger's socket reached the gateway, it is closed before any attach.
        sock = RecordingSocket()
        code = await env.gateway().run(TerminalAuthorization(sid, "u2"), sock)
        assert code == CLOSE_SESSION_REFUSED
        assert sock.closed is not None and sock.sent == []

        assert env.get(sid).status is SessionStatus.READY
        assert _pod_uid(kind_context, sid) == uid

    asyncio.run(scenario())


# --------------------------------------------------------------------------- Worker restart


# Claims: 20.5
def test_worker_killed_mid_provisioning_reruns_and_exactly_one_pod_exists(
    kind_context: str, env: LabEnv
) -> None:
    sid = env.add_provisioning()
    session = env.get(sid)

    # The first Worker claimed the attempt and created the pod, then died before the session row
    # was updated: the row is still ``provisioning`` with no sandbox_ref.
    assert env.sessions.claim_provision_attempt(
        sid, expected_attempts=session.provision_attempts, now=env.clock.now()
    )

    class _Lab:
        image = LAB_IMAGE

    first = env.sandbox.provision(build_sandbox_spec(_Lab(), sid))
    first_uid = _pod_uid(kind_context, sid)
    assert env.get(sid).status is SessionStatus.PROVISIONING

    # The restarted Worker redelivers the job: same deterministic name, so it adopts the pod.
    outcome = env.provisioner().execute(sid)

    assert outcome is ProvisionOutcome.READY
    assert env.get(sid).status is SessionStatus.READY
    assert env.get(sid).sandbox_ref == first.sandbox_id
    assert _pod_uid(kind_context, sid) == first_uid  # adopted, not recreated
    assert len(pods_of_session(kind_context, sid)) == 1


# Claims: 20.6
def test_worker_killed_during_grading_records_failed_run_and_removes_the_bundle(
    kind_context: str, env: LabEnv
) -> None:
    sid = env.ready_session()
    handle = env.handle(sid)
    uid = _pod_uid(kind_context, sid)

    # Mid-grading state: a bundle copied into /grader, the learner's processes frozen, the run
    # ``running`` and the session ``grading``. The Worker then dies.
    def exec_as(user: str, *argv: str, timeout_s: float | None = 20) -> Any:
        return env.sandbox.exec(handle, list(argv), as_user=user, env={}, cwd="/", timeout_s=timeout_s)

    assert exec_as("root", "/bin/sh", "-c", "echo secret > /grader/bundle-marker").exit_code == 0
    # Detach the background sleep's fds; otherwise it holds the exec's stdout open and the
    # provider's timeout wrapper waits on it until the backstop (exit 137), as seen in the
    # isolation suite and lab-images/README.md.
    assert (
        exec_as(
            LEARNER_USER, "/bin/bash", "-c", "sleep 600 >/dev/null 2>&1 </dev/null & disown; echo started"
        ).exit_code
        == 0
    )
    # Freeze without timeout_s: kill -1 also signals the provider's own timeout wrapper, so with
    # a timeout the exec stops itself and hangs (lab-images/README.md). GradeRun sends it so too.
    assert exec_as(LEARNER_USER, "/bin/kill", "-s", "STOP", "-1", timeout_s=None).exit_code in (0, 1)
    env.sessions.transition(sid, expected={SessionStatus.READY}, to=SessionStatus.GRADING)
    now = env.clock.now()
    env.runs.add(GradingRun(f"run-{sid}", sid, RunStatus.RUNNING, now, now + timedelta(seconds=15)))

    # The new Worker pod starts: it closes interrupted runs before consuming any job.
    closed = env.reaper().close_interrupted_runs()

    assert closed == 1
    run = env.runs.get(f"run-{sid}")
    assert run is not None
    assert run.status is RunStatus.COULD_NOT_COMPLETE  # translated "could not complete", no output
    assert run.results == ()
    assert env.get(sid).status is SessionStatus.READY  # not re-run; the learner may grade again
    assert env.mastery.get("u1", "c1") == env.mastery_before
    assert _pod_uid(kind_context, sid) == uid
    # Req 20.6: the grading bundle is removed from the Lab_Pod.
    listing = exec_as("root", "/bin/sh", "-c", "ls -A /grader")
    assert listing.exit_code == 0 and listing.stdout.strip() == b"", listing.stdout
    # The learner was thawed: no process of the learner is left stopped.
    states = exec_as(LEARNER_USER, "/bin/ps", "-e", "-o", "stat=")
    assert not [s for s in states.stdout.decode().split() if s.startswith("T")]


# Claims: 20.7, 20.3
def test_restarted_worker_resumes_the_reaper_from_persisted_state_only(
    kind_context: str, kind_labs_namespace: str, env: LabEnv
) -> None:
    healthy = env.ready_session("u1")
    doomed = env.ready_session("u2")
    healthy_uid = _pod_uid(kind_context, healthy)
    doomed_pod = env.pod_name(doomed)

    # The pod of ``doomed`` vanishes while no Worker is running (the old one is dead).
    kubectl(kind_context, "delete", "pod", doomed_pod, "-n", LABS_NAMESPACE, "--wait=true")

    # New Worker: nothing in memory, only the persisted sessions and the provider listing.
    started = time.monotonic()
    env.start_worker_jobs()
    resumed_at = wait_until(
        lambda: env.get(doomed).status is SessionStatus.ENDED_BY_INFRASTRUCTURE,
        REAPER_RESUME_BOUND_S + MEASURE_SLACK_S,
        what="the restarted Worker to end the session whose pod vanished",
    )
    assert resumed_at - started <= REAPER_RESUME_BOUND_S + MEASURE_SLACK_S
    assert env.get(healthy).status is SessionStatus.READY  # an active session is never selected
    assert _pod_uid(kind_context, healthy) == healthy_uid
    assert env.metrics.infra_loss == 1


# --------------------------------------------------------------------------- real Chart pods


def _platform_pods(context: str, component: str) -> list[dict[str, Any]]:
    done = kubectl(
        context,
        "get",
        "pods",
        "-n",
        PLATFORM_NAMESPACE,
        "-l",
        f"app.kubernetes.io/part-of=edu-it,app.kubernetes.io/component={component}",
        "-o",
        "json",
        check=False,
    )
    if done.returncode != 0:
        return []
    return [p for p in json.loads(done.stdout)["items"] if "deletionTimestamp" not in p["metadata"]]


def _ready(pod: dict[str, Any]) -> bool:
    return any(
        c["type"] == "Ready" and c["status"] == "True"
        for c in pod.get("status", {}).get("conditions", [])
    )


def _require_chart(context: str, component: str) -> list[dict[str, Any]]:
    pods = _platform_pods(context, component)
    if not pods:
        pytest.skip(
            f"no {component} pod in namespace {PLATFORM_NAMESPACE}: install the Chart first "
            "(see tests/k8s/test_smoke_kind.py)"
        )
    return pods


def _wait_replaced(
    context: str, component: str, old_uids: set[str]
) -> tuple[float, dict[str, Any]]:
    """Wait for a Ready ``component`` pod not in ``old_uids``; return the time and the pod."""

    found: dict[str, Any] = {}

    def replaced() -> bool:
        for pod in _platform_pods(context, component):
            if pod["metadata"]["uid"] not in old_uids and _ready(pod):
                found.update(pod)
                return True
        return False

    return wait_until(replaced, ROLLOUT_S, interval_s=1, what=f"a new Ready {component} pod"), found


def _kill(context: str, pod: dict[str, Any]) -> None:
    kubectl(
        context,
        "delete",
        "pod",
        pod["metadata"]["name"],
        "-n",
        PLATFORM_NAMESPACE,
        "--grace-period=0",
        "--force",
        "--wait=false",
    )


# Claims: 20.3, 20.1
def test_killed_api_pod_is_replaced_and_lab_pods_are_not_touched(
    kind_context: str, env: LabEnv
) -> None:
    api_pods = _require_chart(kind_context, "api")
    sid = env.ready_session()
    uid = _pod_uid(kind_context, sid)

    old = {p["metadata"]["uid"] for p in api_pods}
    _kill(kind_context, api_pods[0])
    _wait_replaced(kind_context, "api", old)

    assert pod_exists(kind_context, LABS_NAMESPACE, env.pod_name(sid))
    assert _pod_uid(kind_context, sid) == uid


# Claims: 20.7, 20.5, 20.6
def test_killed_worker_pod_is_replaced_and_its_reaper_resumes_within_the_bound(
    kind_context: str, kind_labs_namespace: str, env: LabEnv
) -> None:
    worker_pods = _require_chart(kind_context, "worker")
    old = {p["metadata"]["uid"] for p in worker_pods}
    orphan = "lab-orphan-after-worker-kill"
    kubectl(
        kind_context,
        "delete",
        "pod",
        orphan,
        "-n",
        LABS_NAMESPACE,
        "--ignore-not-found",
        check=False,
    )

    _kill(kind_context, worker_pods[0])
    # An orphan managed pod appears while the new Worker starts. The Chart's Reaper has no session
    # for it, so a Reaper that really resumed from persisted state deletes it.
    kubectl(
        kind_context,
        "apply",
        "-f",
        "-",
        input_text=json.dumps(unschedulable_managed_pod(orphan, "orphan-after-worker-kill")),
    )
    try:
        ready_at, _ = _wait_replaced(kind_context, "worker", old)
        removed_at = wait_until(
            lambda: not pod_exists(kind_context, LABS_NAMESPACE, orphan),
            REAPER_RESUME_BOUND_S + MEASURE_SLACK_S + ROLLOUT_S,
            interval_s=1,
            what="the restarted Worker's Reaper to delete the orphan Lab_Pod",
        )
        assert removed_at - ready_at <= REAPER_RESUME_BOUND_S + MEASURE_SLACK_S
    finally:
        kubectl(
            kind_context,
            "delete",
            "pod",
            orphan,
            "-n",
            LABS_NAMESPACE,
            "--ignore-not-found",
            "--wait=false",
            check=False,
        )


def _evict(context: str, pod: dict[str, Any]) -> bool:
    """Ask the eviction API to remove ``pod``; ``True`` when it was accepted."""

    body = {
        "apiVersion": "policy/v1",
        "kind": "Eviction",
        "metadata": {"name": pod["metadata"]["name"], "namespace": PLATFORM_NAMESPACE},
    }
    done = kubectl(
        context,
        "create",
        "--raw",
        f"/api/v1/namespaces/{PLATFORM_NAMESPACE}/pods/{pod['metadata']['name']}/eviction",
        "-f",
        "-",
        input_text=json.dumps(body),
        check=False,
    )
    return done.returncode == 0


# Claims: 20.8
@pytest.mark.parametrize("component", ["api", "worker"])
def test_pod_disruption_budget_keeps_one_replica_during_voluntary_disruption(
    kind_context: str, component: str
) -> None:
    pods = [p for p in _require_chart(kind_context, component) if _ready(p)]
    budgets = [
        b
        for b in kubectl_json(kind_context, "get", "pdb", "-n", PLATFORM_NAMESPACE)["items"]
        if b["spec"]["selector"]["matchLabels"].get("app.kubernetes.io/component") == component
    ]
    assert len(budgets) == 1, f"the Chart must render one PodDisruptionBudget for {component}"
    assert int(budgets[0]["spec"]["minAvailable"]) >= 1

    accepted = sum(1 for pod in pods if _evict(kind_context, pod))
    try:
        # Evicting every replica at once must be refused for at least one of them.
        assert accepted < len(pods)
    finally:
        _wait_replaced(kind_context, component, set())
