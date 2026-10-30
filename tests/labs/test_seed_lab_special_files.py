"""Device nodes and sockets where the seed labs expect regular files or folders (task 17 follow-up).

Requirements 21.x and 22.x with Requirement 29.9. The Docker_Provider's sandboxes drop every
capability, so ``mknod`` cannot create a device node inside them: the ``device`` cases of the tamper
tests always skip there. This module covers the same ground with *real* special files in a throwaway
container of the Python Lab_Image started with exactly one extra capability (``CAP_MKNOD``) so the
fixture can create a character device, a block device and a UNIX socket. It then runs

* the four real lab 1 Check scripts (piped to ``sh -s``), which must exit 90 (``unsafe-path``)
  whenever ``/workspace/team``, ``docs`` or ``shared-notes.txt`` is a device node or a socket, and
* lab 2's real harness ``read_learner_files`` on a ``solution.py`` that is a device node or a
  socket, which must raise ``UnsafeLearnerFile`` without reading or running anything.

The throwaway container is not a sandbox: it only provides real file types to the real code. The
sandbox-level behaviour (exit 90 mapped to ``could_not_complete``) is covered by the tamper tests.
Tests skip without Docker or ``edu-it/lab-python:dev``.
"""

from __future__ import annotations

import shutil
import subprocess
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BUNDLES = REPO_ROOT / "content" / "linux-fundamentals" / "lab-bundles"
CHECKS_DIR = BUNDLES / "fix-the-shared-folder" / "sandbox" / "checks"
HARNESS = BUNDLES / "world-writable-entries" / "sandbox" / "harness" / "run_cases.py"
PFX = "lab-check.fix-the-shared-folder."
IMAGE = "edu-it/lab-python:dev"
UNSAFE_PATH = 90

#: kind -> shell command creating it at ``$1`` (run as root in the throwaway container).
MAKERS = {
    "char-device": 'mknod "$1" c 1 3',
    "block-device": 'mknod "$1" b 7 0',
    "socket": (
        "python3 -c 'import socket,sys; s=socket.socket(socket.AF_UNIX); s.bind(sys.argv[1])'"
        ' "$1"'
    ),
}
KINDS = list(MAKERS)

#: path replaced -> Checks whose verdict must be ``unsafe-path``.
TARGETS = {
    "/workspace/team": ("dirs-exist", "group-setgid", "no-world-writable", "planted-intact"),
    "/workspace/team/docs": ("dirs-exist",),
    "/workspace/team/scripts": ("dirs-exist",),
    "/workspace/team/shared-notes.txt": ("planted-intact",),
}

pytestmark = pytest.mark.docker


def _docker(*args: str, data: bytes | None = None) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["docker", *args], input=data, capture_output=True, timeout=60, check=False
    )


def _available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        return _docker("image", "inspect", IMAGE).returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


@pytest.fixture(scope="module")
def box() -> Iterator[str]:
    """A container like a sandbox (no network, read-only root, no caps) plus ``CAP_MKNOD``."""
    if not _available():
        pytest.skip(f"Docker or image {IMAGE} not available")
    name = f"edu-it-special-{uuid.uuid4().hex[:8]}"
    started = _docker(
        "run", "-d", "--name", name, "--init", "--network", "none",
        "--cap-drop", "ALL", "--cap-add", "MKNOD", "--security-opt", "no-new-privileges",
        "--user", "0:0", "--tmpfs", "/workspace:rw,size=8m", "--tmpfs", "/tmp:rw,size=8m",
        IMAGE, "sleep", "600",
    )  # fmt: skip
    if started.returncode != 0:
        pytest.skip(f"cannot start container: {started.stderr.decode(errors='replace')}")
    try:
        yield name
    finally:
        _docker("rm", "-f", name)


def _sh(
    box: str, script: str, *args: str, stdin: bytes | None = None
) -> subprocess.CompletedProcess[bytes]:
    flags = ["-i"] if stdin is not None else []
    return _docker("exec", *flags, box, "/bin/sh", "-c", script, "sh", *args, data=stdin)


def _reset_team(box: str) -> None:
    """A correct lab 1 end state: team/{docs,scripts} and a regular shared-notes.txt."""
    script = (
        "rm -rf /workspace/team && mkdir -p /workspace/team/docs /workspace/team/scripts"
        " && echo x > /workspace/team/shared-notes.txt"
    )
    assert _sh(box, script).returncode == 0


def _make(box: str, kind: str, path: str) -> None:
    res = _sh(box, f'rm -rf "$1" && {MAKERS[kind]}', path)
    assert res.returncode == 0, (kind, path, res.stderr)
    shown = _sh(box, '/bin/stat -c "%F" -- "$1"', path).stdout.decode().strip()
    assert shown in {"character special file", "block special file", "socket"}, shown


def _run_check(box: str, name: str) -> int:
    script = (CHECKS_DIR / f"{PFX}{name}.sh").read_bytes()
    return _docker("exec", "-i", box, "/bin/sh", "-s", data=script).returncode


def test_the_sandbox_itself_cannot_create_device_nodes() -> None:
    """Why the tamper tests skip their device case: sandboxes have no CAP_MKNOD (by design)."""
    if not _available():
        pytest.skip(f"Docker or image {IMAGE} not available")
    name = f"edu-it-nocap-{uuid.uuid4().hex[:8]}"
    out = _docker(
        "run", "--rm", "--name", name, "--cap-drop", "ALL", "--user", "0:0",
        "--security-opt", "no-new-privileges", IMAGE, "/bin/mknod", "/tmp/n", "c", "1", "3",
    )  # fmt: skip
    assert out.returncode != 0


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("target", list(TARGETS))
def test_lab1_checks_report_unsafe_path_for_device_nodes_and_sockets(
    box: str, kind: str, target: str
) -> None:
    _reset_team(box)
    # Baseline: the untampered end state passes every Check, so a 90 below is the tampering.
    assert {c: _run_check(box, c) for c in TARGETS["/workspace/team"]}["dirs-exist"] == 0
    _make(box, kind, target)
    for check in TARGETS[target]:
        assert _run_check(box, check) == UNSAFE_PATH, (kind, target, check)


@pytest.mark.parametrize("kind", KINDS)
def test_lab2_harness_rejects_device_nodes_and_sockets_as_learner_files(
    box: str, kind: str
) -> None:
    assert _sh(box, "mkdir -p /workspace").returncode == 0
    _make(box, kind, "/workspace/solution.py")
    # The harness source arrives on stdin (``/tmp`` is a tmpfs, which ``docker cp`` cannot reach).
    probe = (
        "import sys, types\n"
        "m = types.ModuleType('run_cases'); sys.modules['run_cases'] = m\n"
        "exec(compile(sys.stdin.read(), 'run_cases.py', 'exec'), m.__dict__)\n"
        "try:\n"
        "    m.read_learner_files('/workspace', ['solution.py'])\n"
        "except m.UnsafeLearnerFile:\n"
        "    print('unsafe')\n"
        "else:\n"
        "    print('accepted')\n"
    )
    res = _docker(
        "exec", "-i", box, "/usr/bin/python3", "-I", "-c", probe, data=HARNESS.read_bytes()
    )
    assert res.returncode == 0, res.stderr
    assert res.stdout.decode().strip() == "unsafe"
