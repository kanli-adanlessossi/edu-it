"""Code-exercise tests for learner file saves (task 12.3).

Reuses the in-process ASGI environment of ``test_labs_routes`` (real router, ``SaveFiles`` and
``RequestGrading`` use cases over ``Fake_SandboxProvider`` and in-memory repositories).

**Validates: Requirements 14.2, 14.6, 14.7, 14.9**
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest
from edu_it_domain import config
from edu_it_domain.labs.models import SessionStatus
from edu_it_domain.labs.sandbox_spec import WORKSPACE_PATH
from edu_it_domain.ports.sandbox import SandboxGone, SandboxHandle

_SPEC = importlib.util.spec_from_file_location(
    "_labs_routes_env", Path(__file__).with_name("test_labs_routes.py")
)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = sys.modules.get(_SPEC.name) or importlib.util.module_from_spec(_SPEC)
if _SPEC.name not in sys.modules:
    sys.modules[_SPEC.name] = _MODULE
    _SPEC.loader.exec_module(_MODULE)
_Env: Any = _MODULE._Env

_MARKER = "UNIQUE-LEARNER-MARKER-7f3a"
_MAIN = f"{WORKSPACE_PATH}/main.py"


def _ready(env: Any) -> tuple[str, SandboxHandle]:
    sid = env.start()["stable_id"]
    env.make_ready(sid)
    ref = env.sessions.get(sid).sandbox_ref
    return sid, SandboxHandle(sandbox_id=ref, session_stable_id=sid, provider="fake")


def _put(env: Any, sid: str, files: dict[str, str]) -> tuple[int, Any]:
    return env.call("PUT", f"/lab-sessions/{sid}/files", body={"files": files})


def test_save_copies_learner_file_into_workspace() -> None:
    env = _Env()
    sid, handle = _ready(env)
    status, data = _put(env, sid, {"main.py": "print('hi')"})
    assert (status, data) == (200, {"saved": 1})
    assert env.sandbox.sandbox(handle).files[_MAIN] == b"print('hi')"
    assert env.sandbox.calls.count("copy_in") == 1


def test_grading_request_keeps_saved_files_in_workspace_and_copies_nothing() -> None:
    env = _Env()
    sid, handle = _ready(env)
    _put(env, sid, {"main.py": "v1"})
    copies = env.sandbox.calls.count("copy_in")
    status, _ = env.call("POST", f"/lab-sessions/{sid}/grading-runs", key="g1")
    assert status == 202
    assert env.sessions.get(sid).status is SessionStatus.GRADING
    # Grading reads the workspace in place: the saved bytes are intact and nothing is re-copied.
    assert env.sandbox.sandbox(handle).files[_MAIN] == b"v1"
    assert env.sandbox.calls.count("copy_in") == copies
    # While grading, further saves are refused and the workspace is unchanged.
    status, _ = _put(env, sid, {"main.py": "v2"})
    assert status == 409
    assert env.sandbox.sandbox(handle).files[_MAIN] == b"v1"


@pytest.mark.parametrize(
    "files",
    [
        {"main.py": "a" * (config.LAB_FILE_MAX_BYTES + 1)},
        {"evil.sh": "x"},
        {"../main.py": "x"},
    ],
)
def test_over_cap_or_unsafe_save_returns_problem_and_copies_nothing(files: dict[str, str]) -> None:
    env = _Env()
    sid, handle = _ready(env)
    status, problem = _put(env, sid, files)
    assert status == 422
    assert problem["status"] == 422
    assert "copy_in" not in env.sandbox.calls
    assert _MAIN not in env.sandbox.sandbox(handle).files
    assert env.sessions.get(sid).last_activity_at is None


def test_file_at_exact_cap_is_saved() -> None:
    env = _Env()
    sid, handle = _ready(env)
    status, _ = _put(env, sid, {"main.py": "a" * config.LAB_FILE_MAX_BYTES})
    assert status == 200
    assert len(env.sandbox.sandbox(handle).files[_MAIN]) == config.LAB_FILE_MAX_BYTES


def test_learner_files_are_not_persisted_after_session_is_destroyed() -> None:
    env = _Env()
    sid, handle = _ready(env)
    assert _put(env, sid, {"main.py": _MARKER})[0] == 200
    env.call("POST", f"/lab-sessions/{sid}/grading-runs", key="g1")
    env.sandbox.destroy(handle)

    with pytest.raises(SandboxGone):
        env.sandbox.copy_out(handle, _MAIN, Path("unused.out"))
    assert env.sandbox.health(handle).healthy is False

    stored = repr(
        (env.sessions._sessions, env.runs.__dict__, env.queue._pending, env.session_store.__dict__)
    )
    assert _MARKER not in stored
    _, view = env.call("GET", f"/lab-sessions/{sid}")
    assert _MARKER not in repr(view)
