"""Failure scenario: lab node drained, failed or deleted, on kind (kubernetes-deployment task 14.6).

Requirement 19 (and the node side of Requirement 20.3, 20.7), run against the real kind API server
with the real ``KubernetesSandboxProvider``, ``KubernetesPodWatcher``, ``WatchLabPods``,
``ReapSessions``, ``EndByInfrastructure`` and ``TerminalGatewayCore`` (see ``failure_support``).
The tests need the cluster of ``tools/kind_harness.py up`` with the lab node pool
(``edu-it.io/pool=labs``) and the lab image ``edu-it/lab-linux-base:dev`` preloaded. They are marked
``kind``: without a reachable cluster ``tests/k8s/conftest.py`` skips them with the reason, so a run
without a cluster only proves that they collect and skip. Nothing here has been run on a cluster
unless a report says so.

These tests DISRUPT the lab node of the cluster (cordon, drain, container stop, node object
delete) and restore it afterwards. They refuse to run on a context that is not ``kind-*`` and must
not run in parallel with other kind suites that use the lab node.

Bounds checked (from ``domain/config.py``, never re-typed here):
terminal close ``LAB_NODE_LOSS_TERMINAL_CLOSE_BOUND`` (10 s), marking
``LAB_INFRA_LOSS_MARK_BOUND`` (30 s), evicted pod removal ``LAB_EVICTED_POD_REMOVAL_BOUND`` (60 s).
Each is measured from outside the process with a small ``MEASURE_SLACK_S`` for polling granularity.

Scale-down note: the drain wait limit of the Values_File (``labs.drainProtection.waitLimitSeconds``,
300 s) is represented by a short ``kubectl drain --timeout`` so the suite stays fast; the
rendered PodDisruptionBudget is the object under test, not the number.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml
from edu_it_application.labs import Frame, TerminalAuthorization
from edu_it_application.labs.terminal import CLOSE_DRAIN
from edu_it_domain import config
from edu_it_domain.labs.models import LabSession, SessionStatus
from edu_it_infrastructure.sandbox.k8s_api import K8sApiPort

from tests.k8s.conftest import LABS_NAMESPACE
from tests.k8s.failure_support import (
    LAB_POOL_LABEL,
    MEASURE_SLACK_S,
    LabEnv,
    RecordingSocket,
    kubectl,
    lab_env,
    pod_exists,
    require_disposable_cluster,
    run,
    sec,
    unschedulable_managed_pod,
    wait_until,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
CHART = REPO_ROOT / "deploy" / "helm" / "edu-it"

pytestmark = pytest.mark.kind

MARK_BOUND_S = sec(config.LAB_INFRA_LOSS_MARK_BOUND)
CLOSE_BOUND_S = sec(config.LAB_NODE_LOSS_TERMINAL_CLOSE_BOUND)
EVICTED_REMOVAL_BOUND_S = sec(config.LAB_EVICTED_POD_REMOVAL_BOUND)
#: Longest the tests wait for the cluster itself (node controller, PodGC) before giving up.
CLUSTER_REACTION_S = 420.0
NODE_READY_S = 300.0
#: Words that must never reach a learner (Req 19.1: no raw infrastructure details).
FORBIDDEN_WORDS = ("pod", "node", "kube", "evict", "sandbox", "drain", "namespace")


# --------------------------------------------------------------------------- fixtures


@pytest.fixture
def lab_node(kind_context: str) -> Iterator[str]:
    """The lab-pool node, restored (uncordoned, started, registered, Ready) after the test."""

    require_disposable_cluster(kind_context)
    done = kubectl(kind_context, "get", "nodes", "-l", LAB_POOL_LABEL, "-o", "name")
    names = [line.split("/", 1)[1] for line in done.stdout.split() if "/" in line]
    if not names:
        pytest.skip(f"no node carries the label {LAB_POOL_LABEL}")
    node = names[0]
    try:
        yield node
    finally:
        _restore_node(kind_context, node)


def _restore_node(context: str, node: str) -> None:
    if shutil.which("docker") is not None:
        run(["docker", "start", node], check=False, timeout=120)  # no-op when it is running
    wait_until(
        lambda: _node_ready(context, node), NODE_READY_S, interval_s=3, what=f"node {node} Ready"
    )
    kubectl(context, "uncordon", node, check=False)
    # A node that re-registered after its object was deleted must carry the pool label again.
    wait_until(
        lambda: node in _nodes_with_label(context),
        60,
        what=f"node {node} to carry {LAB_POOL_LABEL}",
    )


def _nodes_with_label(context: str) -> list[str]:
    done = kubectl(context, "get", "nodes", "-l", LAB_POOL_LABEL, "-o", "name", check=False)
    return [line.split("/", 1)[1] for line in done.stdout.split() if "/" in line]


def _node_ready(context: str, node: str) -> bool:
    done = kubectl(context, "get", "node", node, "-o", "json", check=False)
    if done.returncode != 0:
        return False
    for cond in json.loads(done.stdout)["status"].get("conditions", []):
        if cond["type"] == "Ready":
            return cond["status"] == "True"
    return False


@pytest.fixture
def env(kind_context: str, kind_client_api: K8sApiPort) -> Iterator[LabEnv]:
    with lab_env(kind_context, kind_client_api) as lab:
        yield lab


# --------------------------------------------------------------------------- scenario helpers


async def _until(predicate: Callable[[], bool], timeout_s: float, what: str) -> float:
    deadline = time.monotonic() + timeout_s
    while True:
        if predicate():
            return time.monotonic()
        if time.monotonic() >= deadline:
            raise AssertionError(f"timed out after {timeout_s:.0f}s waiting for {what}")
        await asyncio.sleep(0.2)


async def _attach_terminal(env: LabEnv, sid: str, user: str = "u1") -> tuple[RecordingSocket, Any]:
    """Run the terminal gateway for ``sid`` and prove the terminal reaches the Lab_Pod."""

    sock = RecordingSocket()
    task = asyncio.create_task(env.gateway().run(TerminalAuthorization(sid, user), sock))
    await sock.inbox.put(Frame(data=b"echo attach-$((6*7))\n"))
    await _until(lambda: b"attach-42" in sock.output(), 30, "terminal output from the Lab_Pod")
    return sock, task


@dataclass(frozen=True)
class Timeline:
    pod_gone_at: float
    marked_at: float
    closed_at: float
    socket: RecordingSocket


async def _loss_timeline(env: LabEnv, sid: str, disrupt: Callable[[], None]) -> Timeline:
    """Open a terminal, run ``disrupt`` (blocking), and time the platform's reaction.

    ``disrupt`` must start the node event; this helper then polls until the Lab_Pod object is
    gone (``pod_gone_at``), the session is marked ended-by-infrastructure (``marked_at``) and the
    terminal WebSocket is closed (``closed_at``). All times are ``time.monotonic()``.
    """

    pod = env.pod_name(sid)
    sock, gateway_task = await _attach_terminal(env, sid)

    def disrupt_and_wait_for_pod_loss() -> float:
        disrupt()
        return wait_until(
            lambda: not pod_exists(env.context, LABS_NAMESPACE, pod),
            CLUSTER_REACTION_S,
            what=f"Lab_Pod {pod} to disappear",
        )

    disruption = asyncio.create_task(asyncio.to_thread(disrupt_and_wait_for_pod_loss))
    marked_at = await _until(
        lambda: env.get(sid).status is SessionStatus.ENDED_BY_INFRASTRUCTURE,
        CLUSTER_REACTION_S + MARK_BOUND_S,
        "the session to be marked ended-by-infrastructure",
    )
    await asyncio.wait_for(gateway_task, CLOSE_BOUND_S + MEASURE_SLACK_S + 30)
    pod_gone_at = await disruption
    assert sock.closed_at is not None
    return Timeline(pod_gone_at, marked_at, sock.closed_at, sock)


def _assert_learner_outcome(env: LabEnv, sid: str, timeline: Timeline) -> None:
    """Req 19.1 to 19.4 and 19.7 for one lost session."""

    # 19.2: marked within 30 s of the loss.
    assert timeline.marked_at - timeline.pod_gone_at <= MARK_BOUND_S + MEASURE_SLACK_S
    # 19.1: terminal closed within 10 s of the platform detecting the loss.
    assert timeline.closed_at - timeline.marked_at <= CLOSE_BOUND_S + MEASURE_SLACK_S
    sock = timeline.socket
    assert sock.closed is not None and sock.closed[0] == CLOSE_DRAIN
    texts = sock.texts()
    assert any(t.get("message_key") == "labs.session_interrupted" for t in texts)
    assert any(t.get("type") == "reconnect" for t in texts)
    shown = (json.dumps(texts) + sock.closed[1]).lower()
    assert not any(word in shown for word in FORBIDDEN_WORDS), shown

    # 19.3: the slot is free at once, a new session starts without a cooldown.
    assert env.sessions.get_active_for_user("u1") is None
    replacement = env.add_provisioning("u1")
    assert env.get(replacement).status is SessionStatus.PROVISIONING

    # 19.4: no failed attempt, no mastery change.
    assert env.runs.list_running() == ()
    assert env.mastery.get("u1", "c1") == env.mastery_before

    # 19.7: the counter moved exactly once, however often the loss is detected again.
    for _ in range(2):
        env.reaper().execute()
    assert env.metrics.infra_loss == 1
    assert env.get(sid).status is SessionStatus.ENDED_BY_INFRASTRUCTURE


# --------------------------------------------------------------------------- cordon and drain


# Claims: 19.1, 19.2, 19.3, 19.4, 19.7
def test_cordon_and_drain_of_the_lab_node_ends_the_session_by_infrastructure(
    kind_context: str, lab_node: str, env: LabEnv
) -> None:
    sid = env.ready_session()
    env.start_worker_jobs()

    def cordon_and_drain() -> None:
        kubectl(kind_context, "cordon", lab_node)
        # --disable-eviction deletes the pod directly: this is the "drain" the operator forces
        # (or the node is lost) and no PodDisruptionBudget applies (see the protection test).
        kubectl(
            kind_context,
            "drain",
            lab_node,
            "--ignore-daemonsets",
            "--delete-emptydir-data",
            "--force",
            "--disable-eviction",
            "--timeout=120s",
            timeout=150,
        )

    timeline = asyncio.run(_loss_timeline(env, sid, cordon_and_drain))
    _assert_learner_outcome(env, sid, timeline)


# --------------------------------------------------------------------------- node failure


# Claims: 19.1, 19.2, 19.3, 19.4, 19.7
def test_failed_then_deleted_lab_node_ends_the_session_by_infrastructure(
    kind_context: str, lab_node: str, env: LabEnv
) -> None:
    if shutil.which("docker") is None:
        pytest.skip("docker is not on PATH: cannot stop the kind node container")
    sid = env.ready_session()
    env.start_worker_jobs()

    def fail_and_delete_node() -> None:
        run(["docker", "stop", lab_node], timeout=120)  # the kubelet and the pod die with it
        kubectl(kind_context, "delete", "node", lab_node, "--wait=false")

    timeline = asyncio.run(_loss_timeline(env, sid, fail_and_delete_node))
    _assert_learner_outcome(env, sid, timeline)


# --------------------------------------------------------------------------- evicted pod


# Claims: 19.5, 19.2, 19.7
def test_evicted_lab_pod_object_is_removed_within_the_bound(
    kind_context: str, kind_labs_namespace: str, env: LabEnv
) -> None:
    """A pod the kubelet evicted is ``Failed`` with reason ``Evicted``; the Reaper removes it.

    The kubelet only evicts under node pressure, which kind cannot be driven into reliably. The
    same API state is produced by setting the pod status through the status subresource on a pod
    that is never scheduled (so no kubelet can overwrite it), which is exactly what the Worker
    sees: phase ``Failed``, reason ``Evicted``.
    """

    sid = "fail-evicted-" + str(int(time.time()))
    pod = "lab-" + sid
    now = env.clock.now()
    env.session_ids.append(sid)
    env.sessions.add_if_no_active(
        LabSession(
            stable_id=sid,
            user_id="u1",
            lab_id="lab1",
            status=SessionStatus.READY,
            created_at=now,
            ttl_at=now + config.LAB_SESSION_TTL,
            sandbox_ref=pod,
        )
    )
    kubectl(
        kind_context, "apply", "-f", "-", input_text=json.dumps(unschedulable_managed_pod(pod, sid))
    )
    env.start_worker_jobs()
    # Not evicted yet: the Reaper must leave the active session's pod alone (Req 20.3).
    time.sleep(2 * sec(config.LAB_REAPER_INTERVAL) / 3)
    assert pod_exists(kind_context, LABS_NAMESPACE, pod)
    assert env.get(sid).status is SessionStatus.READY

    evicted_at = time.monotonic()
    kubectl(
        kind_context,
        "patch",
        "pod",
        pod,
        "-n",
        LABS_NAMESPACE,
        "--subresource=status",
        "--type=merge",
        "-p",
        json.dumps(
            {
                "status": {
                    "phase": "Failed",
                    "reason": "Evicted",
                    "message": "The node was low on resource: memory.",
                }
            }
        ),
    )
    removed_at = wait_until(
        lambda: not pod_exists(kind_context, LABS_NAMESPACE, pod),
        EVICTED_REMOVAL_BOUND_S + MEASURE_SLACK_S,
        what=f"evicted pod {pod} to be removed",
    )
    assert removed_at - evicted_at <= EVICTED_REMOVAL_BOUND_S + MEASURE_SLACK_S
    assert env.get(sid).status is SessionStatus.ENDED_BY_INFRASTRUCTURE
    assert env.sessions.get_active_for_user("u1") is None
    assert env.metrics.infra_loss == 1


# --------------------------------------------------------------------------- drain protection


def _render_labs_pdb() -> dict[str, Any]:
    helm = shutil.which("helm")
    if helm is None:
        pytest.skip("helm is not on PATH")
    done = run(
        [
            helm,
            "template",
            "edu-it",
            str(CHART),
            "-f",
            str(CHART / "values-dev.yaml"),
            "--set",
            "namespaces.manage=operator-managed",
            "--set",
            "networkPolicy.kubernetesApi.cidrs={10.0.0.1/32}",
            "-s",
            "templates/pdb.yaml",
        ],
        timeout=120,
    )
    pdbs = [
        d
        for d in yaml.safe_load_all(done.stdout)
        if d
        and d.get("kind") == "PodDisruptionBudget"
        and d["metadata"].get("namespace") == LABS_NAMESPACE
    ]
    assert len(pdbs) == 1, (
        "the Chart must render exactly one PodDisruptionBudget in the labs namespace"
    )
    return pdbs[0]


# Claims: 19.6, 19.8, 19.2, 19.4, 19.7
def test_drain_protection_makes_a_planned_drain_wait_then_forced_removal_ends_the_session(
    kind_context: str, lab_node: str, kind_labs_namespace: str, env: LabEnv
) -> None:
    values = yaml.safe_load((CHART / "values-dev.yaml").read_text(encoding="utf-8"))
    protection = values["labs"]["drainProtection"]
    assert protection["enabled"] is True and int(protection["waitLimitSeconds"]) > 0

    pdb = _render_labs_pdb()
    assert pdb["spec"]["maxUnavailable"] == 0  # 19.6: a planned drain may not take a Lab_Pod
    kubectl(kind_context, "apply", "-f", "-", input_text=json.dumps(pdb))
    try:
        sid = env.ready_session()
        pod = env.pod_name(sid)
        env.start_worker_jobs()

        # Planned drain (eviction API): blocked by the PDB until the limit, the pod stays.
        planned = kubectl(
            kind_context,
            "drain",
            lab_node,
            "--ignore-daemonsets",
            "--delete-emptydir-data",
            "--force",
            "--timeout=25s",
            check=False,
            timeout=90,
        )
        assert planned.returncode != 0, "a drain must wait on the PodDisruptionBudget, not finish"
        assert pod_exists(kind_context, LABS_NAMESPACE, pod)
        assert env.get(sid).status is SessionStatus.READY
        assert env.metrics.infra_loss == 0

        # Limit exceeded: the operator forces removal, the loss path applies (19.8).
        def force_removal() -> None:
            kubectl(
                kind_context,
                "drain",
                lab_node,
                "--ignore-daemonsets",
                "--delete-emptydir-data",
                "--force",
                "--disable-eviction",
                "--timeout=120s",
                timeout=150,
            )

        timeline = asyncio.run(_loss_timeline(env, sid, force_removal))
        _assert_learner_outcome(env, sid, timeline)
    finally:
        kubectl(
            kind_context,
            "delete",
            "pdb",
            pdb["metadata"]["name"],
            "-n",
            LABS_NAMESPACE,
            "--ignore-not-found",
            check=False,
        )
