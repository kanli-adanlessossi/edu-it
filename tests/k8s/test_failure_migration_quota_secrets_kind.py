"""Failure scenarios on kind: failing migration, full Labs quota, unsyncable ExternalSecret.

kubernetes-deployment task 14.7 (Req 21, 22, 23).

Three groups, each tagged with ``# Claims:`` ids for ``tools/check_traceability.py``:

* Migration (``helm upgrade`` with a failing Migration_Job, and one that exceeds its
  ``activeDeadlineSeconds``). A real failing migration is produced by rewinding the dev database
  to revision ``0005_ai_content`` and parking the ``lab_session`` table, so ``0006`` fails
  inside its transaction. The deadline case holds an ACCESS EXCLUSIVE lock on
  ``alembic_version`` so the Job hangs. Both need the Chart, ``helm`` and the image digests of
  ``test_smoke_kind`` (``EDU_IT_KIND_DIGEST_*``).
* Quota (Req 22). These need only the kind cluster, not the Chart. A ``ResourceQuota`` with
  ``pods: 0`` fills the ``labs`` namespace, and the real ``KubernetesSandboxProvider`` and the
  real ``ProvisionSandbox`` job run against the kind API server.
* Secrets (Req 23). The harness installs only the ``ExternalSecret`` CRD, no operator, so no
  ``ExternalSecret`` ever syncs and its Kubernetes Secret stays absent. That is the unsyncable
  case, for ``helm install`` and for ``helm upgrade``. One test needs no cluster: it starts the
  Worker startup check as a process with no secrets mounted.

Without a reachable cluster the ``kind`` marker skips the cluster tests (``conftest.py``); the
process-level startup test always runs.

Known limit: the Api has no required-secret startup check yet (only the Worker has one,
``edu_it_worker.lifecycle.run_startup_checks``), so the 30 s exit test covers the Worker only.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import pytest
from edu_it_api.errors.labs import LAB_CAPACITY, LAB_MESSAGES
from edu_it_application.labs import ProvisionOutcome, ProvisionSandbox
from edu_it_domain import config
from edu_it_domain.labs.models import LabSession, SessionStatus
from edu_it_domain.labs.sandbox_spec import build_sandbox_spec
from edu_it_domain.ports.lab_catalog import LabDefinition
from edu_it_domain.ports.sandbox import ProvisionCapacityError, ProvisionError
from edu_it_infrastructure.composition import CompositionSettings, build_k8s_settings
from edu_it_infrastructure.fakes import (
    Fake_LabCatalog,
    In_Memory_GradingRunRepository,
    In_Memory_LabSessionRepository,
    Test_Clock,
)
from edu_it_infrastructure.sandbox.k8s_api import K8sApiPort
from edu_it_infrastructure.sandbox.k8s_provider import LABEL_SESSION, KubernetesSandboxProvider
from edu_it_infrastructure.telemetry import PrometheusLabMetrics
from prometheus_client import CollectorRegistry

from tests.k8s.conftest import LABS_NAMESPACE
from tests.k8s.test_smoke_kind import (
    PLATFORM_NAMESPACE,
    RELEASE,
    _api_cidr,
    _digests,
    _helm_args,
    _kubectl,
    _namespace_manifest,
    _run,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
LAB_IMAGE = "edu-it/lab-linux-base:dev"
POSTGRES_POD = f"{RELEASE}-dev-postgres-0"
HEAD_REVISION = "0006_k8s_status_capacity"
PREVIOUS_REVISION = "0005_ai_content"
WORKLOADS = ("api", "worker")
QUOTA_NAME = "failure-quota-full"
LOCK_MARKER = "pg_sleep(241)"

#: Values that make every ExternalSecret point at a store that does not exist. With no
#: External Secrets operator on kind, none of them ever syncs (Req 23.1).
UNSYNCABLE_SETS = (
    "devSecrets.enabled=false",
    "externalSecrets.storeName=unsyncable-store",
    "externalSecrets.storeKind=ClusterSecretStore",
    "externalSecrets.remoteKeys.database=edu-it/database",
    "externalSecrets.remoteKeys.redis=edu-it/redis",
    "externalSecrets.remoteKeys.session=edu-it/session",
    "externalSecrets.remoteKeys.llm=edu-it/llm",
    "externalSecrets.remoteKeys.objectStorage=edu-it/object-storage",
)


# --------------------------------------------------------------------------- shared helpers


def _helm(argv: list[str], *, timeout: int = 900) -> subprocess.CompletedProcess[str]:
    """Run helm and return the result whatever the exit status (failure is the expectation)."""

    return subprocess.run(
        ["helm", *argv], capture_output=True, text=True, timeout=timeout, check=False
    )


def _kubectl_try(context: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["kubectl", "--context", context, *args],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )


def _release_status(context: str) -> str:
    out = _run(
        ["helm", "status", RELEASE, "--kube-context", context, "-n", PLATFORM_NAMESPACE,
         "-o", "json"],
        timeout=60,
    )
    return str(json.loads(out)["info"]["status"])


def _set_args(pairs: tuple[str, ...] | list[str]) -> list[str]:
    args: list[str] = []
    for pair in pairs:
        args += ["--set", pair]
    return args


def _pods(context: str, component: str) -> list[dict]:
    out = _kubectl(
        context, "get", "pods", "-n", PLATFORM_NAMESPACE,
        "-l", f"app.kubernetes.io/component={component}", "-o", "json",
    )
    return [
        pod
        for pod in json.loads(out)["items"]
        if not pod["metadata"].get("deletionTimestamp")
    ]


def _is_ready(pod: dict) -> bool:
    conditions = {c["type"]: c["status"] for c in pod.get("status", {}).get("conditions", [])}
    return conditions.get("Ready") == "True"


def _workload_state(context: str) -> dict[str, dict[str, object]]:
    """Per Deployment: the live pod names and the Deployment generation."""

    state: dict[str, dict[str, object]] = {}
    for component in WORKLOADS:
        generation = _kubectl(
            context, "get", "deployment", f"{RELEASE}-{component}", "-n", PLATFORM_NAMESPACE,
            "-o", "jsonpath={.metadata.generation}",
        )
        state[component] = {
            "pods": sorted(p["metadata"]["name"] for p in _pods(context, component)),
            "generation": generation,
        }
    return state


def _psql(context: str, sql: str) -> str:
    return _kubectl(
        context, "exec", "-n", PLATFORM_NAMESPACE, POSTGRES_POD, "-c", "postgres", "--",
        "psql", "-U", "edu_it", "-d", "edu_it", "-tA", "-v", "ON_ERROR_STOP=1", "-c", sql,
    ).strip()


def _migration_log(context: str) -> str:
    done = _kubectl_try(
        context, "logs", "-n", PLATFORM_NAMESPACE,
        "-l", "app.kubernetes.io/component=migration", "--tail=-1",
    )
    return done.stdout + done.stderr


def _reset_platform(context: str) -> None:
    """Uninstall the release and remove the platform namespace (hook leftovers included)."""

    subprocess.run(
        ["helm", "uninstall", RELEASE, "--kube-context", context, "-n", PLATFORM_NAMESPACE,
         "--no-hooks", "--wait"],
        capture_output=True, timeout=300, check=False,
    )
    subprocess.run(
        ["kubectl", "--context", context, "delete", "namespace", PLATFORM_NAMESPACE,
         "--ignore-not-found", "--wait=true", "--timeout=180s"],
        capture_output=True, timeout=240, check=False,
    )


def _prepare_namespaces(context: str) -> None:
    for namespace in (PLATFORM_NAMESPACE, LABS_NAMESPACE):
        _kubectl(context, "apply", "-f", "-", input_text=_namespace_manifest(namespace))


@pytest.fixture(scope="module")
def chart_args(kind_context: str) -> list[str]:
    if shutil.which("helm") is None:
        pytest.skip("helm is not on PATH")
    return _helm_args(kind_context, _digests(), _api_cidr())


@pytest.fixture(scope="module")
def baseline(kind_context: str, chart_args: list[str]) -> Iterator[str]:
    """A healthy dev release (two-phase install as in ``test_smoke_kind``), Api and Worker Ready."""

    _reset_platform(kind_context)
    _prepare_namespaces(kind_context)
    try:
        _run(["helm", "install", RELEASE, *chart_args, "--no-hooks"])
        for store in ("dev-postgres", "dev-redis"):
            _kubectl(
                kind_context, "rollout", "status", f"statefulset/{RELEASE}-{store}",
                "-n", PLATFORM_NAMESPACE, "--timeout=300s",
            )
        _run(["helm", "upgrade", RELEASE, *chart_args, "--wait", "--timeout", "600s"], timeout=700)
        yield kind_context
    finally:
        _reset_platform(kind_context)


# --------------------------------------------------------------------------- Req 22: quota


class _Lab:
    image = LAB_IMAGE


_LAB_DEFINITION = LabDefinition(
    lab_id="lab1", kind="terminal", image=LAB_IMAGE, concept_ids=(), tasks=(), checks=()
)


def _kubernetes_env(**extra: str) -> CompositionSettings:
    # kind has no gVisor or Kata: plain runc without a RuntimeClass (dev only, Req 2.12).
    return CompositionSettings.from_env(
        {
            **os.environ,
            "SANDBOX_PROVIDER": "kubernetes",
            "K8S_LABS_NAMESPACE": LABS_NAMESPACE,
            "SANDBOX_RUNTIME_KIND": "runc",
            "SANDBOX_RUNTIME_CLASS": "",
            **extra,
        }
    )


def _advancing_sleep(clock: Test_Clock, real_s: float = 0.5):
    """A sleeper for the provider: wait a little for real, move the test clock by the full step."""

    def sleep(seconds: float) -> None:
        time.sleep(min(seconds, real_s))
        clock.advance(timedelta(seconds=seconds))

    return sleep


def _session_pods(api: K8sApiPort, session_id: str) -> list[str]:
    return [p.name for p in api.list_pods(LABS_NAMESPACE, f"{LABEL_SESSION}={session_id}")]


def _wait_for_quota(context: str, pods: int) -> None:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        hard = _kubectl_try(
            context, "get", "resourcequota", QUOTA_NAME, "-n", LABS_NAMESPACE,
            "-o", "jsonpath={.status.hard.pods}",
        ).stdout.strip()
        if hard == str(pods):
            return
        time.sleep(0.5)
    raise AssertionError(f"ResourceQuota {QUOTA_NAME} did not report pods={pods}")


@pytest.fixture
def full_labs_quota(kind_context: str, kind_labs_namespace: str) -> Iterator[None]:
    """Fill the Labs quota: ``pods: 0`` rejects every Lab_Pod create (Req 22.1)."""

    quota = {
        "apiVersion": "v1",
        "kind": "ResourceQuota",
        "metadata": {"name": QUOTA_NAME, "namespace": LABS_NAMESPACE},
        "spec": {"hard": {"pods": "0"}},
    }
    _kubectl(kind_context, "apply", "-f", "-", input_text=json.dumps(quota))
    try:
        _wait_for_quota(kind_context, 0)
        yield
    finally:
        _kubectl_try(
            kind_context, "delete", "resourcequota", QUOTA_NAME, "-n", LABS_NAMESPACE,
            "--ignore-not-found",
        )


def _provision_job(api: K8sApiPort, clock: Test_Clock, registry: CollectorRegistry):
    provider = KubernetesSandboxProvider(
        api, build_k8s_settings(_kubernetes_env()), clock, sleep=_advancing_sleep(clock)
    )
    sessions = In_Memory_LabSessionRepository(In_Memory_GradingRunRepository())
    job = ProvisionSandbox(
        sessions=sessions,
        catalog=Fake_LabCatalog([_LAB_DEFINITION]),
        sandbox=provider,
        clock=clock,
        metrics=PrometheusLabMetrics(registry),
    )
    return job, sessions


def _new_session(sessions: In_Memory_LabSessionRepository, clock: Test_Clock) -> str:
    session_id = f"fail-{uuid.uuid4().hex[:8]}"
    now = clock.now()
    sessions.add_if_no_active(
        LabSession(
            stable_id=session_id,
            user_id="u-failure",
            lab_id="lab1",
            status=SessionStatus.PROVISIONING,
            created_at=now,
            ttl_at=now + config.LAB_SESSION_TTL,
        )
    )
    return session_id


# Claims: 22.1, 22.2, 22.3
@pytest.mark.kind
def test_full_quota_raises_a_capacity_error_with_no_raw_text_and_no_pod(
    kind_client_api: K8sApiPort, full_labs_quota: None
) -> None:
    clock = Test_Clock()
    provider = KubernetesSandboxProvider(
        kind_client_api, build_k8s_settings(_kubernetes_env()), clock
    )
    session_id = f"fail-{uuid.uuid4().hex[:8]}"
    started = time.monotonic()
    with pytest.raises(ProvisionCapacityError) as raised:
        provider.provision(build_sandbox_spec(_Lab(), session_id))
    assert time.monotonic() - started < 30  # Req 22.3 bound

    text = str(raised.value).lower()
    assert "exceeded quota" not in text and "pods=" not in text and "forbidden" not in text
    assert _session_pods(kind_client_api, session_id) == []  # no partial Lab_Pod (Req 22.3)

    # The learner message exists in both locales and carries no quota detail (Req 22.2).
    assert LAB_MESSAGES["labs.capacity"] is LAB_CAPACITY
    for locale in ("fr", "en"):
        shown = f"{LAB_CAPACITY.title[locale]} {LAB_CAPACITY.detail[locale]}".lower()
        assert LAB_CAPACITY.detail[locale]
        assert not any(raw in shown for raw in ("quota", "forbidden", "pods=", "kubernetes"))
    assert LAB_CAPACITY.detail["fr"] != LAB_CAPACITY.detail["en"]


# Claims: 22.1
@pytest.mark.kind
def test_a_non_quota_rejection_is_not_a_capacity_error(
    kind_client_api: K8sApiPort, kind_labs_namespace: str
) -> None:
    """A missing RuntimeClass is refused by admission, which is not a quota rejection."""

    settings = build_k8s_settings(
        _kubernetes_env(SANDBOX_RUNTIME_KIND="gvisor", SANDBOX_RUNTIME_CLASS="missing-rc")
    )
    provider = KubernetesSandboxProvider(kind_client_api, settings, Test_Clock())
    session_id = f"fail-{uuid.uuid4().hex[:8]}"
    with pytest.raises(ProvisionError) as raised:
        provider.provision(build_sandbox_spec(_Lab(), session_id))
    assert not isinstance(raised.value, ProvisionCapacityError)
    assert _session_pods(kind_client_api, session_id) == []


# Claims: 22.2, 22.3, 22.4, 22.5, 22.6, 22.7
@pytest.mark.kind
def test_full_quota_requeues_then_fails_without_using_the_retry_allowance(
    kind_client_api: K8sApiPort, full_labs_quota: None
) -> None:
    clock = Test_Clock()
    registry = CollectorRegistry()
    job, sessions = _provision_job(kind_client_api, clock, registry)
    session_id = _new_session(sessions, clock)
    backoff = config.LAB_PROVISION_CAPACITY_BACKOFF
    timeout = config.LAB_PROVISION_TIMEOUT
    first_attempt = clock.now()

    # Below the provisioning timeout: requeued with the capacity backoff (Req 22.6), the failure
    # is reported to the worker within 5 s (Req 22.2), and no pod is left (Req 22.3).
    started = time.monotonic()
    assert job.execute(session_id) is ProvisionOutcome.RETRY_SCHEDULED
    assert time.monotonic() - started < 5
    session = sessions.get(session_id)
    assert session is not None and session.status is SessionStatus.PROVISIONING
    assert session.next_provision_at == first_attempt + backoff
    assert session.provision_attempts == 0 and session.capacity_attempts == 1  # Req 22.4
    assert _session_pods(kind_client_api, session_id) == []

    # More rejections than the whole retry allowance still leave it untouched.
    rejections = 1
    while clock.now() - first_attempt + backoff < timeout:
        clock.advance(backoff)
        assert job.execute(session_id) is ProvisionOutcome.RETRY_SCHEDULED
        rejections += 1
    session = sessions.get(session_id)
    assert session is not None
    assert session.provision_attempts == 0 and session.capacity_attempts == rejections
    assert rejections >= 2  # the loop ran at least once more than the first rejection

    # At or above the timeout: no more requeue, failed with the capacity reason (Req 22.7).
    clock.advance(timeout)
    assert job.execute(session_id) is ProvisionOutcome.FAILED
    session = sessions.get(session_id)
    assert session is not None and session.status is SessionStatus.FAILED
    assert session.provision_attempts == 0 and session.capacity_attempts == rejections + 1
    assert _session_pods(kind_client_api, session_id) == []

    # Exactly one counter increment per rejected attempt (Req 22.5), and the failure reason.
    assert registry.get_sample_value("edu_it_lab_quota_rejections_total") == rejections + 1
    assert (
        registry.get_sample_value("edu_it_lab_provision_failures_total", {"reason": "capacity"})
        == 1
    )


# Claims: 22.8, 22.1
@pytest.mark.kind
def test_pending_unschedulable_lab_pod_at_the_timeout_is_a_capacity_error_and_is_deleted(
    kind_client_api: K8sApiPort, kind_labs_namespace: str
) -> None:
    clock = Test_Clock()
    settings = build_k8s_settings(
        _kubernetes_env(SANDBOX_NODE_SELECTOR="edu-it.io/no-such-pool=true")
    )
    provider = KubernetesSandboxProvider(
        kind_client_api,
        settings,
        clock,
        sleep=_advancing_sleep(clock),
        provision_timeout=timedelta(seconds=10),
    )
    session_id = f"fail-{uuid.uuid4().hex[:8]}"
    with pytest.raises(ProvisionCapacityError):
        provider.provision(build_sandbox_spec(_Lab(), session_id))

    deadline = time.monotonic() + 60  # the Pending pod is deleted, not just abandoned
    while _session_pods(kind_client_api, session_id) and time.monotonic() < deadline:
        time.sleep(1)
    assert _session_pods(kind_client_api, session_id) == []


# --------------------------------------------------------------------------- Req 21: migration


# Claims: 21.1, 21.2
@pytest.mark.kind
def test_failing_migration_fails_helm_upgrade_and_leaves_the_release_intact(
    baseline: str, chart_args: list[str]
) -> None:
    context = baseline
    before = _workload_state(context)
    assert all(state["pods"] for state in before.values())

    # A real failing migration: 0006 runs again and cannot find its table, inside its transaction.
    _psql(context, f"UPDATE alembic_version SET version_num = '{PREVIOUS_REVISION}'")
    _psql(context, "ALTER TABLE lab_session RENAME TO lab_session_parked")
    try:
        # The extra value would roll the Api out if the upgrade got past the migration.
        done = _helm(
            ["upgrade", RELEASE, *chart_args, "--set", "api.drainPeriodSeconds=31",
             "--wait", "--timeout", "120s"],
            timeout=300,
        )
        assert done.returncode != 0, done.stdout  # Req 21.1: non-zero exit status
        assert _release_status(context) == "failed"

        # Previous Deployments untouched: same pods, same generation, no new-release pod.
        assert _workload_state(context) == before
        for component in WORKLOADS:
            assert all(_is_ready(p) for p in _pods(context, component))

        # Req 21.2: the Job log names the failing migration and keeps the error text.
        log = _migration_log(context)
        assert HEAD_REVISION in log, log
        assert "lab_session" in log, log
        assert "edu-it-dev-placeholder" not in log  # the database URL is never logged

        # Req 21.2 / 21.3: the failed migration left the schema as it was before it started.
        assert _psql(context, "SELECT version_num FROM alembic_version") == PREVIOUS_REVISION
        assert _psql(
            context, "SELECT to_regclass('public.lab_session_parked') IS NOT NULL"
        ) == "t"  # the parked table is exactly as the failed migration found it
    finally:
        _psql(context, "ALTER TABLE IF EXISTS lab_session_parked RENAME TO lab_session")
        _psql(context, f"UPDATE alembic_version SET version_num = '{HEAD_REVISION}'")
        _run(["helm", "upgrade", RELEASE, *chart_args, "--wait", "--timeout", "600s"], timeout=700)


# Claims: 21.5, 21.1
@pytest.mark.kind
def test_migration_job_over_its_deadline_is_a_failed_upgrade(
    baseline: str, chart_args: list[str]
) -> None:
    context = baseline
    before = _workload_state(context)
    holder = subprocess.Popen(
        ["kubectl", "--context", context, "exec", "-n", PLATFORM_NAMESPACE, POSTGRES_POD,
         "-c", "postgres", "--", "psql", "-U", "edu_it", "-d", "edu_it", "-c",
         f"BEGIN; LOCK TABLE alembic_version IN ACCESS EXCLUSIVE MODE; SELECT {LOCK_MARKER};"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        lock_sql = (
            "SELECT count(*) FROM pg_locks l JOIN pg_class c ON c.oid = l.relation "
            "WHERE c.relname = 'alembic_version' AND l.mode = 'AccessExclusiveLock' AND l.granted"
        )
        deadline = time.monotonic() + 60
        while _psql(context, lock_sql) == "0":
            assert holder.poll() is None and time.monotonic() < deadline, "lock not taken"
            time.sleep(1)

        started = time.monotonic()
        done = _helm(
            ["upgrade", RELEASE, *chart_args, "--set", "api.drainPeriodSeconds=31",
             "--set", "migration.activeDeadlineSeconds=30", "--wait", "--timeout", "180s"],
            timeout=300,
        )
        elapsed = time.monotonic() - started
        assert done.returncode != 0, done.stdout
        assert elapsed >= 30  # not before the Job's deadline
        assert _release_status(context) == "failed"
        reason = _kubectl(
            context, "get", "job", "-n", PLATFORM_NAMESPACE,
            "-l", "app.kubernetes.io/component=migration",
            "-o", "jsonpath={.items[0].status.conditions[?(@.type=='Failed')].reason}",
        )
        assert reason == "DeadlineExceeded"
        assert _workload_state(context) == before  # Req 21.1 applies to a timed-out Job too
    finally:
        _kubectl_try(
            context, "exec", "-n", PLATFORM_NAMESPACE, POSTGRES_POD, "-c", "postgres", "--",
            "psql", "-U", "edu_it", "-d", "edu_it", "-c",
            f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            f"WHERE query LIKE '%{LOCK_MARKER}%' AND pid <> pg_backend_pid()",
        )
        holder.kill()
        _run(["helm", "upgrade", RELEASE, *chart_args, "--wait", "--timeout", "600s"], timeout=700)


# --------------------------------------------------------------------------- Req 23: secrets


# Claims: 23.1, 23.3
@pytest.mark.kind
def test_install_with_an_unsyncable_externalsecret_fails_at_the_helm_timeout(
    kind_context: str, chart_args: list[str]
) -> None:
    context = kind_context
    timeout_s = 60
    _reset_platform(context)
    _prepare_namespaces(context)
    try:
        started = time.monotonic()
        done = _helm(
            ["install", RELEASE, *chart_args, *_set_args(UNSYNCABLE_SETS),
             "--wait", "--timeout", f"{timeout_s}s"],
            timeout=timeout_s + 240,
        )
        elapsed = time.monotonic() - started
        assert done.returncode != 0, done.stdout  # the release fails (Req 23.3)
        assert elapsed >= timeout_s * 0.8  # it waited for the Helm timeout, it did not pass early
        assert elapsed <= timeout_s + 120
        assert _release_status(context) == "failed"

        # The Secret is absent and nothing started with default or empty credentials (Req 23.1).
        absent = _kubectl_try(
            context, "get", "secret", f"{RELEASE}-database", "-n", PLATFORM_NAMESPACE
        )
        assert absent.returncode != 0 and "NotFound" in absent.stderr
        for component in WORKLOADS:
            assert not any(_is_ready(p) for p in _pods(context, component))
        migration_pods = _pods(context, "migration")
        assert not any(_is_ready(p) for p in migration_pods)
        assert not any(
            s.get("state", {}).get("running")
            for p in migration_pods
            for s in p.get("status", {}).get("containerStatuses", [])
        )
    finally:
        _reset_platform(context)


# Claims: 23.1, 23.3, 23.4
@pytest.mark.kind
def test_upgrade_with_an_unsyncable_externalsecret_fails_and_old_pods_keep_serving(
    baseline: str, chart_args: list[str]
) -> None:
    context = baseline
    timeout_s = 90
    before = _workload_state(context)
    old_pods = {c: set(state["pods"]) for c, state in before.items()}  # type: ignore[arg-type]
    assert all(old_pods.values())
    assert all(_is_ready(p) for c in WORKLOADS for p in _pods(context, c))
    try:
        started = time.monotonic()
        # The Chart no longer renders the dev Secrets, so the new pods find no Secret to read.
        done = _helm(
            ["upgrade", RELEASE, *chart_args, *_set_args(UNSYNCABLE_SETS),
             "--set", "api.drainPeriodSeconds=31", "--wait", "--timeout", f"{timeout_s}s"],
            timeout=timeout_s + 240,
        )
        elapsed = time.monotonic() - started
        assert done.returncode != 0, done.stdout  # Req 23.4
        assert timeout_s * 0.8 <= elapsed <= timeout_s + 120
        assert _release_status(context) == "failed"

        # The Kubernetes Secret is absent (Req 23.1) ...
        absent = _kubectl_try(
            context, "get", "secret", f"{RELEASE}-redis", "-n", PLATFORM_NAMESPACE
        )
        assert absent.returncode != 0 and "NotFound" in absent.stderr

        # ... the pods that wait for it are not Ready, and were not started with empty
        # credentials (Req 23.1) ...
        for component in WORKLOADS:
            pods = _pods(context, component)
            new_pods = [p for p in pods if p["metadata"]["name"] not in old_pods[component]]
            assert not any(_is_ready(p) for p in new_pods)
            assert not any(
                s.get("ready") for p in new_pods for s in p["status"].get("containerStatuses", [])
            )
            # ... while every previous pod is still there and Ready (Req 23.4).
            names = {p["metadata"]["name"] for p in pods}
            assert old_pods[component] <= names
            assert all(_is_ready(p) for p in pods if p["metadata"]["name"] in old_pods[component])

        # The previous Api pod still serves: it is an address of the Service endpoints.
        addresses = _kubectl(
            context, "get", "endpoints", f"{RELEASE}-api", "-n", PLATFORM_NAMESPACE,
            "-o", "jsonpath={.subsets[*].addresses[*].targetRef.name}",
        ).split()
        assert set(addresses) & old_pods["api"]
    finally:
        _run(["helm", "upgrade", RELEASE, *chart_args, "--wait", "--timeout", "600s"], timeout=700)


# --------------------------------------------------------------------------- Req 23.2: no cluster

_STARTUP_PROBE = """
import logging, sys
logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(message)s")
from edu_it_worker.lifecycle import StartupRefused, run_startup_checks
import os
try:
    run_startup_checks(os.environ)
except StartupRefused as exc:
    logging.error("%s", exc)
    sys.exit(1)
"""


# Claims: 23.2
def test_worker_with_missing_secrets_exits_non_zero_within_30_s_naming_each_key(
    tmp_path: Path,
) -> None:
    """Worker only: the Api has no required-secret startup check yet."""

    sentinel = "SENTINEL-" + uuid.uuid4().hex
    (tmp_path / "session").mkdir()
    (tmp_path / "session" / "key").write_text(sentinel, encoding="utf-8")  # present, not required
    env = {
        **os.environ,
        "SANDBOX_PROVIDER": "kubernetes",
        "SANDBOX_RUNTIME_KIND": "runc",
        "SANDBOX_RUNTIME_CLASS": "",
        "EDU_IT_SECRETS_ROOT": str(tmp_path),
        "PYTHONPATH": os.pathsep.join(p for p in sys.path if p),
    }
    started = time.monotonic()
    done = subprocess.run(
        [sys.executable, "-c", _STARTUP_PROBE],
        env=env, capture_output=True, text=True, timeout=60, check=False,
    )
    elapsed = time.monotonic() - started
    assert done.returncode != 0, done.stderr
    assert elapsed < 30
    output = done.stdout + done.stderr
    for key in ("database/url", "redis/url"):
        assert output.count(f"{key}") >= 1, output
    assert sentinel not in output  # a secret value is never logged
