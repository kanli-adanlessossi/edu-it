"""The shared provider suites on a real kind cluster (kubernetes-deployment task 6.23).

``KubernetesSandboxProvider`` over ``KubectlK8sApi`` (the kind API server) runs:

- the ``SandboxProvider`` contract suite of ``packages/infrastructure/tests`` (its test bodies are
  re-exported here unchanged; only the ``kind`` and ``provider`` fixtures are replaced), and
- the labs isolation, grading-isolation (tamper resistance) and Check-abuse suites of
  ``tests/labs``, selected with ``EDU_IT_LAB_SUITE_PROVIDER=kubernetes`` (task 5.4) in a pytest
  child process whose result is read from its JUnit report.

Zero skips and zero exclusions beyond the Docker_Provider run: the child run may skip only the one
test the Docker run also skips (cgroup ``cpu.stat`` unreadable); any other skip, failure or error
fails this test. Everything is ``kind``-marked: with no reachable kind cluster it is skipped (with
the reason) and the default run is unaffected. The lab images must be available to the cluster
(``kind load docker-image edu-it/lab-linux-base:dev --name edu-it``). kind has no gVisor/Kata, so
the provider runs with ``runc`` and no RuntimeClass, the dev-only mode of Req 2.12.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import types
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from pathlib import Path

import pytest
from edu_it_domain import config as cfg
from edu_it_domain.ports.sandbox import SandboxHandle, SandboxProvider

from tests.k8s.conftest import LABS_NAMESPACE, kind_context_name
from tests.labs import sandbox_backend as sb

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_SUITE = (
    REPO_ROOT / "packages" / "infrastructure" / "tests" / "test_sandbox_provider_contract.py"
)
LAB_SUITES = (
    "tests/labs/test_docker_isolation.py",
    "tests/labs/test_docker_grading_isolation.py",
    "tests/labs/test_docker_check_abuse.py",
)
#: The one skip the Docker_Provider run has too (test_docker_isolation.py: no cgroup cpu.stat).
DOCKER_RUN_SKIPS = ("cgroup cpu.stat not readable in the container",)
SUITE_TIMEOUT_S = 1800


def kind_suite_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """Environment that points the lab suites and ``make_provider`` at the kind cluster."""

    env = dict(os.environ if base is None else base)
    env[sb.BACKEND_ENV] = "kubernetes"
    env[sb.K8S_API_ENV] = "tests.k8s.kind_suite_api:kind_api"
    env[sb.K8S_NAMESPACE_ENV] = LABS_NAMESPACE
    env.setdefault("EDU_IT_KIND_CONTEXT", kind_context_name())
    # kind has no gVisor or Kata: plain runc without a RuntimeClass (dev only).
    env.setdefault("SANDBOX_RUNTIME_KIND", "runc")
    env.setdefault("SANDBOX_RUNTIME_CLASS", "")
    # The lab node pool of the kind cluster (deploy/kind/kind-config.yaml), as in production.
    env.setdefault("SANDBOX_NODE_SELECTOR", "edu-it.io/pool=labs")
    env.setdefault("SANDBOX_TOLERATIONS", "edu-it.io/labs=true:NoSchedule")
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(REPO_ROOT), env.get("PYTHONPATH")]))
    return env


# ------------------------------------------------------------- Sandbox_Port contract suite


def _load_contract_suite() -> object:
    spec = importlib.util.spec_from_file_location("sandbox_provider_contract_suite", CONTRACT_SUITE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_contract = _load_contract_suite()
CONTRACT_TESTS = sorted(name for name in vars(_contract) if name.startswith("test_"))


def _kind_copy(func: types.FunctionType) -> types.FunctionType:
    """A copy of a contract test, so the ``kind`` mark does not leak onto the original."""

    clone = types.FunctionType(
        func.__code__, func.__globals__, func.__name__, func.__defaults__, func.__closure__
    )
    clone.__kwdefaults__ = func.__kwdefaults__
    clone.__doc__ = func.__doc__
    return pytest.mark.kind(clone)


# The contract test bodies (unchanged) and their ``sandbox`` fixture.
for _name in CONTRACT_TESTS:
    globals()[_name] = _kind_copy(getattr(_contract, _name))
sandbox = _contract.sandbox


@pytest.fixture
def kind() -> str:
    return "kubernetes"


@pytest.fixture
def provider(
    kind_context: str, kind_labs_namespace: str, monkeypatch: pytest.MonkeyPatch
) -> Iterator[SandboxProvider]:
    for key, value in kind_suite_env().items():
        monkeypatch.setenv(key, value)
    yield sb.make_provider()


# Claims: 1.9, 5.7
@pytest.mark.kind
def test_contract_suite_is_complete() -> None:
    assert CONTRACT_TESTS == [
        "test_copy_in_to_grader_as_root_and_copy_out_round_trip",
        "test_copy_out_missing_source_raises_file_not_found",
        "test_destroy_is_idempotent",
        "test_list_sandboxes_tracks_live_sandboxes",
        "test_operations_after_destroy_raise_sandbox_gone",
        "test_provision_returns_handle_for_session",
    ]


# ------------------------------------------------------------- freeze verification on a real pod


# Claims: 5.4, 16.10
@pytest.mark.kind
def test_freeze_verification_sees_real_learner_processes(
    provider: SandboxProvider, sandbox: SandboxHandle
) -> None:
    """The listing GradeRun uses on Kubernetes must see the learner's processes. A listing as the
    grader sees only the grader container (separate process namespaces) and would be vacuously
    'all stopped'; this test fails in that case, on a real kernel."""

    from edu_it_application.labs.grade_run import _PS_LIST, _PS_LIST_IN_LEARNER, _parse_ps
    from edu_it_domain.labs.grading import learner_all_stopped

    env = {"PATH": "/usr/bin:/bin"}

    def run(argv: list[str], user: str):  # type: ignore[no-untyped-def]
        return provider.exec(sandbox, argv, as_user=user, env=env, cwd="/", timeout_s=20)

    detach = "sleep 300 >/dev/null 2>&1 </dev/null & disown"  # stdio closed: exec can return
    started = run(["/bin/bash", "-c", f"{detach}; {detach}"], "learner")
    assert started.exit_code == 0

    def verified_in_learner() -> bool:
        # Unbounded, as in GradeRun: a bounded exec runs under a learner-owned ``timeout``
        # wrapper, which would itself be a running learner process in the listing.
        listing = provider.exec(
            sandbox, _PS_LIST_IN_LEARNER, as_user="learner", env=env, cwd="/"
        )
        assert listing.exit_code == 0, listing.stderr
        return learner_all_stopped(_parse_ps(listing.stdout, True))

    assert verified_in_learner() is False  # running learner processes are really seen
    as_grader = run(_PS_LIST, "grader")
    assert as_grader.exit_code == 0
    assert b"sleep" not in as_grader.stdout  # the grader container cannot see them at all
    assert learner_all_stopped(_parse_ps(as_grader.stdout)) is True  # the vacuous truth

    stop = provider.exec(
        sandbox, ["/bin/kill", "-s", "STOP", "-1"], as_user="learner", env=env, cwd="/"
    )
    assert stop.exit_code == 0
    assert verified_in_learner() is True
    provider.exec(sandbox, ["/bin/kill", "-s", "CONT", "-1"], as_user="learner", env=env, cwd="/")
    assert verified_in_learner() is False


# ------------------------------------------------------------- node settings the suites rely on


def _kubelet_config(context: str, node: str) -> dict:
    done = subprocess.run(
        ["kubectl", "--context", context, "get", "--raw", f"/api/v1/nodes/{node}/proxy/configz"],
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert done.returncode == 0, done.stderr.decode("utf-8", "replace")
    return json.loads(done.stdout.decode("utf-8"))["kubeletconfig"]


# Claims: 2.4, 16.10
@pytest.mark.kind
def test_lab_nodes_enforce_the_pod_pid_limit_and_single_process_oom_kill(
    kind_context: str,
) -> None:
    """Fork-bomb and memory-loop containment on Kubernetes are kubelet settings, not container
    settings: ``podPidsLimit`` caps the whole pod, ``singleProcessOOMKill`` makes an OOM kill one
    process as in Docker. The suites prove the behaviour; this proves the node setting behind it."""

    done = subprocess.run(
        [
            "kubectl", "--context", kind_context, "get", "nodes",
            "-l", "edu-it.io/pool=labs", "-o", "jsonpath={.items[*].metadata.name}",
        ],
        capture_output=True,
        timeout=60,
        check=False,
    )
    nodes = done.stdout.decode().split()
    assert nodes, "no node of the labs pool"
    for node in nodes:
        config = _kubelet_config(kind_context, node)
        assert config["podPidsLimit"] == cfg.SANDBOX_PID_LIMIT, node
        assert config["singleProcessOOMKill"] is True, node


# ------------------------------------------------------------- labs suites on the cluster


def _outcomes(report: Path) -> tuple[int, list[str], list[str]]:
    """(tests run, failed/errored ids, skip descriptions) of a JUnit report."""

    total = 0
    bad: list[str] = []
    skipped: list[str] = []
    for case in ET.parse(report).getroot().iter("testcase"):
        total += 1
        ident = f"{case.get('classname')}::{case.get('name')}"
        if case.find("failure") is not None or case.find("error") is not None:
            bad.append(ident)
        skip = case.find("skipped")
        if skip is not None:
            skipped.append(f"{ident}: {skip.get('message') or ''}")
    return total, bad, skipped


# Claims: 1.9, 5.7, 16.10
@pytest.mark.kind
@pytest.mark.parametrize("suite", LAB_SUITES, ids=[Path(s).stem for s in LAB_SUITES])
def test_lab_suite_passes_with_the_kubernetes_provider(
    suite: str, kind_context: str, kind_labs_namespace: str, tmp_path: Path
) -> None:
    report = tmp_path / "report.xml"
    done = subprocess.run(
        [sys.executable, "-m", "pytest", suite, "-p", "no:cacheprovider", f"--junitxml={report}"],
        cwd=REPO_ROOT,
        env=kind_suite_env(),
        capture_output=True,
        timeout=SUITE_TIMEOUT_S,
        check=False,
    )
    output = done.stdout.decode("utf-8", "replace")[-4000:]
    assert report.exists(), f"no report written:\n{output}"
    total, bad, skipped = _outcomes(report)
    assert total > 0, f"{suite} collected no tests:\n{output}"
    assert not bad, f"{suite} failed with the Kubernetes provider: {bad}\n{output}"
    extra = [s for s in skipped if not any(allowed in s for allowed in DOCKER_RUN_SKIPS)]
    assert not extra, f"{suite} skipped beyond the Docker run: {extra}"
    assert done.returncode == 0, output
