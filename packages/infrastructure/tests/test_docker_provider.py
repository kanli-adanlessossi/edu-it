"""Tests for the dev-only ``DockerSandboxProvider`` (hands-on-labs task 2.5).

Daemon-free tests run everywhere. Tests marked ``docker`` need a reachable daemon and the
locally built ``edu-it/lab-linux-base:dev`` image, and are skipped otherwise.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import pytest
from edu_it_domain.labs.sandbox_spec import build_sandbox_spec
from edu_it_domain.ports.sandbox import (
    ProviderUnavailable,
    SandboxGone,
    SandboxHandle,
)
from edu_it_infrastructure.sandbox.docker_provider import (
    RUNC_WARNING,
    DockerSandboxProvider,
    _parse_ps,
    _write_tar,
)

IMAGE = "edu-it/lab-linux-base:dev"
LOCAL = {"as_user": "learner", "env": {}, "cwd": "/workspace"}


@dataclass(frozen=True)
class _Lab:
    image: str = IMAGE


# -- daemon-free ---------------------------------------------------------------


def test_startup_logs_dev_only_runc_warning(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        DockerSandboxProvider(base_url="tcp://127.0.0.1:1")
    assert any(RUNC_WARNING in r.getMessage() and "runc" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("base_url", ["tcp://127.0.0.1:1", "tcp://10.255.255.1:2375"])
def test_unreachable_daemon_is_provider_unavailable_within_bound(base_url: str) -> None:
    bound = 1.0
    provider = DockerSandboxProvider(base_url=base_url, operation_timeout=timedelta(seconds=bound))
    handle = SandboxHandle("abc", "sess", "docker")
    spec = build_sandbox_spec(_Lab(), "sess")
    calls: list[Callable[[], object]] = [
        lambda: provider.provision(spec),
        lambda: provider.health(handle),
        lambda: provider.list_sandboxes(),
        lambda: provider.destroy(handle),
        lambda: provider.exec(handle, ["/bin/true"], timeout_s=1, **LOCAL),  # type: ignore[arg-type]
    ]
    for call in calls:
        start = time.monotonic()
        with pytest.raises(ProviderUnavailable):
            call()
        assert time.monotonic() - start < bound + 2.0
    # Nothing was cached: the client was never created.
    assert provider._client is None


def test_foreign_handle_is_gone_and_destroy_ignores_it() -> None:
    provider = DockerSandboxProvider(base_url="tcp://127.0.0.1:1")
    other = SandboxHandle("fake-1", "sess", "fake")
    provider.destroy(other)
    with pytest.raises(SandboxGone):
        provider.attach_terminal(other)


def test_parse_ps_reads_user_pid_ppid_args() -> None:
    rows = _parse_ps(
        "learner        1       0 /sbin/docker-init -- /bin/sleep infinity\n"
        "learner        7       1 /bin/sleep infinity\n"
        "garbage\n"
    )
    assert [(r.pid, r.ppid, r.args) for r in rows] == [
        (1, 0, "/sbin/docker-init -- /bin/sleep infinity"),
        (7, 1, "/bin/sleep infinity"),
    ]


def test_tar_writer_refuses_symlinks(tmp_path: Path) -> None:
    (tmp_path / "real.txt").write_text("x")
    try:
        (tmp_path / "link").symlink_to(tmp_path / "real.txt")
    except OSError:
        pytest.skip("symlinks not permitted on this host")
    from edu_it_domain.ports.sandbox import SandboxError

    with pytest.raises(SandboxError):
        _write_tar(tmp_path, None, open(tmp_path.parent / "out.tar", "wb"))  # noqa: SIM115


# -- docker daemon -------------------------------------------------------------


def _daemon_ready() -> bool:
    try:
        import docker

        client = docker.from_env(timeout=3)
        client.ping()
        client.images.get(IMAGE)
    except Exception:
        return False
    return True


requires_docker = pytest.mark.skipif(not _daemon_ready(), reason="no Docker daemon or lab image")
pytestmark_docker = [pytest.mark.docker, requires_docker]


@pytest.fixture
def provider() -> DockerSandboxProvider:
    return DockerSandboxProvider()


@pytest.fixture
def sandbox(provider: DockerSandboxProvider) -> Iterator[SandboxHandle]:
    handle = provider.provision(build_sandbox_spec(_Lab(), "pytest-sess"))
    try:
        yield handle
    finally:
        provider.destroy(handle)


@pytest.mark.docker
@requires_docker
def test_lifecycle_list_health_and_idempotent_destroy(provider: DockerSandboxProvider) -> None:
    handle = provider.provision(build_sandbox_spec(_Lab(), "pytest-life"))
    assert handle.provider == "docker"
    assert handle in provider.list_sandboxes()
    assert provider.health(handle).healthy
    provider.destroy(handle)
    provider.destroy(handle)
    assert handle not in provider.list_sandboxes()
    assert not provider.health(handle).healthy
    with pytest.raises(SandboxGone):
        provider.exec(handle, ["/bin/true"], **LOCAL)  # type: ignore[arg-type]


@pytest.mark.docker
@requires_docker
def test_exec_output_exit_code_env_and_cwd(
    provider: DockerSandboxProvider, sandbox: SandboxHandle
) -> None:
    res = provider.exec(
        sandbox,
        ["/bin/bash", "-c", "echo out; echo err >&2; echo $FOO; pwd; id -u; exit 7"],
        as_user="learner",
        env={"FOO": "bar"},
        cwd="/workspace",
    )
    assert res.exit_code == 7
    assert res.stdout.decode().split() == ["out", "bar", "/workspace", "1000"]
    assert res.stderr.decode().strip() == "err"
    assert not res.timed_out
    result = provider.exec(sandbox, ["/usr/bin/id", "-u"], as_user="grader", env={}, cwd="/")
    assert result.stdout.strip() == b"1001"


@pytest.mark.docker
@requires_docker
def test_timeout_kills_command_and_background_and_sandbox_survives(
    provider: DockerSandboxProvider, sandbox: SandboxHandle
) -> None:
    # A command that forks a backgrounded child, an escapee in its own session, and
    # then blocks. After the timeout nothing the learner started may survive, yet the
    # sandbox (keep-alive process under docker-init) must stay usable.
    script = "sleep 1000 & setsid /bin/sleep 1001 & sleep 1002"
    start = time.monotonic()
    res = provider.exec(
        sandbox,
        ["/bin/bash", "-c", script],
        timeout_s=1.0,
        **LOCAL,  # type: ignore[arg-type]
    )
    assert res.timed_out and res.exit_code == 137
    assert time.monotonic() - start < 10

    assert provider.health(sandbox).healthy
    ps = provider.exec(sandbox, ["/bin/ps", "-e", "-o", "pid=", "-o", "args="], **LOCAL)  # type: ignore[arg-type]
    args = ps.stdout.decode()
    assert "sleep 100" not in args.replace("sleep infinity", "")
    assert "sleep infinity" in args
    # And the keep-alive process was thawed (not left in state T).
    stat = provider.exec(sandbox, ["/bin/ps", "-e", "-o", "stat=", "-o", "args="], **LOCAL)  # type: ignore[arg-type]
    line = next(
        x
        for x in stat.stdout.decode().splitlines()
        if "sleep infinity" in x and "docker-init" not in x
    )
    assert not line.split()[0].startswith("T")


@pytest.mark.docker
@requires_docker
def test_copy_round_trip_workspace_and_root_grader(
    provider: DockerSandboxProvider, sandbox: SandboxHandle, tmp_path: Path
) -> None:
    src = tmp_path / "src"
    (src / "sub").mkdir(parents=True)
    (src / "a.txt").write_bytes(b"alpha")
    (src / "sub" / "b.bin").write_bytes(bytes(range(256)))

    provider.copy_in(sandbox, src, "/workspace")
    res = provider.exec(sandbox, ["/bin/cat", "/workspace/sub/b.bin"], **LOCAL)  # type: ignore[arg-type]
    assert res.stdout == bytes(range(256))

    out_file = tmp_path / "out" / "a.txt"
    provider.copy_out(sandbox, "/workspace/a.txt", out_file)
    assert out_file.read_bytes() == b"alpha"
    out_dir = tmp_path / "outdir"
    provider.copy_out(sandbox, "/workspace/sub", out_dir)
    assert (out_dir / "b.bin").read_bytes() == bytes(range(256))

    # /grader is a root-owned tmpfs: copy in and out as root (README form).
    provider.copy_in(sandbox, src / "a.txt", "/grader/check.txt", as_user="root")
    owner = provider.exec(
        sandbox, ["/bin/ls", "-l", "/grader/check.txt"], as_user="root", env={}, cwd="/"
    )
    assert owner.stdout.split()[2] == b"root"
    back = tmp_path / "check.txt"
    provider.copy_out(sandbox, "/grader/check.txt", back)
    assert back.read_bytes() == b"alpha"

    with pytest.raises(FileNotFoundError):
        provider.copy_out(sandbox, "/workspace/missing", tmp_path / "nope")


@pytest.mark.docker
@requires_docker
def test_terminal_echo_and_close(provider: DockerSandboxProvider, sandbox: SandboxHandle) -> None:
    term = provider.attach_terminal(sandbox)
    term.resize(30, 100)
    term.write(b"echo hello-term\n")
    seen = b""
    deadline = time.monotonic() + 10
    while (
        b"hello-term" not in seen.replace(b"echo hello-term", b"") and time.monotonic() < deadline
    ):
        seen += term.read(4096, timeout_s=1.0)
    assert b"hello-term" in seen
    while term.read(4096, timeout_s=0.3):
        pass  # drain the prompt; an idle read then returns b"" within its timeout
    term.close()
    term.close()
    with pytest.raises(SandboxGone):
        term.read(10, timeout_s=0.1)
