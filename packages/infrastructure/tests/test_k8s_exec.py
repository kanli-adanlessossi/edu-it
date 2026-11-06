"""Unit tests for exec, copy and terminal of ``KubernetesSandboxProvider`` (task 5.3).

Runs on ``FakeK8sApi``; time is the ``Test_Clock`` advanced by an injected sleeper. The dedicated
exec and terminal tests of task 5.9 extend these essentials.
"""

from __future__ import annotations

import io
import logging
import tarfile
from datetime import timedelta
from pathlib import Path

import pytest
from edu_it_domain.labs.sandbox_spec import build_sandbox_spec
from edu_it_domain.ports.sandbox import (
    ExecResult,
    SandboxError,
    SandboxGone,
    SandboxHandle,
    TerminalChannel,
)
from edu_it_infrastructure.fakes.clock import Test_Clock
from edu_it_infrastructure.fakes.k8s_api import FakeK8sApi, FakeNode, Taint
from edu_it_infrastructure.sandbox.k8s_api import ExecOutput, K8sApiError
from edu_it_infrastructure.sandbox.k8s_exec import EXEC_MARKER_VAR
from edu_it_infrastructure.sandbox.k8s_pod_spec import K8sSettings, Toleration
from edu_it_infrastructure.sandbox.k8s_provider import KubernetesSandboxProvider

NS = "labs"
LAB_NODE = FakeNode(
    "lab-node", labels={"edu-it/pool": "labs"}, taints=(Taint("edu-it/lab", "true"),)
)
SECRET = "internal-host.local token=abc"


class _Lab:
    image = "registry.example/lab@sha256:abc"


def _settings() -> K8sSettings:
    return K8sSettings(
        namespace=NS,
        runtime_class_name="gvisor",
        runtime_kind="gvisor",
        node_selector={"edu-it/pool": "labs"},
        tolerations=(Toleration("edu-it/lab", "Equal", "true", "NoSchedule"),),
        node_loss_toleration_seconds=30,
        grader_gid=1001,
    )


def _setup() -> tuple[KubernetesSandboxProvider, FakeK8sApi, Test_Clock, SandboxHandle]:
    clock = Test_Clock()
    api = FakeK8sApi(clock, nodes=[LAB_NODE], runtime_classes=["gvisor"])

    def sleep(seconds: float) -> None:
        clock.advance(timedelta(seconds=seconds))

    provider = KubernetesSandboxProvider(api, _settings(), clock, sleep=sleep)
    handle = provider.provision(build_sandbox_spec(_Lab(), "s1"))
    api.exec_calls.clear()
    api.exec_stdin.clear()
    return provider, api, clock, handle


def _members(data: bytes | None) -> dict[str, tarfile.TarInfo]:
    assert data is not None
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as tf:
        return {m.name: m for m in tf.getmembers()}


def _tar_of(name: str, content: bytes) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        info = tarfile.TarInfo(name)
        info.size = len(content)
        tf.addfile(info, io.BytesIO(content))
    return buf.getvalue()


# Claims: 1.1
def test_provider_exposes_exec_copy_and_terminal_on_the_port() -> None:
    provider, _api, _clock, handle = _setup()
    result = provider.exec(handle, ["true"], as_user="learner", env={}, cwd="/")
    assert isinstance(result, ExecResult)


# Claims: 1.1, 5.1
@pytest.mark.parametrize(
    ("as_user", "container"),
    [("learner", "learner"), ("grader", "grader"), ("root", "owner")],
)
def test_exec_runs_in_the_container_of_as_user_with_env_and_cwd(
    as_user: str, container: str
) -> None:
    provider, api, _, handle = _setup()
    api.exec_handler = lambda pod, c, cmd: ExecOutput(3, b"out", b"err")
    result = provider.exec(handle, ["ls", "-l"], as_user=as_user, env={"A": "1"}, cwd="/workspace")
    assert result == ExecResult(3, b"out", b"err", False)
    (_ns, name, used, argv) = api.exec_calls[0]
    assert (name, used) == (handle.sandbox_id, container)
    assert "/workspace" in argv and "A=1" in argv
    assert argv[-2:] == ("ls", "-l")
    assert any(a.startswith(f"{EXEC_MARKER_VAR}=") for a in argv)


# Claims: 5.1
def test_unknown_as_user_raises_sandbox_error_without_calling_the_api() -> None:
    provider, api, _, handle = _setup()
    with pytest.raises(SandboxError):
        provider.exec(handle, ["id"], as_user="nobody", env={}, cwd="/")
    with pytest.raises(SandboxError):
        provider.copy_in(handle, Path("."), "/workspace", as_user="nobody")
    assert api.exec_calls == []


# Claims: 5.6
def test_timeout_kills_the_marked_processes_and_reports_timed_out() -> None:
    provider, api, clock, handle = _setup()
    api.exec_handler = lambda pod, c, cmd: ExecOutput(0, b"partial", b"")
    api.exec_duration = timedelta(seconds=30)
    before = clock.now()
    result = provider.exec(handle, ["sleep", "30"], as_user="learner", env={}, cwd="/", timeout_s=2)
    assert result == ExecResult(137, b"partial", b"", True)
    assert clock.now() - before >= timedelta(seconds=2)
    run, kill = api.exec_calls
    marker = next(a for a in run[3] if a.startswith(f"{EXEC_MARKER_VAR}=")).split("=", 1)[1]
    assert kill[2] == run[2] == "learner"
    assert kill[3][-1] == marker
    assert "kill" in kill[3][2]


# Claims: 5.6
def test_a_command_within_its_timeout_is_not_killed() -> None:
    provider, api, _, handle = _setup()
    api.exec_duration = timedelta(seconds=1)
    result = provider.exec(handle, ["true"], as_user="learner", env={}, cwd="/", timeout_s=5)
    assert result == ExecResult(0, b"", b"", False)
    assert len(api.exec_calls) == 1


# Claims: 5.6
def test_non_positive_timeout_is_rejected() -> None:
    provider, _api, _, handle = _setup()
    with pytest.raises(ValueError):
        provider.exec(handle, ["true"], as_user="learner", env={}, cwd="/", timeout_s=0)


# Claims: 1.12
def test_exec_on_a_missing_pod_is_gone_and_raw_text_is_not_raised(
    caplog: pytest.LogCaptureFixture,
) -> None:
    provider, api, _, handle = _setup()
    api.fail_next("exec", K8sApiError(500, "InternalError", SECRET))
    with caplog.at_level(logging.WARNING), pytest.raises(SandboxError) as info:
        provider.exec(handle, ["true"], as_user="learner", env={}, cwd="/")
    assert SECRET not in str(info.value)
    assert SECRET in caplog.text
    api.vanish_pod(NS, handle.sandbox_id)
    with pytest.raises(SandboxGone):
        provider.exec(handle, ["true"], as_user="learner", env={}, cwd="/")


# Claims: 5.1, 5.2
def test_copy_in_to_grader_goes_through_the_owner_and_never_writes_the_source(
    tmp_path: Path,
) -> None:
    provider, api, _, handle = _setup()
    bundle = tmp_path / "bundle"
    (bundle / "sub").mkdir(parents=True)
    (bundle / "check.sh").write_bytes(b"echo ok\n")
    (bundle / "sub" / "data.txt").write_bytes(b"x")
    before = {p: p.read_bytes() for p in bundle.rglob("*") if p.is_file()}

    provider.copy_in(handle, bundle, "/grader/bundle", as_user="grader")

    (_ns, _name, container, argv) = api.exec_calls[0]
    assert container == "owner"
    assert "/bin/tar" in argv and "-x" in argv and "--no-same-owner" in argv
    assert argv[argv.index("-C") + 1] == "/grader/bundle"
    members = _members(api.exec_stdin[0])
    assert set(members) == {"check.sh", "sub", "sub/data.txt"}
    assert all(m.mode & 0o040 for m in members.values())  # group readable
    assert not any(m.mode & 0o027 for m in members.values())  # no group write, no other access
    assert before == {p: p.read_bytes() for p in bundle.rglob("*") if p.is_file()}


# Claims: 5.1
def test_copy_in_of_a_file_outside_grader_uses_the_as_user_container(tmp_path: Path) -> None:
    provider, api, _, handle = _setup()
    src = tmp_path / "main.py"
    src.write_bytes(b"print(1)\n")
    provider.copy_in(handle, src, "/workspace/main.py")
    (_ns, _name, container, argv) = api.exec_calls[0]
    assert container == "learner"
    assert argv[argv.index("-C") + 1] == "/workspace"
    assert set(_members(api.exec_stdin[0])) == {"main.py"}


# Claims: 5.1, 5.2
def test_copy_in_failure_is_a_sandbox_error_without_tar_text(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    provider, api, _, handle = _setup()
    src = tmp_path / "f"
    src.write_bytes(b"1")
    api.exec_handler = lambda pod, c, cmd: ExecOutput(2, b"", SECRET.encode())
    with caplog.at_level(logging.WARNING), pytest.raises(SandboxError) as info:
        provider.copy_in(handle, src, "/workspace/f")
    assert SECRET not in str(info.value)
    assert SECRET in caplog.text
    with pytest.raises(FileNotFoundError):
        provider.copy_in(handle, tmp_path / "missing", "/workspace/f")


# Claims: 5.2
def test_copy_out_unpacks_a_file_and_reads_grader_as_owner(tmp_path: Path) -> None:
    provider, api, _, handle = _setup()
    api.exec_handler = lambda pod, c, cmd: ExecOutput(0, _tar_of("result.json", b"{}"))
    dest = tmp_path / "out" / "result.json"
    provider.copy_out(handle, "/workspace/result.json", dest)
    assert dest.read_bytes() == b"{}"
    assert api.exec_calls[0][2] == "learner"
    provider.copy_out(handle, "/grader/result.json", dest)
    assert api.exec_calls[1][2] == "owner"


# Claims: 5.2
def test_copy_out_of_a_missing_path_is_file_not_found(tmp_path: Path) -> None:
    provider, api, _, handle = _setup()
    api.exec_handler = lambda pod, c, cmd: ExecOutput(
        2, b"", b"tar: nope: Cannot stat: No such file or directory"
    )
    with pytest.raises(FileNotFoundError):
        provider.copy_out(handle, "/workspace/nope", tmp_path / "x")


# Claims: 1.10
def test_terminal_reads_writes_resizes_and_closes_idempotently() -> None:
    provider, api, _, handle = _setup()
    channel = provider.attach_terminal(handle)
    assert isinstance(channel, TerminalChannel)
    stream = api.terminals[0]
    assert stream.container == "learner"
    assert stream.command[-2:] == ("/bin/bash", "-l")
    channel.write(b"ls\n")
    assert channel.read(2) == b"ls"
    assert channel.read(10) == b"\n"
    assert channel.read(10, timeout_s=0.5) == b""
    channel.resize(24, 80)
    assert stream.written == [b"ls\n"] and stream.sizes == [(24, 80)]

    api.exec_calls.clear()
    channel.close()
    channel.close()
    assert stream.closed
    assert len(api.exec_calls) == 1  # the shell's processes are killed once
    for call in (
        lambda: channel.read(1),
        lambda: channel.write(b"x"),
        lambda: channel.resize(1, 1),
    ):
        with pytest.raises(SandboxGone):
            call()


# Claims: 1.11
def test_attach_terminal_to_a_missing_pod_is_gone() -> None:
    provider, api, _, handle = _setup()
    api.vanish_pod(NS, handle.sandbox_id)
    with pytest.raises(SandboxGone):
        provider.attach_terminal(handle)


# Claims: 1.12
def test_terminal_operations_raise_gone_after_pod_loss() -> None:
    provider, api, _, handle = _setup()
    channel = provider.attach_terminal(handle)
    api.vanish_pod(NS, handle.sandbox_id)
    for call in (
        lambda: channel.read(1),
        lambda: channel.write(b"x"),
        lambda: channel.resize(1, 1),
    ):
        with pytest.raises(SandboxGone):
            call()
    channel.close()  # idempotent and silent after pod loss


# Claims: 1.12
def test_terminal_of_a_terminating_pod_is_gone() -> None:
    provider, api, _, handle = _setup()
    channel = provider.attach_terminal(handle)
    api.delete_pod(NS, handle.sandbox_id)
    with pytest.raises(SandboxGone):
        channel.read(1)
