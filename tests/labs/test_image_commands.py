"""Command-compatibility checks run inside each built Lab_Image.

Asserts that ``ps``, ``kill``, ``timeout`` and ``tar`` exist at the paths recorded in
``lab-images/README.md`` and accept every argv form the harness, setup scripts, Checks and
provider use (Requirements 4.15, 4.16). Skipped when Docker or the images are unavailable.
"""

from __future__ import annotations

import io
import shutil
import subprocess
import tarfile
import uuid
from collections.abc import Iterator

import pytest

pytestmark = pytest.mark.docker

IMAGES = ["edu-it/lab-linux-base:dev", "edu-it/lab-python:dev"]


def _docker(*args: str, input: bytes | None = None, timeout: int = 60) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(["docker", *args], input=input, capture_output=True, timeout=timeout, check=False)


def _available(image: str) -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        return _docker("image", "inspect", image, timeout=20).returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


@pytest.fixture(params=IMAGES, scope="module")
def container(request: pytest.FixtureRequest) -> Iterator[str]:
    image: str = request.param
    if not _available(image):
        pytest.skip(f"Docker or image {image} not available")
    name = f"edu-it-cmdtest-{uuid.uuid4().hex[:8]}"
    # A root-owned long-lived process, so STOP/KILL -1 as learner/grader cannot end the container.
    started = _docker(
        "run", "-d", "--name", name, "--init", "--network", "none", "--read-only",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--user", "1000:1000",
        "--tmpfs", "/tmp:rw,size=16m", image, "sleep", "600",
    )  # fmt: skip
    if started.returncode != 0:
        pytest.skip(f"cannot start sandbox: {started.stderr.decode(errors='replace')}")
    try:
        yield name
    finally:
        _docker("rm", "-f", name)


def _exec(
    name: str, user: str, argv: list[str], input: bytes | None = None
) -> subprocess.CompletedProcess[bytes]:
    flags = ["-i"] if input is not None else []
    return _docker("exec", *flags, "-u", user, name, *argv, input=input)


def _tar_bytes(member: str, data: bytes) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        info = tarfile.TarInfo(member)
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


@pytest.mark.parametrize(
    ("path", "needle"),
    [
        ("/bin/ps", b"procps"),
        ("/usr/bin/timeout", b"GNU coreutils"),
        ("/bin/tar", b"GNU tar"),
    ],
)
def test_binary_exists_and_implementation(container: str, path: str, needle: bytes) -> None:
    r = _exec(container, "learner", [path, "--version"])
    assert r.returncode == 0, r.stderr
    assert needle in r.stdout + r.stderr


def test_kill_is_busybox_applet_at_recorded_path(container: str) -> None:
    assert _exec(container, "learner", ["/bin/sh", "-c", "test -x /bin/kill"]).returncode == 0
    assert _exec(container, "learner", ["/bin/sh", "-c", "test ! -e /usr/bin/kill"]).returncode == 0


@pytest.mark.parametrize("user", ["learner", "grader"])
def test_ps_forms(container: str, user: str) -> None:
    r = _exec(container, user, ["/bin/ps", "-eo", "user,stat,pid,comm"])
    assert r.returncode == 0, r.stderr
    assert r.stdout.splitlines()[0].split() == [b"USER", b"STAT", b"PID", b"COMMAND"]
    r = _exec(container, user, ["/bin/ps", "-e", "-o", "user=", "-o", "stat=", "-o", "pid=", "-o", "comm="])
    assert r.returncode == 0, r.stderr
    first = r.stdout.splitlines()[0].split()
    assert len(first) == 4
    assert first[2].isdigit()


def test_ps_stat_reports_stopped_state(container: str) -> None:
    # Grader's STOP/CONT -1 only touches grader processes, so the container stays running.
    script = "sleep 30 & echo $!; /bin/kill -s STOP $!; /bin/ps -o stat= -p $!; /bin/kill -s KILL $!"
    r = _exec(container, "grader", ["/bin/bash", "-c", script])
    assert r.returncode == 0, r.stderr
    assert r.stdout.split()[1].startswith(b"T")


@pytest.mark.parametrize("sig", ["STOP", "CONT"])
def test_kill_freeze_thaw_as_grader(container: str, sig: str) -> None:
    r = _exec(container, "grader", ["/bin/kill", "-s", sig, "-1"])
    assert r.returncode == 0, r.stderr


def test_kill_s_kill_minus_one_as_grader(container: str) -> None:
    # Grader cannot signal learner processes, so the sandbox's main process survives.
    r = _exec(container, "grader", ["/bin/kill", "-s", "KILL", "-1"])
    assert r.returncode == 0, r.stderr
    assert _docker("inspect", "-f", "{{.State.Running}}", container).stdout.strip() == b"true"


def test_timeout_forms(container: str) -> None:
    t = "/usr/bin/timeout"
    r = _exec(container, "learner", [t, "-s", "KILL", "1", "/bin/sleep", "10"])
    assert r.returncode == 137
    r = _exec(container, "learner", [t, "-s", "KILL", "1.5", "/bin/sleep", "10"])
    assert r.returncode == 137
    r = _exec(container, "learner", [t, "-s", "9", "1", "/bin/sleep", "10"])
    assert r.returncode == 137
    r = _exec(container, "learner", [t, "-s", "KILL", "5", "/bin/sh", "-c", "exit 7"])
    assert r.returncode == 7
    r = _exec(container, "learner", [t, "-s", "KILL", "1", "/bin/sh", "-c", "sleep 10 & wait"])
    assert r.returncode == 137


def test_tar_stream_out_and_in_as_learner(container: str) -> None:
    prep = "mkdir -p /tmp/src /tmp/dst && echo hello > /tmp/src/f.txt"
    assert _exec(container, "learner", ["/bin/sh", "-c", prep]).returncode == 0
    out = _exec(container, "learner", ["/bin/tar", "-c", "-C", "/tmp/src", "-f", "-", "f.txt"])
    assert out.returncode == 0, out.stderr
    for argv in (
        ["/bin/tar", "-x", "-C", "/tmp/dst", "-f", "-"],
        ["/bin/tar", "-x", "-C", "/tmp/dst"],
    ):
        r = _exec(container, "learner", argv, input=out.stdout)
        assert r.returncode == 0, r.stderr
    cat = _exec(container, "learner", ["/bin/cat", "/tmp/dst/f.txt"])
    assert cat.stdout == b"hello\n"


def test_tar_stream_in_as_root_requires_no_same_owner(container: str) -> None:
    payload = _tar_bytes("a.txt", b"x")
    ok = _exec(
        container, "root", ["/bin/tar", "-x", "--no-same-owner", "-C", "/tmp", "-f", "-"], input=payload
    )
    assert ok.returncode == 0, ok.stderr


@pytest.mark.parametrize(
    "flags",
    [
        ["--numeric-owner"],
        ["--one-file-system"],
        ["--no-recursion"],
        ["--hard-dereference"],
        ["--owner=0", "--group=0"],
        ["--mode=a+rX"],
    ],
)
def test_tar_create_flags(container: str, flags: list[str]) -> None:
    _exec(container, "learner", ["/bin/sh", "-c", "mkdir -p /tmp/src && echo hi > /tmp/src/f.txt"])
    r = _exec(container, "learner", ["/bin/tar", "-c", *flags, "-C", "/tmp/src", "-f", "-", "f.txt"])
    assert r.returncode == 0, r.stderr


@pytest.mark.parametrize("flags", [["--no-same-owner"], ["--no-same-permissions"], ["-o"]])
def test_tar_extract_flags(container: str, flags: list[str]) -> None:
    payload = _tar_bytes("b.txt", b"y")
    r = _exec(container, "learner", ["/bin/tar", "-x", *flags, "-C", "/tmp", "-f", "-"], input=payload)
    assert r.returncode == 0, r.stderr
