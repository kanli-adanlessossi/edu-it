"""Docker tests for the code-exercise harness ``run_cases.py`` (hands-on-labs task 12.2).

Requirements 14.3, 14.4, 14.10, 14.11, 14.12, 28.6, 28.7, 29.7, 29.9, evidenced against a real
kernel. The real harness from ``content/linux-fundamentals/lab-bundles/world-writable-entries``
is copied, with test-owned case files, into a temporary bundle and started the way ``GradeRun``
starts it: ``/grader`` is populated as root, ``/grader/run`` is created by the Grader_User, and
the harness runs as the Grader_User with the fixed clean environment and ``/grader/run`` as cwd.
``/grader`` is 0700-style closed to the learner and to root without ``CAP_DAC_OVERRIDE``, so
everything under ``/grader/run`` is inspected as the grader, as ``GradeRun`` does.

Tests carry the ``docker`` marker and are skipped without a daemon or the local
``edu-it/lab-python:dev`` image. Learner programs are written with LF endings; nothing is written
outside ``tmp_path`` locally.
"""

from __future__ import annotations

import base64
import json
import shutil
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from edu_it_application.labs import GradeRun, RecordCheckResult
from edu_it_domain import config as cfg
from edu_it_domain.labs.grading import ExpectedCase, parse_harness_report
from edu_it_domain.labs.models import (
    CheckOutcome,
    GradingRun,
    LabSession,
    RunStatus,
    SessionStatus,
)
from edu_it_domain.labs.sandbox_spec import build_sandbox_spec
from edu_it_domain.ports.lab_catalog import (
    LabCheckDefinition,
    LabDefinition,
    LabTaskDefinition,
)
from edu_it_domain.ports.sandbox import ExecResult, SandboxHandle
from edu_it_infrastructure.clock import System_Clock
from edu_it_infrastructure.fakes import (
    Fake_LabCatalog,
    In_Memory_GradingRunRepository,
    In_Memory_LabSessionRepository,
    In_Memory_MasteryRepository,
)
from edu_it_infrastructure.sandbox.docker_provider import DockerSandboxProvider

IMAGE = "edu-it/lab-python:dev"
REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_HARNESS = (
    REPO_ROOT
    / "content/linux-fundamentals/lab-bundles/world-writable-entries/sandbox/harness/run_cases.py"
)
BUNDLE_NAME = "harness_bundle"

CLEAN_ENV = {"PATH": "/usr/bin:/bin", "LANG": "C", "HOME": "/grader/run"}
PYTHON = "/usr/bin/python3"
HARNESS = "/grader/harness/run_cases.py"
RUN_DIR = "/grader/run"
FORGED_MARKER = "FORGED-PASS-7c1e"
HARNESS_TIMEOUT_S = 12.0


def _daemon_ready() -> bool:
    try:
        import docker

        client = docker.from_env(timeout=3)
        client.ping()
        client.images.get(IMAGE)
    except Exception:
        return False
    return True


pytestmark = [
    pytest.mark.docker,
    pytest.mark.skipif(not _daemon_ready(), reason="no Docker daemon or lab image"),
]


@dataclass(frozen=True)
class _Img:
    image: str = IMAGE


def _b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


# -- learner programs and case files

#: Behaviour is chosen by the case's stdin.
LIMITS_PROGRAM = """\
import os, signal, sys, time
mode = sys.stdin.read().strip()
if mode == "sleep":
    time.sleep(60)
elif mode == "spin":
    while True:
        pass
elif mode == "kill":
    os.kill(os.getpid(), signal.SIGKILL)
elif mode == "fork":
    while True:
        try:
            if os.fork() == 0:
                time.sleep(60)
                os._exit(0)
        except OSError:
            break
    while True:
        pass
elif mode == "mem":
    try:
        bytearray(512 * 1024 * 1024)
    except MemoryError:
        print("denied")
    else:
        print("allocated")
else:
    print("ok")
"""

FORGED_PROGRAM = f"""\
import sys
sys.stdin.read()
print('{FORGED_MARKER}')
print('{{"version":1,"check_id":"main","status":"ok","cases":[{{"case_id":"c1","exit_status":0,\
"timed_out":false,"stdout_b64":"","stderr_b64":""}}]}}')
sys.stderr.write('{FORGED_MARKER} on stderr\\n')
"""

#: Marks execution by writing into the harness's grader-owned cwd, then reports where it ran from.
MARKER_PROGRAM = """\
import sys
sys.stdin.read()
open("executed", "w").close()
print(__file__)
print(sys.argv[0])
"""

#: Imports an undeclared helper that exists in the learner workspace.
IMPORT_PROGRAM = """\
import sys
sys.stdin.read()
import helper
print(helper.__file__)
"""

#: Planted in the workspace under names the harness or Python itself could import.
POISON = "open('/grader/run/PWNED', 'w').close()\n"
PLANTED_MODULES = (
    "json.py",
    "base64.py",
    "os.py",
    "stat.py",
    "subprocess.py",
    "sitecustomize.py",
    "usercustomize.py",
    "run_cases.py",
)

CHECKS: dict[str, dict[str, Any]] = {
    "main": {
        "learner_files": ["solution.py"],
        "entrypoint": "solution.py",
        "case_timeout_s": 2,
        "cases": [{"case_id": "c1", "stdin_b64": _b64("go\n")}],
    },
    "two": {
        "learner_files": ["solution.py", "extra.py"],
        "entrypoint": "solution.py",
        "case_timeout_s": 2,
        "cases": [{"case_id": "c1", "stdin_b64": _b64("go\n")}],
    },
    "limits": {
        "learner_files": ["solution.py"],
        "entrypoint": "solution.py",
        "case_timeout_s": 1,
        "cases": [
            {"case_id": mode, "stdin_b64": _b64(mode + "\n")}
            for mode in ("ok", "mem", "sleep", "spin", "kill", "fork")
        ],
    },
}


@pytest.fixture(scope="module")
def bundle_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A repository-derived bundle: the real harness plus test-owned ``cases/*.json``."""
    root = tmp_path_factory.mktemp("harness-bundle")
    sandbox = root / BUNDLE_NAME / "sandbox"
    (sandbox / "harness").mkdir(parents=True)
    shutil.copyfile(REAL_HARNESS, sandbox / "harness" / "run_cases.py")
    (sandbox / "cases").mkdir()
    for check_id, doc in CHECKS.items():
        (sandbox / "cases" / f"{check_id}.json").write_text(
            json.dumps(doc), encoding="utf-8", newline="\n"
        )
    return root


@pytest.fixture(scope="module")
def provider() -> DockerSandboxProvider:
    return DockerSandboxProvider()


@pytest.fixture
def sandbox(provider: DockerSandboxProvider) -> Iterator[SandboxHandle]:
    handle = provider.provision(build_sandbox_spec(_Img(), "pytest-harness"))
    try:
        yield handle
    finally:
        provider.destroy(handle)


# -- helpers


def _sh(
    provider: DockerSandboxProvider,
    handle: SandboxHandle,
    script: str,
    *,
    user: str = "learner",
) -> ExecResult:
    return provider.exec(
        handle,
        ["/bin/bash", "-c", script],
        as_user=user,
        env={"PATH": "/usr/bin:/bin"},
        cwd="/workspace" if user == "learner" else "/",
        timeout_s=30,
    )


def _put(
    provider: DockerSandboxProvider, handle: SandboxHandle, tmp_path: Path, name: str, text: str
) -> None:
    """Create ``/workspace/<name>`` as the learner."""
    src = tmp_path / f"learner-{name}"
    src.write_text(text, encoding="utf-8", newline="\n")
    provider.copy_in(handle, src, f"/workspace/{name}")


def _prepare_grader(
    provider: DockerSandboxProvider, handle: SandboxHandle, bundle_root: Path
) -> None:
    """Populate ``/grader`` exactly as ``GradeRun`` does (root copy, grader-owned ``run``)."""
    provider.copy_in(handle, bundle_root / BUNDLE_NAME / "sandbox", "/grader", as_user="root")
    for argv, user in (
        (["/bin/chmod", "0775", "/grader"], "root"),
        (["/bin/mkdir", "-m", "0700", "--", RUN_DIR], "grader"),
        (["/bin/chmod", "0755", "/grader"], "root"),
    ):
        res = provider.exec(handle, argv, as_user=user, env=CLEAN_ENV, cwd="/", timeout_s=5)
        assert res.exit_code == 0, argv


def _run_harness(
    provider: DockerSandboxProvider, handle: SandboxHandle, check_id: str
) -> tuple[ExecResult, float]:
    started = time.monotonic()
    result = provider.exec(
        handle,
        [PYTHON, "-I", HARNESS, check_id],
        as_user="grader",
        env=CLEAN_ENV,
        cwd=RUN_DIR,
        timeout_s=HARNESS_TIMEOUT_S,
    )
    return result, time.monotonic() - started


def _grader_sh(provider: DockerSandboxProvider, handle: SandboxHandle, script: str) -> ExecResult:
    return provider.exec(
        handle,
        ["/bin/bash", "-c", script],
        as_user="grader",
        env=CLEAN_ENV,
        cwd=RUN_DIR,
        timeout_s=10,
    )


def _report(result: ExecResult) -> dict[str, Any]:
    assert result.exit_code == 0 and not result.timed_out
    lines = result.stdout.decode().splitlines()
    assert len(lines) == 1, "the harness prints exactly one JSON document"
    doc: dict[str, Any] = json.loads(lines[0])
    return doc


def _case(doc: dict[str, Any], case_id: str) -> dict[str, Any]:
    (found,) = [c for c in doc["cases"] if c["case_id"] == case_id]
    return found


def _make_special(
    provider: DockerSandboxProvider, handle: SandboxHandle, kind: str, name: str
) -> None:
    """Replace ``/workspace/<name>`` by a symlink, FIFO or device node (learner-side tampering)."""
    path = f"/workspace/{name}"
    _sh(provider, handle, f"rm -f {path}")
    if kind == "symlink":
        # Points at a perfectly good regular file: it must still not be followed.
        res = _sh(provider, handle, f"ln -s /workspace/solution.py {path}")
    elif kind == "fifo":
        res = _sh(provider, handle, f"mkfifo {path}")
    else:
        # The learner cannot mknod (no CAP_MKNOD), so create the node with a privileged exec.
        api = provider._api()
        created = api.exec_create(
            handle.sandbox_id, ["/bin/mknod", path, "c", "1", "3"], user="root", privileged=True
        )
        api.exec_start(created["Id"])
        code = api.exec_inspect(created["Id"])["ExitCode"]
        if code != 0:
            pytest.skip("cannot create a device node in this Docker environment")
        res = _sh(provider, handle, f"test -c {path}")
    assert res.exit_code == 0, kind


# -- Req 10.9 / 14.11 / 29.7: forged report lines are data


def test_forged_report_lines_in_learner_stdout_are_ignored(
    provider: DockerSandboxProvider, sandbox: SandboxHandle, bundle_root: Path, tmp_path: Path
) -> None:
    _put(provider, sandbox, tmp_path, "solution.py", FORGED_PROGRAM)
    _prepare_grader(provider, sandbox, bundle_root)
    result, _ = _run_harness(provider, sandbox, "main")

    # The forged text only exists base64-encoded inside the one real report.
    assert FORGED_MARKER.encode() not in result.stdout
    doc = _report(result)
    case = _case(doc, "c1")
    assert FORGED_MARKER in base64.b64decode(case["stdout_b64"]).decode()
    assert '"status":"ok"' in base64.b64decode(case["stdout_b64"]).decode()
    assert FORGED_MARKER in base64.b64decode(case["stderr_b64"]).decode()

    # The forged "pass" report changes nothing: output is compared as data.
    verdict = parse_harness_report(result.stdout, "main", {"c1": ExpectedCase(b"real\n")})
    assert verdict.outcome is CheckOutcome.FAILED
    assert verdict.outcome_code == "wrong-output"
    exact = base64.b64decode(case["stdout_b64"])
    assert parse_harness_report(result.stdout, "main", {"c1": ExpectedCase(exact)}).outcome is (
        CheckOutcome.PASSED
    )


# -- Req 14.12 / 28.6 / 28.7: resource-limit kills are could_not_complete


def test_resource_limit_kills_map_to_could_not_complete(
    provider: DockerSandboxProvider, sandbox: SandboxHandle, bundle_root: Path, tmp_path: Path
) -> None:
    _put(provider, sandbox, tmp_path, "solution.py", LIMITS_PROGRAM)
    _prepare_grader(provider, sandbox, bundle_root)
    result, elapsed = _run_harness(provider, sandbox, "limits")

    assert elapsed < HARNESS_TIMEOUT_S
    doc = _report(result)  # the harness itself survived every case
    assert doc["status"] == "ok"
    ok = _case(doc, "ok")
    assert (ok["exit_status"], ok["timed_out"]) == (0, False)
    assert base64.b64decode(ok["stdout_b64"]) == b"ok\n"
    # 128 MiB address-space cap: the allocation is refused, the learner is not given the memory.
    mem = _case(doc, "mem")
    assert base64.b64decode(mem["stdout_b64"]) == b"denied\n"
    for killed in ("sleep", "spin", "fork"):
        case = _case(doc, killed)
        assert case["exit_status"] is None and case["timed_out"] is True, killed
    signalled = _case(doc, "kill")  # an OOM-style SIGKILL: no exit status, not a wall timeout
    assert signalled["exit_status"] is None

    expected = {c["case_id"]: ExpectedCase(b"ok\n") for c in doc["cases"]}
    verdict = parse_harness_report(result.stdout, "limits", expected)
    assert verdict.outcome is CheckOutcome.COULD_NOT_COMPLETE
    assert verdict.outcome_code == "case-timeout"
    # Everything the runaway started is gone and the Sandbox is still usable.
    left = _sh(provider, sandbox, "/bin/ps -e -o user= -o comm=", user="grader")
    assert [
        ln
        for ln in left.stdout.decode().splitlines()
        if ln.split()[:1] == ["grader"] and "python" in ln
    ] == []
    assert provider.health(sandbox).healthy


# -- Req 14.3 / 14.4: nothing is imported from the learner workspace


def test_no_test_code_helper_or_config_is_imported_from_the_workspace(
    provider: DockerSandboxProvider, sandbox: SandboxHandle, bundle_root: Path, tmp_path: Path
) -> None:
    _put(provider, sandbox, tmp_path, "solution.py", IMPORT_PROGRAM)
    _put(provider, sandbox, tmp_path, "helper.py", POISON + "__file__ = 'WORKSPACE-HELPER'\n")
    for name in PLANTED_MODULES:
        _put(provider, sandbox, tmp_path, name, POISON)
    _put(provider, sandbox, tmp_path, "main.json", '{"learner_files": ["evil.py"]}\n')
    _prepare_grader(provider, sandbox, bundle_root)
    result, _ = _run_harness(provider, sandbox, "main")

    doc = _report(result)
    assert doc["status"] == "ok" and doc["check_id"] == "main"
    case = _case(doc, "c1")
    # The undeclared workspace helper cannot be imported by the executed copy ...
    assert case["exit_status"] == 1
    assert b"ModuleNotFoundError" in base64.b64decode(case["stderr_b64"])
    assert base64.b64decode(case["stdout_b64"]) == b""
    # ... and neither the harness nor the learner interpreter imported any planted module.
    assert _grader_sh(provider, sandbox, "test ! -e /grader/run/PWNED").exit_code == 0
    # Case inputs came from /grader/cases, not from a workspace file of the same name.
    verdict = parse_harness_report(result.stdout, "main", {"c1": ExpectedCase(b"")})
    assert verdict.outcome is CheckOutcome.FAILED


# -- Req 14.10 / 14.11: the executed path is the grader-owned copy


def test_executed_path_is_the_grader_owned_copy(
    provider: DockerSandboxProvider, sandbox: SandboxHandle, bundle_root: Path, tmp_path: Path
) -> None:
    _put(provider, sandbox, tmp_path, "solution.py", MARKER_PROGRAM)
    _prepare_grader(provider, sandbox, bundle_root)
    result, _ = _run_harness(provider, sandbox, "main")

    case = _case(_report(result), "c1")
    printed = base64.b64decode(case["stdout_b64"]).decode().split()
    assert printed == ["/grader/run/learner/solution.py"] * 2
    assert not any(p.startswith("/workspace") for p in printed)
    # The copy is owned by the Grader_User, closed to others, and holds the learner's bytes.
    info = _grader_sh(
        provider,
        sandbox,
        "stat -c '%U %a' /grader/run/learner /grader/run/learner/solution.py; "
        "cmp /grader/run/learner/solution.py /workspace/solution.py",
    )
    assert info.exit_code == 0
    assert info.stdout.decode().split("\n")[:2] == ["grader 700", "grader 600"]
    # The program ran in the grader-owned working directory, never in the workspace.
    assert _grader_sh(provider, sandbox, "test -e /grader/run/work/executed").exit_code == 0
    assert _sh(provider, sandbox, "test ! -e /workspace/executed").exit_code == 0


# -- Req 14.3 / 14.11 / 29.9: symlink, FIFO and device node are rejected


@pytest.mark.parametrize("target", ["extra.py", "solution.py"])
@pytest.mark.parametrize("kind", ["symlink", "fifo", "device"])
def test_special_learner_file_is_rejected_without_blocking_or_executing(
    provider: DockerSandboxProvider,
    sandbox: SandboxHandle,
    bundle_root: Path,
    tmp_path: Path,
    kind: str,
    target: str,
) -> None:
    _put(provider, sandbox, tmp_path, "solution.py", MARKER_PROGRAM)
    _put(provider, sandbox, tmp_path, "extra.py", "print('declared helper')\n")
    _make_special(provider, sandbox, kind, target)
    _prepare_grader(provider, sandbox, bundle_root)
    result, elapsed = _run_harness(provider, sandbox, "two")

    # Never blocks: a FIFO with no writer would hang a blocking open until the exec timeout.
    assert not result.timed_out
    assert elapsed < 5
    doc = _report(result)
    assert doc["status"] == "unsafe-learner-file"
    assert doc["cases"] == []
    # Nothing was copied and nothing was executed.
    gone = _grader_sh(
        provider, sandbox, "test ! -e /grader/run/learner && test ! -e /grader/run/work/executed"
    )
    assert gone.exit_code == 0
    # The worker maps it to could_not_complete.
    verdict = parse_harness_report(result.stdout, "two", {"c1": ExpectedCase(b"")})
    assert verdict.outcome is CheckOutcome.COULD_NOT_COMPLETE
    assert verdict.outcome_code == "unsafe-learner-file"


# -- Req 29.9 end to end: the whole run is could_not_complete


def _grade(
    provider: DockerSandboxProvider,
    handle: SandboxHandle,
    bundle_root: Path,
    check_id: str,
    expected_stdout: bytes,
) -> tuple[RunStatus | None, Any]:
    expected_dir = bundle_root / BUNDLE_NAME / "expected"
    expected_dir.mkdir(exist_ok=True)
    (expected_dir / f"{check_id}.json").write_text(
        json.dumps({"cases": {"c1": {"stdout_b64": base64.b64encode(expected_stdout).decode()}}}),
        encoding="utf-8",
        newline="\n",
    )
    lab = LabDefinition(
        lab_id="harness-lab",
        kind="code",
        image=IMAGE,
        concept_ids=(),
        tasks=(LabTaskDefinition("t1", required=True),),
        checks=(LabCheckDefinition(check_id, "t1", 12.0),),
        bundle_path=BUNDLE_NAME,
    )
    clock = System_Clock()
    runs = In_Memory_GradingRunRepository()
    sessions = In_Memory_LabSessionRepository(runs)
    now = clock.now()
    sessions.add_if_no_active(
        LabSession("s1", "u1", lab.lab_id, SessionStatus.GRADING, now, now + cfg.LAB_SESSION_TTL)
    )
    sessions.transition(
        "s1",
        expected={SessionStatus.GRADING},
        to=SessionStatus.GRADING,
        sandbox_ref=handle.sandbox_id,
    )
    runs.add(GradingRun("r1", "s1", RunStatus.RUNNING, now, now + cfg.LAB_GRADING_RUN_DEADLINE))
    job = GradeRun(
        sessions=sessions,
        runs=runs,
        catalog=Fake_LabCatalog([lab]),
        sandbox=provider,
        record=RecordCheckResult(runs=runs, mastery=In_Memory_MasteryRepository(), clock=clock),
        clock=clock,
        bundle_root=bundle_root,
    )
    status = job.execute("r1")
    run = runs.get("r1")
    assert run is not None
    return status, {r.check_id: r for r in run.results}


@pytest.mark.parametrize("kind", ["symlink", "fifo", "device"])
def test_grade_run_with_special_learner_file_is_could_not_complete(
    provider: DockerSandboxProvider,
    sandbox: SandboxHandle,
    bundle_root: Path,
    tmp_path: Path,
    kind: str,
) -> None:
    _put(provider, sandbox, tmp_path, "solution.py", MARKER_PROGRAM)
    _put(provider, sandbox, tmp_path, "extra.py", "print('declared helper')\n")
    _make_special(provider, sandbox, kind, "extra.py")
    status, results = _grade(provider, sandbox, bundle_root, "two", b"")

    assert status is RunStatus.COULD_NOT_COMPLETE
    assert results["two"].outcome is CheckOutcome.COULD_NOT_COMPLETE
    assert results["two"].outcome_code == "unsafe-learner-file"


def test_grade_run_ignores_forged_report_lines(
    provider: DockerSandboxProvider, sandbox: SandboxHandle, bundle_root: Path, tmp_path: Path
) -> None:
    _put(provider, sandbox, tmp_path, "solution.py", FORGED_PROGRAM)
    status, results = _grade(provider, sandbox, bundle_root, "main", b"real\n")

    assert status is RunStatus.FAILED
    assert results["main"].outcome is CheckOutcome.FAILED
    assert results["main"].outcome_code == "wrong-output"
