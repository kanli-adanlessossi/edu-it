"""Lab ports: structural conformance, error hierarchy, and import purity (task 1.4)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from edu_it_domain.ports import lab_catalog, lab_sessions, sandbox
from edu_it_domain.ports.lab_catalog import LabCatalog
from edu_it_domain.ports.lab_sessions import (
    ActiveSessionExists,
    GradingRunRepository,
    LabSessionRepository,
)
from edu_it_domain.ports.sandbox import (
    ExecResult,
    ProviderUnavailable,
    ProvisionError,
    ProvisionTimeout,
    SandboxError,
    SandboxGone,
    SandboxHandle,
    SandboxProvider,
    TerminalChannel,
    WarmPool,
)

SANDBOX_METHODS = [
    "provision",
    "exec",
    "attach_terminal",
    "copy_in",
    "copy_out",
    "destroy",
    "health",
    "list_sandboxes",
]
SESSION_METHODS = [
    "add_if_no_active",
    "get",
    "get_for_owner",
    "get_active_for_user",
    "transition",
    "try_accept_grading",
    "claim_provision_attempt",
    "touch_activity",
    "bump_terminal_epoch",
    "count_started_since",
    "list_for_user_page",
    "list_active",
    "list_ended_before",
    "delete_session_data",
]
RUN_METHODS = [
    "add",
    "get",
    "append_result",
    "finish",
    "list_running",
    "first_pass_exists",
    "add_task_completions",
]


def _impl(name: str, methods: list[str]) -> object:
    return type(name, (), {m: (lambda self, *a, **k: None) for m in methods})()


@pytest.mark.parametrize(
    ("protocol", "methods"),
    [
        (SandboxProvider, SANDBOX_METHODS),
        (TerminalChannel, ["read", "write", "resize", "close"]),
        (WarmPool, ["acquire"]),
        (LabSessionRepository, SESSION_METHODS),
        (GradingRunRepository, RUN_METHODS),
        (LabCatalog, ["get", "list_labs", "labs_covering"]),
    ],
)
def test_protocols_are_runtime_checkable(protocol: type, methods: list[str]) -> None:
    assert isinstance(_impl("Impl", methods), protocol)
    assert not isinstance(_impl("Partial", methods[1:]), protocol)


def test_error_hierarchy_and_partial_handle() -> None:
    handle = SandboxHandle("c1", "s1", "fake")
    err = ProvisionError("boom", partial_handle=handle)
    assert err.partial_handle is handle
    assert isinstance(ProvisionTimeout("slow"), ProvisionError)
    assert ProvisionTimeout("slow").partial_handle is None
    assert issubclass(ProviderUnavailable, SandboxError)
    assert issubclass(SandboxGone, SandboxError)
    assert issubclass(ActiveSessionExists, Exception)


def test_exec_result_is_frozen() -> None:
    result = ExecResult(exit_code=0, stdout=b"", stderr=b"", timed_out=False)
    with pytest.raises(AttributeError):
        result.exit_code = 1  # type: ignore[misc]


@pytest.mark.parametrize("module", [sandbox, lab_sessions, lab_catalog])
def test_ports_import_no_infrastructure(module: object) -> None:
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))  # type: ignore[attr-defined]
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    forbidden = {"docker", "fastapi", "starlette", "redis", "sqlalchemy", "subprocess", "socket"}
    assert not roots & forbidden
