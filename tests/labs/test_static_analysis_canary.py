"""Static-analysis and canary tests (hands-on-labs task 8.6; Req 1.3, 5.1-5.5).

Two guarantees that the api and worker never execute learner-controlled content:

* An AST scan over ``apps/api`` and ``apps/worker`` fails on ``eval``, ``exec``, ``compile``,
  ``subprocess``, ``os.system`` (and its exec/spawn/popen relatives) and dynamic imports. The
  Docker adapter is the only place that may talk to a runtime, so it is the only exemption;
  the Fake_SandboxProvider must import no subprocess, socket or Docker module.
* A canary payload that would write a host marker if executed is planted as a learner file,
  in Check output and in stderr; after a full session and grading flow the marker is absent.
  The Docker leg carries the ``docker`` marker and is skipped without a daemon.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from edu_it_application.labs import ProvisionOutcome, ProvisionSandbox
from edu_it_application.labs.grade_run import GradeRun, RecordCheckResult
from edu_it_application.labs.request_grading import RequestGradingUseCase
from edu_it_application.labs.save_files import SaveFilesUseCase
from edu_it_domain import config
from edu_it_domain.labs.models import LabSession, RunStatus, SessionStatus
from edu_it_domain.labs.sandbox_spec import build_sandbox_spec
from edu_it_domain.ports.lab_catalog import LabCheckDefinition, LabDefinition, LabTaskDefinition
from edu_it_domain.ports.sandbox import ExecResult, SandboxHandle, SandboxProvider
from edu_it_infrastructure.fakes import (
    Fake_LabCatalog,
    Fake_SandboxProvider,
    In_Memory_GradingRunRepository,
    In_Memory_IdempotencyStore,
    In_Memory_LabSessionRepository,
    In_Memory_MasteryRepository,
    In_Memory_TaskQueue,
    Sequential_IdGenerator,
    Test_Clock,
)

REPO = Path(__file__).resolve().parents[2]
SCANNED_ROOTS = (REPO / "apps" / "api", REPO / "apps" / "worker")
FAKE_PROVIDER = (
    REPO / "packages" / "infrastructure" / "edu_it_infrastructure" / "fakes" / "sandbox_provider.py"
)

#: The only adapter allowed to use a runtime client; nothing under ``apps`` is exempt.
DOCKER_ADAPTER_DIR = REPO / "packages" / "infrastructure" / "edu_it_infrastructure" / "sandbox"

_BANNED_CALLS = {"eval", "exec", "compile", "__import__"}
_BANNED_MODULES = {"subprocess", "importlib", "pty", "ctypes"}
_BANNED_OS_ATTRS = {"system", "popen", "fork", "forkpty", "execv", "execve", "execl", "execlp"}
_BANNED_OS_PREFIXES = ("spawn", "exec")
_FAKE_BANNED_MODULES = {"subprocess", "socket", "docker", "ssl", "asyncio", "pty", "ctypes"}


def _python_files(root: Path) -> list[Path]:
    skip = {"__pycache__", ".venv", "node_modules", "tests"}
    return sorted(p for p in root.rglob("*.py") if not skip.intersection(p.relative_to(root).parts))


def _is_banned_os_attr(name: str) -> bool:
    return name in _BANNED_OS_ATTRS or name.startswith(_BANNED_OS_PREFIXES)


def find_violations(source: str) -> list[str]:
    """Return a description of each forbidden construct in ``source``."""
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        line = getattr(node, "lineno", 0)
        if isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Name) and fn.id in _BANNED_CALLS:
                found.append(f"{fn.id}() at line {line}")
            elif (
                isinstance(fn, ast.Attribute)
                and isinstance(fn.value, ast.Name)
                and fn.value.id == "os"
                and _is_banned_os_attr(fn.attr)
            ):
                found.append(f"os.{fn.attr}() at line {line}")
            elif (
                isinstance(fn, ast.Attribute)
                and isinstance(fn.value, ast.Name)
                and fn.value.id in {"builtins", "importlib"}
                and fn.attr in _BANNED_CALLS | {"import_module"}
            ):
                found.append(f"{fn.value.id}.{fn.attr}() at line {line}")
        elif isinstance(node, ast.Import):
            found += [
                f"import {a.name} at line {line}"
                for a in node.names
                if a.name.split(".")[0] in _BANNED_MODULES
            ]
        elif isinstance(node, ast.ImportFrom):
            module = (node.module or "").split(".")[0]
            if module in _BANNED_MODULES:
                found.append(f"from {node.module} import ... at line {line}")
            elif module == "os":
                found += [
                    f"from os import {a.name} at line {line}"
                    for a in node.names
                    if _is_banned_os_attr(a.name)
                ]
    return found


def _imported_modules(source: str) -> set[str]:
    mods: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            mods |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module.split(".")[0])
    return mods


# --- the scanner itself ------------------------------------------------------


@pytest.mark.parametrize(
    "snippet",
    [
        "eval('1')",
        "exec('x=1')",
        "compile('1', 'f', 'eval')",
        "import subprocess",
        "from subprocess import run",
        "import os\nos.system('id')",
        "import os\nos.execv('/bin/sh', [])",
        "from os import system",
        "__import__('os')",
        "import importlib\nimportlib.import_module('x')",
        "from importlib import import_module",
    ],
)
def test_scanner_flags_each_forbidden_construct(snippet: str) -> None:
    assert find_violations(snippet)


@pytest.mark.parametrize(
    "snippet",
    ["import re\nre.compile('x')", "sandbox.exec(h, ['ls'])", "import os\nos.path.join('a', 'b')"],
)
def test_scanner_allows_lookalikes(snippet: str) -> None:
    assert find_violations(snippet) == []


# --- AST scan over apps ------------------------------------------------------


def test_scan_roots_exist_and_contain_code() -> None:
    for root in SCANNED_ROOTS:
        assert root.is_dir()
        assert _python_files(root)


@pytest.mark.parametrize("root", SCANNED_ROOTS, ids=lambda p: p.name)
def test_apps_have_no_dynamic_execution_or_subprocess(root: Path) -> None:
    problems: list[str] = []
    for path in _python_files(root):
        if DOCKER_ADAPTER_DIR in path.parents:
            continue
        found = find_violations(path.read_text("utf-8"))
        problems += [f"{path.relative_to(REPO)}: {v}" for v in found]
    assert problems == []


def test_fake_provider_imports_no_subprocess_or_socket() -> None:
    imported = _imported_modules(FAKE_PROVIDER.read_text("utf-8"))
    assert imported.isdisjoint(_FAKE_BANNED_MODULES), imported & _FAKE_BANNED_MODULES
    assert find_violations(FAKE_PROVIDER.read_text("utf-8")) == []


# --- canary ------------------------------------------------------------------


def _canary_payload(marker: Path) -> str:
    """Python source that would write ``marker`` on the host if anything executed it."""
    target = marker.as_posix()
    return (
        "import os\n"
        f"open({target!r}, 'w').write('pwned')\n"
        f"os.system('echo pwned > {target}')\n"
        f"__import__('subprocess').run(['touch', {target!r}])\n"
        f"exec(\"open({target!r}, 'w').write('x')\")\n"
    )


_LAB = LabDefinition(
    lab_id="lab1",
    kind="code",
    image="img",
    concept_ids=(),
    tasks=(LabTaskDefinition("t1", required=True),),
    checks=(LabCheckDefinition("c1", "t1", 5.0),),
    bundle_path="bundle",
    learner_files=("main.py",),
)


def _assert_no_marker(marker: Path) -> None:
    assert not marker.exists(), "canary payload executed on the host"


def _fake_flow(tmp_path: Path, marker: Path) -> None:
    payload = _canary_payload(marker)
    bundle = tmp_path / "bundles" / "bundle" / "sandbox" / "checks"
    bundle.mkdir(parents=True)
    (bundle / "c1.sh").write_text("exit 0\n", encoding="utf-8")
    setup = tmp_path / "setup.sh"
    setup.write_text(payload, encoding="utf-8")  # hostile setup script content, copied as data

    clock = Test_Clock()
    runs = In_Memory_GradingRunRepository()
    sessions = In_Memory_LabSessionRepository(runs)
    queue = In_Memory_TaskQueue()
    provider = Fake_SandboxProvider(clock)
    catalog = Fake_LabCatalog([_LAB])
    # Check output and stderr carry the payload too; it must be treated as data.
    provider.exec_handler = lambda _sb, _cmd, _user: ExecResult(
        0, payload.encode(), payload.encode(), False
    )

    now = clock.now()
    sessions.add_if_no_active(
        LabSession(
            stable_id="s1",
            user_id="u1",
            lab_id="lab1",
            status=SessionStatus.PROVISIONING,
            created_at=now,
            ttl_at=now + config.LAB_SESSION_TTL,
        )
    )
    assert (
        ProvisionSandbox(
            sessions=sessions,
            catalog=catalog,
            sandbox=provider,
            clock=clock,
            setup_scripts=lambda _lab: setup,
        ).execute("s1")
        is ProvisionOutcome.READY
    )

    SaveFilesUseCase(
        sessions=sessions, catalog=catalog, sandbox=provider, clock=clock, provider_name="fake"
    ).execute("u1", "s1", {"main.py": payload})
    sandbox = next(iter(provider._sandboxes.values()))
    assert sandbox.files["/workspace/main.py"] == payload.encode()  # delivered, never run

    run = (
        RequestGradingUseCase(
            sessions=sessions,
            runs=runs,
            queue=queue,
            idempotency=In_Memory_IdempotencyStore(),
            clock=clock,
            ids=Sequential_IdGenerator("r"),
        )
        .execute("u1", "s1", "k1")
        .run
    )
    status = GradeRun(
        sessions=sessions,
        runs=runs,
        catalog=catalog,
        sandbox=provider,
        record=RecordCheckResult(runs=runs, mastery=In_Memory_MasteryRepository(), clock=clock),
        clock=clock,
        bundle_root=tmp_path / "bundles",
        provider_name="fake",
    ).execute(run.stable_id)
    assert status in set(RunStatus) - {RunStatus.RUNNING}
    final = sessions.get("s1")
    assert final is not None and final.status is SessionStatus.READY


def test_canary_marker_absent_after_full_flow_fake_provider(tmp_path: Path) -> None:
    marker = tmp_path / "host-marker"
    _fake_flow(tmp_path, marker)
    _assert_no_marker(marker)


IMAGE = "edu-it/lab-linux-base:dev"


def _docker_ready() -> bool:
    try:
        import docker

        client = docker.from_env(timeout=3)
        client.ping()
        client.images.get(IMAGE)
    except Exception:
        return False
    return True


@pytest.mark.docker
@pytest.mark.skipif(not _docker_ready(), reason="no Docker daemon or lab image")
def test_canary_marker_absent_after_session_flow_docker_provider(tmp_path: Path) -> None:
    from dataclasses import dataclass

    from edu_it_infrastructure.sandbox.docker_provider import DockerSandboxProvider

    @dataclass(frozen=True)
    class _L:
        image: str = IMAGE

    marker = tmp_path / "host-marker"
    src = tmp_path / "main.py"
    src.write_text(_canary_payload(marker), encoding="utf-8")
    provider: SandboxProvider = DockerSandboxProvider()
    handle: SandboxHandle = provider.provision(build_sandbox_spec(_L(), "canary-sess"))
    try:
        provider.copy_in(handle, src, "/workspace/main.py", as_user="learner")
        # Read it back as data; the api process itself never executes the payload.
        out = provider.exec(
            handle,
            ["/bin/cat", "/workspace/main.py"],
            as_user="learner",
            env={},
            cwd="/workspace",
            timeout_s=10,
        )
        assert out.stdout == src.read_bytes()
    finally:
        provider.destroy(handle)
    _assert_no_marker(marker)
