"""Property test for unsafe learner files.

Feature: hands-on-labs, Property 36: Unsafe learner files are rejected and never executed

**Validates: Requirements 14.10, 14.11, 29.9**

Drives the real in-sandbox harness (``run_cases.run_check`` and ``read_learner_files``) over a
temporary grader directory and workspace. If any declared learner file is a symlink (to a file, to
a directory, or to a missing target), a FIFO, a device node, a socket, a directory, missing, or
larger than the cap, the report status is ``unsafe-learner-file`` and the injected runner is never
invoked. If every file is a regular file within the cap, the copy under ``<grader>/run/learner`` is
byte-identical and the path handed to the runner is that copy, never the workspace path.

The harness needs ``O_NOFOLLOW``, FIFOs and symlinks, so this runs on Linux only (the CI runner, or
inside the Python Lab_Image locally with the ``docker`` marker); it is skipped elsewhere. Device
nodes need privilege to create and are only generated when ``mknod`` is permitted.
"""

from __future__ import annotations

import importlib.util
import json
import os
import socket
import stat
import sys
import tempfile
from pathlib import Path
from types import ModuleType

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

pytestmark = [
    pytest.mark.skipif(
        not sys.platform.startswith("linux"), reason="needs O_NOFOLLOW, FIFOs and symlinks (Linux)"
    ),
]

HARNESS = (
    Path(__file__).resolve().parents[2]
    / "content/linux-fundamentals/lab-bundles/world-writable-entries/sandbox/harness/run_cases.py"
)
CHECK_ID = "chk1"


def _load_harness() -> ModuleType:
    spec = importlib.util.spec_from_file_location("lab_run_cases", HARNESS)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["lab_run_cases"] = module
    spec.loader.exec_module(module)
    return module


def _can_mknod() -> bool:
    if not sys.platform.startswith("linux"):
        return False
    with tempfile.TemporaryDirectory() as tmp:
        try:
            os.mknod(os.path.join(tmp, "dev"), stat.S_IFCHR | 0o600, os.makedev(1, 3))
        except OSError:
            return False
    return True


rc = _load_harness() if sys.platform.startswith("linux") else None

UNSAFE_KINDS = [
    "symlink_file",
    "symlink_dir",
    "symlink_missing",
    "fifo",
    "socket",
    "directory",
    "missing",
    "oversized",
]
if _can_mknod():
    UNSAFE_KINDS.append("device")

_names = st.sampled_from(["solution.py", "helper.py", "notes.txt", "data.bin", "a", "b.cfg"])
_regular_bytes = st.one_of(
    st.binary(max_size=256),
    st.just(b""),
    st.just(b"x" * (64 * 1024)),  # exactly at the cap
)


def _materialise(workspace: Path, outside: Path, name: str, kind: str, data: bytes) -> None:
    path = workspace / name
    if kind == "regular":
        path.write_bytes(data)
    elif kind == "oversized":
        path.write_bytes(b"y" * (64 * 1024 + 1))
    elif kind == "symlink_file":
        target = outside / "target_file"
        target.write_bytes(data)
        path.symlink_to(target)
    elif kind == "symlink_dir":
        target = outside / "target_dir"
        target.mkdir(exist_ok=True)
        path.symlink_to(target, target_is_directory=True)
    elif kind == "symlink_missing":
        path.symlink_to(outside / "does_not_exist")
    elif kind == "fifo":
        os.mkfifo(path)
    elif kind == "socket":
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.bind(str(path))
        finally:
            sock.close()  # the socket file stays behind
    elif kind == "directory":
        path.mkdir()
    elif kind == "device":
        os.mknod(path, stat.S_IFCHR | 0o600, os.makedev(1, 3))
    elif kind == "missing":
        pass
    else:  # pragma: no cover
        raise AssertionError(kind)


@st.composite
def _scenarios(draw: st.DrawFn) -> tuple[list[str], dict[str, str], dict[str, bytes], int]:
    names = draw(st.lists(_names, min_size=1, max_size=4, unique=True))
    kinds = {n: draw(st.sampled_from(["regular", *UNSAFE_KINDS])) for n in names}
    # Bias towards at least some all-regular scenarios.
    if draw(st.booleans()):
        kinds = dict.fromkeys(names, "regular")
    data = {n: draw(_regular_bytes) for n in names}
    n_cases = draw(st.integers(min_value=1, max_value=3))
    return names, kinds, data, n_cases


@settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)
@given(scenario=_scenarios())
def test_unsafe_learner_files_rejected_and_never_executed(
    scenario: tuple[list[str], dict[str, str], dict[str, bytes], int],
) -> None:
    """Feature: hands-on-labs, Property 36: Unsafe learner files are rejected and never executed."""
    assert rc is not None
    names, kinds, data, n_cases = scenario
    with tempfile.TemporaryDirectory(prefix="p36") as tmp:
        root = Path(tmp)
        grader = root / "grader"
        workspace = root / "workspace"
        outside = root / "outside"
        (grader / "cases").mkdir(parents=True)
        workspace.mkdir()
        outside.mkdir()
        entry = names[0]
        (grader / "cases" / f"{CHECK_ID}.json").write_text(
            json.dumps(
                {
                    "learner_files": names,
                    "entrypoint": entry,
                    "cases": [{"case_id": f"c{i}"} for i in range(n_cases)],
                }
            )
        )
        for name in names:
            _materialise(workspace, outside, name, kinds[name], data[name])

        calls: list[str] = []

        def runner(path: str, stdin: bytes, timeout_s: float, work_dir: str) -> object:
            calls.append(path)
            return rc.CaseOutcome(0, False, b"", b"")

        report = json.loads(
            rc.run_check(
                CHECK_ID, grader_dir=str(grader), workspace=str(workspace), runner=runner
            )
        )

        if any(kinds[n] != "regular" for n in names):
            assert report["status"] == "unsafe-learner-file"
            assert report["cases"] == []
            assert calls == []
            assert not (grader / "run" / "learner").exists()
        else:
            assert report["status"] == "ok"
            copy_dir = grader / "run" / "learner"
            assert calls == [str(copy_dir / entry)] * n_cases
            for name in names:
                assert (copy_dir / name).read_bytes() == data[name]
                assert not (copy_dir / name).is_symlink()
            assert str(workspace) not in calls[0]
