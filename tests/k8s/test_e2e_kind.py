"""E2E test on kind (kubernetes-deployment task 14.3, Req 16.1, 16.2, 8.8, 8.9).

Installs the Chart with ``values-dev.yaml`` on an *empty* cluster (only the two namespaces) with
the two-phase first-install bootstrap of ADR 0010 ("Known gap (first install on kind)"), as the
6.14 smoke test does: Helm runs pre-install hooks before the release's normal resources, so a
single ``helm install`` cannot migrate the bundled dev PostgreSQL. Phase 1 is
``helm install --no-hooks`` and waits for the dev datastores; phase 2 is a ``helm upgrade`` whose
pre-upgrade hooks prove the hook order of task 12.3: the PriorityClass, Migration_Job
ServiceAccount, database Secret and ConfigMap are weight ``-10`` hooks that exist before the
weight ``0`` Job runs. It then waits up to 600 s after the bootstrap completes for every Platform
workload to be Ready. If they are not, the test fails, names the
workloads that are not Ready and leaves the cluster and the release in place (Req 16.2). When they
are Ready it runs the Phase 3 end-to-end flow (the real-stack Playwright journeys of
``apps/web/e2e/real``) against the ingress, and finally upgrades once and checks that the hook
resources were replaced cleanly (Req 8.8, 8.9).

Requirements to run: a kind cluster from ``tools/kind_harness.py up`` (context ``kind-edu-it`` or
``EDU_IT_KIND_CONTEXT``), ``helm``, ``kubectl``, ``npx`` with Playwright browsers installed in
``apps/web``, and the image digests in ``EDU_IT_KIND_DIGEST_{WEB,API,WORKER,MIGRATIONS}``.
Without a cluster the ``kind`` marker skips the cluster tests (conftest); without helm or digests
they skip with the reason. The pure helpers are tested without a cluster at the bottom.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path

import pytest

from tests.k8s.conftest import LABS_NAMESPACE, PSA_LABELS

REPO_ROOT = Path(__file__).resolve().parents[2]
CHART = REPO_ROOT / "deploy" / "helm" / "edu-it"
HARNESS = REPO_ROOT / "tools" / "kind_harness.py"
WEB_APP = REPO_ROOT / "apps" / "web"
RELEASE = "edu-it"
PLATFORM_NAMESPACE = "platform"
READY_WAIT_SECONDS = 600
INGRESS_HOST = "edu-it.localtest.me"
INGRESS_LOCAL_PORT = 18443
PUBLIC_WEB_URL = f"https://{INGRESS_HOST}:{INGRESS_LOCAL_PORT}"
DIGEST_ENV = {
    "web": "EDU_IT_KIND_DIGEST_WEB",
    "api": "EDU_IT_KIND_DIGEST_API",
    "worker": "EDU_IT_KIND_DIGEST_WORKER",
    "migrations": "EDU_IT_KIND_DIGEST_MIGRATIONS",
}
# The Platform workloads of values-dev.yaml (Deployments and StatefulSets), by release suffix.
EXPECTED_WORKLOADS = (
    ("deployment", "api"),
    ("deployment", "worker"),
    ("deployment", "web"),
    ("statefulset", "dev-postgres"),
    ("statefulset", "dev-redis"),
)
# Hook resources replaced on every install and upgrade (weights -10 and 0, Req 8.8, 8.9).
HOOK_RESOURCES = (
    ("serviceaccount", "migration"),
    ("configmap", "migration"),
    ("secret", "database"),
    ("job", "migration"),
)
KIND_PLAYWRIGHT_CONFIG = "playwright.kind-e2e.config.ts"


# --------------------------------------------------------------------------- pure helpers


def not_ready_workloads(items: Sequence[Mapping[str, object]], expected: Sequence[str]) -> list[str]:
    """Names (``kind/name``) of expected workloads that are missing or not fully Ready.

    ``items`` are the entries of ``kubectl get deployments,statefulsets -o json``.
    """

    found: dict[str, bool] = {}
    for item in items:
        meta = item.get("metadata") or {}
        spec = item.get("spec") or {}
        status = item.get("status") or {}
        assert isinstance(meta, Mapping) and isinstance(spec, Mapping) and isinstance(status, Mapping)
        key = f"{str(item.get('kind', '')).lower()}/{meta.get('name', '')}"
        wanted = int(spec.get("replicas", 1) or 0)
        found[key] = int(status.get("readyReplicas", 0) or 0) >= wanted
    return [name for name in expected if not found.get(name, False)]


def expected_workload_names(release: str = RELEASE) -> list[str]:
    return [f"{kind}/{release}-{suffix}" for kind, suffix in EXPECTED_WORKLOADS]


def hooks_replaced(before: Mapping[str, str], after: Mapping[str, str]) -> list[str]:
    """Hook resources whose UID did not change (not replaced) or that are missing after."""

    return [name for name, uid in before.items() if after.get(name) in (None, uid)]


# --------------------------------------------------------------------------- command helpers


def _run(argv: list[str], *, timeout: int = 900, input_text: str | None = None) -> str:
    done = subprocess.run(
        argv, input=input_text, capture_output=True, text=True, timeout=timeout, check=False
    )
    assert done.returncode == 0, (
        f"{' '.join(argv)} failed ({done.returncode}):\n{done.stdout[-3000:]}\n{done.stderr[-3000:]}"
    )
    return done.stdout


def _kubectl(context: str, *args: str, input_text: str | None = None, timeout: int = 300) -> str:
    return _run(["kubectl", "--context", context, *args], input_text=input_text, timeout=timeout)


def _digests() -> dict[str, str]:
    missing = [env for env in DIGEST_ENV.values() if not os.environ.get(env)]
    if missing:
        pytest.skip(f"image digests not provided: set {', '.join(missing)}")
    return {name: os.environ[env] for name, env in DIGEST_ENV.items()}


def _api_cidr() -> str:
    out = _run([sys.executable, str(HARNESS), "api-address"], timeout=60).split()
    assert out, "kind_harness api-address printed nothing"
    return out[0]


def _helm_args(context: str, digests: Mapping[str, str], cidr: str) -> list[str]:
    args = [
        str(CHART),
        "--kube-context", context,
        "--namespace", PLATFORM_NAMESPACE,
        "-f", str(CHART / "values-dev.yaml"),
        "--set", "namespaces.manage=operator-managed",
        "--set", f"networkPolicy.kubernetesApi.cidrs={{{cidr}}}",
        # The browser reaches the ingress through the port-forward below, so email links and
        # the Api's CORS / terminal Origin allowlist must use that origin, not https://<host>.
        "--set", f"ingress.publicBaseUrl={PUBLIC_WEB_URL}",
    ]
    for name, digest in digests.items():
        args += ["--set", f"images.{name}.digest={digest}"]
    return args


def _workload_items(context: str) -> list[Mapping[str, object]]:
    out = _kubectl(context, "get", "deployments,statefulsets", "-n", PLATFORM_NAMESPACE, "-o", "json")
    return json.loads(out)["items"]  # type: ignore[no-any-return]


def _wait_ready(context: str, seconds: int = READY_WAIT_SECONDS) -> list[str]:
    """Poll up to ``seconds``; return the workloads still not Ready (empty when all are)."""

    deadline = time.monotonic() + seconds
    pending = expected_workload_names()
    while time.monotonic() < deadline:
        pending = not_ready_workloads(_workload_items(context), expected_workload_names())
        if not pending:
            return []
        time.sleep(5)
    return pending


def _hook_uids(context: str) -> dict[str, str]:
    uids: dict[str, str] = {}
    for kind, suffix in HOOK_RESOURCES:
        name = f"{RELEASE}-{suffix}"
        out = _kubectl(
            context, "get", kind, name, "-n", PLATFORM_NAMESPACE, "-o", "jsonpath={.metadata.uid}"
        )
        uids[f"{kind}/{name}"] = out.strip()
    return uids


#: The content sync of tools/e2e_stack.py, run in the Worker image: it ships the authored content
#: (/app/content, EDU_IT_CONTENT_ROOT) and lab-images/, and its DATABASE_URL comes from the
#: database Secret. Idempotent (upsert keyed by stable id), so a rerun changes nothing.
CONTENT_SYNC = (
    "import os, sys; from edu_it_content.cli import main; "
    "sys.exit(main(['sync', '--database-url', os.environ['DATABASE_URL']]))"
)


def _seed_content(context: str) -> str:
    """Sync the seed tracks, lessons and labs into the release's PostgreSQL; return the summary.

    The real journeys enroll in a track and start Seed_Labs, which need the content rows;
    without them enrollment answers 404 and starting a lab fails on the unknown lab.
    """

    return _kubectl(
        context, "exec", "-n", PLATFORM_NAMESPACE, f"deployment/{RELEASE}-worker", "-c", "worker",
        "--", "/opt/venv/bin/python", "-c", CONTENT_SYNC,
    ).strip()


def _port_open(port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(1)
        return sock.connect_ex(("127.0.0.1", port)) == 0


# --------------------------------------------------------------------------- fixtures


class _Release:
    def __init__(self, context: str, args: list[str]) -> None:
        self.context = context
        self.args = args
        self.install_failure: str | None = None
        self.hooks_after_install: dict[str, str] = {}


def _wait_namespace_not_terminating(context: str, namespace: str, timeout_s: int = 240) -> None:
    """Block while ``namespace`` is Terminating (earlier tests delete it with ``--wait=false``).

    Applying a namespace that is being terminated succeeds but leaves it terminating, and
    ``helm install`` then fails creating its release Secret. A missing namespace is fine.
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        done = subprocess.run(
            ["kubectl", "--context", context, "get", "namespace", namespace,
             "-o", "jsonpath={.status.phase}"],
            capture_output=True, text=True, timeout=60, check=False,
        )
        if done.returncode != 0 or done.stdout.strip() != "Terminating":
            return
        time.sleep(3)
    pytest.fail(f"namespace {namespace} still Terminating after {timeout_s} s")


def _bootstrap_install(context: str, args: list[str]) -> str | None:
    """First install in two phases (ADR 0010, known gap); return the failure text, or None.

    Helm runs pre-install hooks before the release's normal resources, so on a single
    ``helm install`` the weight-0 Migration_Job finds no dev PostgreSQL. Phase 1 installs
    without hooks so the dev datastores exist and become ready; phase 2 is a ``helm upgrade``
    whose pre-upgrade hooks (weight -10 bootstrap resources, then the Job) run against them.
    """

    phases: list[tuple[str, list[str], int]] = [
        ("helm install --no-hooks", ["helm", "install", RELEASE, *args, "--no-hooks"], 300),
        *[
            (
                f"rollout of {store}",
                ["kubectl", "--context", context, "rollout", "status",
                 f"statefulset/{RELEASE}-{store}", "-n", PLATFORM_NAMESPACE, "--timeout=300s"],
                360,
            )
            for store in ("dev-postgres", "dev-redis")
        ],
        ("helm upgrade (hooks)", ["helm", "upgrade", RELEASE, *args], READY_WAIT_SECONDS + 300),
    ]
    for label, argv, timeout in phases:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
        if done.returncode != 0:
            return (
                f"{label} failed ({done.returncode}):\n"
                f"{done.stdout[-1500:]}\n{done.stderr[-3000:]}"
            )
    return None


@pytest.fixture(scope="module")
def release(request: pytest.FixtureRequest, kind_context: str) -> Iterator[_Release]:
    if shutil.which("helm") is None:
        pytest.skip("helm is not on PATH")
    digests = _digests()
    args = _helm_args(kind_context, digests, _api_cidr())
    rel = _Release(kind_context, args)
    # An empty cluster: only the namespaces (pre-created with PSA labels, like the smoke test).
    for namespace in (PLATFORM_NAMESPACE, LABS_NAMESPACE):
        _wait_namespace_not_terminating(kind_context, namespace)
        manifest = json.dumps(
            {"apiVersion": "v1", "kind": "Namespace",
             "metadata": {"name": namespace, "labels": PSA_LABELS}}
        )
        _kubectl(kind_context, "apply", "-f", "-", input_text=manifest)
    rel.install_failure = _bootstrap_install(kind_context, args)
    yield rel
    if request.session.testsfailed:
        # Req 16.2: leave the Kind_Cluster and the release in place for inspection.
        return
    subprocess.run(
        ["helm", "uninstall", RELEASE, "--kube-context", kind_context,
         "-n", PLATFORM_NAMESPACE, "--wait"],
        capture_output=True, timeout=300, check=False,
    )
    subprocess.run(
        ["kubectl", "--context", kind_context, "delete", "namespace", PLATFORM_NAMESPACE,
         "--ignore-not-found", "--wait=false"],
        capture_output=True, timeout=120, check=False,
    )


# --------------------------------------------------------------------------- cluster tests


# Claims: 16.1, 16.2, 8.8, 8.9
@pytest.mark.kind
def test_install_on_empty_cluster_reaches_ready_within_600_seconds(release: _Release) -> None:
    pending = _wait_ready(release.context)
    if release.install_failure or pending:
        events = _kubectl(
            release.context, "get", "events", "-n", PLATFORM_NAMESPACE,
            "--sort-by=.lastTimestamp",
        )
        pytest.fail(
            "stack not Ready within 600 s of the install (cluster left in place for inspection).\n"
            f"Workloads not Ready: {pending or 'none listed'}\n"
            f"{release.install_failure or ''}\nEvents:\n{events[-3000:]}"
        )
    release.hooks_after_install = _hook_uids(release.context)
    assert all(release.hooks_after_install.values())


# Claims: 16.1
@pytest.mark.kind
def test_phase3_end_to_end_flow_passes(release: _Release) -> None:
    if release.install_failure or not release.hooks_after_install:
        pytest.skip("stack is not Ready; see the install test")
    npx = shutil.which("npx")
    if npx is None:
        pytest.skip("npx is not on PATH")
    # The journeys need the seed content in the database (playwright.e2e.config.ts prerequisites).
    assert "content synced" in _seed_content(release.context)
    work = WEB_APP / KIND_PLAYWRIGHT_CONFIG
    mail_log = REPO_ROOT / ".e2e" / "kind-api-mail.log"
    mail_log.parent.mkdir(exist_ok=True)
    # The real journeys use the browser: trust the ingress self-signed certificate.
    work.write_text(
        "import base from './playwright.e2e.config';\n"
        "import { defineConfig } from '@playwright/test';\n"
        "export default defineConfig({ ...base, use: { ...base.use, ignoreHTTPSErrors: true } });\n",
        encoding="utf-8",
    )
    forward = logs = None
    try:
        with mail_log.open("wb") as log_file:
            forward = subprocess.Popen(
                ["kubectl", "--context", release.context, "port-forward", "-n", "ingress-nginx",
                 "svc/ingress-nginx-controller", f"{INGRESS_LOCAL_PORT}:443"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            logs = subprocess.Popen(
                ["kubectl", "--context", release.context, "logs", "-f", "-n", PLATFORM_NAMESPACE,
                 f"deployment/{RELEASE}-api"],
                stdout=log_file, stderr=subprocess.STDOUT,
            )
            for _ in range(30):
                if _port_open(INGRESS_LOCAL_PORT):
                    break
                time.sleep(1)
            else:
                pytest.fail("ingress port-forward did not open")
            env = {
                **os.environ,
                "E2E_WEB_URL": PUBLIC_WEB_URL,
                "E2E_MAIL_LOG": str(mail_log),
            }
            done = subprocess.run(
                [npx, "playwright", "test", "-c", KIND_PLAYWRIGHT_CONFIG],
                cwd=WEB_APP, env=env, capture_output=True, text=True, timeout=1800, check=False,
            )
            assert done.returncode == 0, f"Phase 3 flow failed:\n{done.stdout[-4000:]}\n{done.stderr[-2000:]}"
    finally:
        for proc in (forward, logs):
            if proc is not None:
                proc.terminate()
        work.unlink(missing_ok=True)


# Claims: 8.8, 8.9
@pytest.mark.kind
def test_upgrade_replaces_hook_resources_cleanly(release: _Release) -> None:
    if release.install_failure or not release.hooks_after_install:
        pytest.skip("stack is not Ready; see the install test")
    _run(["helm", "upgrade", RELEASE, *release.args], timeout=READY_WAIT_SECONDS + 300)
    assert _wait_ready(release.context) == []
    after = _hook_uids(release.context)
    # before-hook-creation: every hook object is deleted and recreated, so its UID changes.
    assert hooks_replaced(release.hooks_after_install, after) == []
    status = json.loads(
        _run(["helm", "status", RELEASE, "--kube-context", release.context,
              "-n", PLATFORM_NAMESPACE, "-o", "json"], timeout=60)
    )
    # Revision 1 is the --no-hooks install, 2 the bootstrap upgrade, 3 this upgrade.
    assert status["info"]["status"] == "deployed" and status["version"] == 3
    job = json.loads(
        _kubectl(release.context, "get", "job", f"{RELEASE}-migration",
                 "-n", PLATFORM_NAMESPACE, "-o", "json")
    )
    assert job["status"].get("succeeded") == 1


# --------------------------------------------------------------------------- unit tests (no cluster)


def _item(kind: str, name: str, replicas: int, ready: int) -> dict[str, object]:
    return {"kind": kind, "metadata": {"name": name}, "spec": {"replicas": replicas},
            "status": {"readyReplicas": ready}}


# Claims: 16.2
def test_not_ready_workloads_names_missing_and_unready() -> None:
    items = [_item("Deployment", "edu-it-api", 2, 2), _item("Deployment", "edu-it-worker", 1, 0)]
    expected = ["deployment/edu-it-api", "deployment/edu-it-worker", "deployment/edu-it-web"]
    assert not_ready_workloads(items, expected) == ["deployment/edu-it-worker", "deployment/edu-it-web"]


# Claims: 16.1
def test_expected_workloads_cover_the_dev_stack() -> None:
    assert expected_workload_names("r") == [
        "deployment/r-api", "deployment/r-worker", "deployment/r-web",
        "statefulset/r-dev-postgres", "statefulset/r-dev-redis",
    ]


# Claims: 8.8, 8.9
def test_hooks_replaced_flags_unchanged_and_missing_uids() -> None:
    before = {"job/a": "1", "secret/b": "2", "configmap/c": "3"}
    assert hooks_replaced(before, {"job/a": "9", "secret/b": "2"}) == ["secret/b", "configmap/c"]
    assert hooks_replaced(before, {"job/a": "9", "secret/b": "8", "configmap/c": "7"}) == []
