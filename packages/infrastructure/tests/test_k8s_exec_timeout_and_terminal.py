"""Deeper unit tests for exec timeouts and the terminal channel (task 5.9).

Complements ``test_k8s_exec.py`` (task 5.3). Runs on ``FakeK8sApi``; time is the ``Test_Clock``.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

import pytest
from edu_it_domain.labs.sandbox_spec import build_sandbox_spec
from edu_it_domain.ports.sandbox import (
    ExecResult,
    ProviderUnavailable,
    SandboxError,
    SandboxGone,
    SandboxHandle,
    TerminalChannel,
)
from edu_it_infrastructure.fakes.clock import Test_Clock
from edu_it_infrastructure.fakes.k8s_api import FakeK8sApi, FakeNode, Taint
from edu_it_infrastructure.sandbox.k8s_api import ExecOutput, K8sApiError, K8sStreamClosed
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
        grader_gid=10001,
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


def _marker(argv: tuple[str, ...]) -> str:
    return next(a for a in argv if a.startswith(f"{EXEC_MARKER_VAR}=")).split("=", 1)[1]


# --------------------------------------------------------------------------- timeout kill


# Claims: 5.6
@pytest.mark.parametrize("as_user", ["learner", "grader", "root"])
def test_timeout_kill_runs_in_the_same_container_as_the_command(as_user: str) -> None:
    provider, api, _, handle = _setup()
    api.exec_duration = timedelta(seconds=30)
    provider.exec(handle, ["sleep", "30"], as_user=as_user, env={}, cwd="/", timeout_s=1)
    run, kill = api.exec_calls
    assert run[2] == kill[2]
    assert run[1] == kill[1] == handle.sandbox_id


# Claims: 5.6
def test_timeout_kill_targets_the_marker_of_that_command_with_sigkill() -> None:
    provider, api, _, handle = _setup()
    api.exec_duration = timedelta(seconds=30)
    provider.exec(handle, ["sleep", "30"], as_user="learner", env={}, cwd="/", timeout_s=1)
    run, kill = api.exec_calls
    assert kill[3][-1] == _marker(run[3])
    assert "KILL" in kill[3][2]
    assert f"{EXEC_MARKER_VAR}=$1" in kill[3][2]


# Claims: 5.6
def test_each_exec_gets_a_distinct_random_marker() -> None:
    provider, api, _, handle = _setup()
    for _ in range(3):
        provider.exec(handle, ["true"], as_user="learner", env={}, cwd="/")
    markers = {_marker(call[3]) for call in api.exec_calls}
    assert len(markers) == 3


# Claims: 5.6
def test_timeout_reports_137_timed_out_and_keeps_partial_output() -> None:
    provider, api, _, handle = _setup()
    api.exec_handler = lambda pod, c, cmd: ExecOutput(0, b"partial-out", b"partial-err")
    api.exec_duration = timedelta(seconds=30)
    result = provider.exec(handle, ["sleep", "30"], as_user="learner", env={}, cwd="/", timeout_s=2)
    assert result == ExecResult(137, b"partial-out", b"partial-err", True)


# Claims: 5.6
def test_timeout_does_not_wait_for_the_whole_command() -> None:
    provider, api, clock, handle = _setup()
    api.exec_duration = timedelta(seconds=300)
    before = clock.now()
    provider.exec(handle, ["sleep", "300"], as_user="learner", env={}, cwd="/", timeout_s=2)
    assert clock.now() - before < timedelta(seconds=300)


# Claims: 5.6
def test_kill_failure_when_the_pod_vanished_is_swallowed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    provider, api, _, handle = _setup()
    api.exec_duration = timedelta(seconds=30)

    def vanish(pod: Any, container: str, argv: Any) -> ExecOutput | None:
        api.vanish_pod(NS, handle.sandbox_id)
        return ExecOutput(0, b"before-loss", b"")

    api.exec_handler = vanish
    with caplog.at_level(logging.WARNING):
        result = provider.exec(
            handle, ["sleep", "30"], as_user="learner", env={}, cwd="/", timeout_s=1
        )
    assert result == ExecResult(137, b"before-loss", b"", True)
    # the kill was attempted and rejected by the API (a rejected call is not recorded)
    assert "kubernetes exec failed" in caplog.text
    assert len(api.exec_calls) == 1


# Claims: 5.6
def test_kill_failure_when_the_api_is_unreachable_is_swallowed() -> None:
    provider, api, _, handle = _setup()
    api.exec_duration = timedelta(seconds=30)

    def lose_api(pod: Any, container: str, argv: Any) -> ExecOutput | None:
        api.fail_next("exec", ConnectionError(SECRET))
        return None

    api.exec_handler = lose_api
    result = provider.exec(handle, ["sleep", "30"], as_user="learner", env={}, cwd="/", timeout_s=1)
    assert result.timed_out is True and result.exit_code == 137


# Claims: 5.6
def test_other_kill_failures_are_swallowed_and_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    provider, api, _, handle = _setup()
    api.exec_duration = timedelta(seconds=30)

    def break_next(pod: Any, container: str, argv: Any) -> ExecOutput | None:
        api.fail_next("exec", K8sApiError(500, "InternalError", SECRET))
        return None

    api.exec_handler = break_next
    with caplog.at_level(logging.WARNING):
        result = provider.exec(
            handle, ["sleep", "30"], as_user="learner", env={}, cwd="/", timeout_s=1
        )
    assert result.timed_out is True and result.exit_code == 137
    assert SECRET in caplog.text


# Claims: 5.6
@pytest.mark.parametrize("timeout_s", [None, 60])
def test_commands_that_do_not_time_out_never_trigger_a_kill(timeout_s: float | None) -> None:
    provider, api, _, handle = _setup()
    api.exec_handler = lambda pod, c, cmd: ExecOutput(5, b"o", b"e")
    api.exec_duration = timedelta(seconds=1)
    result = provider.exec(
        handle, ["false"], as_user="learner", env={}, cwd="/", timeout_s=timeout_s
    )
    assert result == ExecResult(5, b"o", b"e", False)
    assert len(api.exec_calls) == 1


# Claims: 5.6
def test_exit_137_from_another_cause_before_the_timeout_is_not_timed_out() -> None:
    provider, api, _, handle = _setup()
    api.exec_handler = lambda pod, c, cmd: ExecOutput(137, b"oom", b"")
    api.exec_duration = timedelta(seconds=1)
    result = provider.exec(handle, ["hog"], as_user="learner", env={}, cwd="/", timeout_s=30)
    assert result == ExecResult(137, b"oom", b"", False)
    assert len(api.exec_calls) == 1


# Claims: 5.6
def test_exit_137_without_a_timeout_argument_is_not_timed_out() -> None:
    provider, api, _, handle = _setup()
    api.exec_handler = lambda pod, c, cmd: ExecOutput(137)
    result = provider.exec(handle, ["hog"], as_user="learner", env={}, cwd="/")
    assert result.timed_out is False
    assert len(api.exec_calls) == 1


# Claims: 5.6
def test_exit_137_at_the_deadline_without_the_flag_is_reported_timed_out() -> None:
    provider, api, _, handle = _setup()
    api.exec_handler = lambda pod, c, cmd: ExecOutput(137, b"x", b"")
    api.exec_duration = timedelta(seconds=5)
    result = provider.exec(handle, ["sleep", "9"], as_user="learner", env={}, cwd="/", timeout_s=5)
    assert result.timed_out is True and result.exit_code == 137
    assert len(api.exec_calls) == 2


# Claims: 1.12
def test_exec_on_an_unreachable_api_is_provider_unavailable_without_raw_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    provider, api, _, handle = _setup()
    api.fail_next("exec", ConnectionError(SECRET))
    with caplog.at_level(logging.WARNING), pytest.raises(ProviderUnavailable) as info:
        provider.exec(handle, ["true"], as_user="learner", env={}, cwd="/")
    assert SECRET not in str(info.value)
    assert SECRET in caplog.text


# --------------------------------------------------------------------------- terminal


def _channel() -> tuple[TerminalChannel, FakeK8sApi, SandboxHandle]:
    provider, api, _, handle = _setup()
    return provider.attach_terminal(handle), api, handle


# Claims: 1.10
def test_terminal_read_times_out_with_empty_bytes_and_advances_the_clock() -> None:
    provider, _api, clock, handle = _setup()
    channel = provider.attach_terminal(handle)
    before = clock.now()
    assert channel.read(16, timeout_s=0.5) == b""
    assert clock.now() - before == timedelta(seconds=0.5)


# Claims: 1.10
def test_terminal_read_returns_at_most_max_bytes_in_order() -> None:
    channel, api, _ = _channel()
    api.terminals[0].feed(b"abcdef")
    assert channel.read(4) == b"abcd"
    assert channel.read(4) == b"ef"


# Claims: 1.10
def test_terminal_writes_and_resizes_reach_the_stream_in_call_order() -> None:
    channel, api, _ = _channel()
    stream = api.terminals[0]
    events: list[str] = []
    orig_write, orig_resize = stream.write, stream.resize

    def traced_write(data: bytes) -> None:
        events.append(f"w:{data!r}")
        orig_write(data)

    def traced_resize(rows: int, cols: int) -> None:
        events.append(f"r:{rows}x{cols}")
        orig_resize(rows, cols)

    stream.write = traced_write  # type: ignore[method-assign]
    stream.resize = traced_resize  # type: ignore[method-assign]
    channel.write(b"a")
    channel.resize(10, 20)
    channel.write(b"b")
    channel.resize(30, 40)
    assert events == ["w:b'a'", "r:10x20", "w:b'b'", "r:30x40"]
    assert stream.written == [b"a", b"b"]
    assert stream.sizes == [(10, 20), (30, 40)]


# Claims: 1.10
def test_terminal_attach_opens_a_login_shell_with_a_marker_in_learner_container() -> None:
    channel, api, handle = _channel()
    stream = api.terminals[0]
    assert (stream.container, stream.name) == ("learner", handle.sandbox_id)
    assert any(a.startswith(f"{EXEC_MARKER_VAR}=") for a in stream.command)
    assert "/workspace" in stream.command
    channel.close()


# Claims: 1.10
def test_terminal_close_kills_the_shell_marker_once_in_the_learner_container() -> None:
    channel, api, _ = _channel()
    marker = _marker(api.terminals[0].command)
    channel.close()
    channel.close()
    assert api.terminals[0].closed
    assert len(api.exec_calls) == 1
    _, _, container, argv = api.exec_calls[0]
    assert container == "learner"
    assert argv[-1] == marker and "KILL" in argv[2]


# Claims: 1.10
def test_terminal_close_is_silent_when_stream_close_and_kill_fail() -> None:
    channel, api, _ = _channel()
    stream = api.terminals[0]

    def boom() -> None:
        raise RuntimeError(SECRET)

    stream.close = boom  # type: ignore[method-assign]
    api.fail_next("exec", K8sApiError(500, "InternalError", SECRET))
    channel.close()  # must not raise
    channel.close()
    with pytest.raises(SandboxGone):
        channel.read(1)


# Claims: 1.10
def test_terminal_operations_after_close_raise_gone_without_touching_the_stream() -> None:
    channel, api, _ = _channel()
    channel.close()
    stream = api.terminals[0]
    with pytest.raises(SandboxGone):
        channel.write(b"x")
    with pytest.raises(SandboxGone):
        channel.resize(1, 1)
    assert stream.written == [] and stream.sizes == []


# Claims: 1.11, 1.12
@pytest.mark.parametrize("how", ["vanish", "delete", "evict", "deadline"])
def test_terminal_read_write_resize_raise_gone_after_pod_loss(how: str) -> None:
    for op in ("read", "write", "resize"):
        channel, api, handle = _channel()
        if how == "vanish":
            api.vanish_pod(NS, handle.sandbox_id)
        elif how == "delete":
            api.delete_pod(NS, handle.sandbox_id)
        elif how == "evict":
            api.evict_pod(NS, handle.sandbox_id)
        else:
            api.evict_pod(NS, handle.sandbox_id, reason="DeadlineExceeded")
        with pytest.raises(SandboxGone):
            if op == "read":
                channel.read(1)
            elif op == "write":
                channel.write(b"x")
            else:
                channel.resize(1, 1)
        channel.close()  # still idempotent and silent


# Claims: 1.11, 1.12
def test_terminal_pod_loss_error_message_is_fixed_and_carries_no_api_text() -> None:
    channel, api, handle = _channel()
    api.vanish_pod(NS, handle.sandbox_id)
    with pytest.raises(SandboxGone) as info:
        channel.read(1)
    assert str(info.value) == "sandbox is gone"
    assert handle.sandbox_id not in str(info.value)


# Claims: 1.12
def test_raw_stream_text_goes_to_the_log_not_the_exception(
    caplog: pytest.LogCaptureFixture,
) -> None:
    channel, api, _ = _channel()
    stream = api.terminals[0]

    def closed(*_a: object) -> bytes:
        raise K8sStreamClosed(SECRET)

    stream.read = closed  # type: ignore[method-assign,assignment]
    with caplog.at_level(logging.WARNING), pytest.raises(SandboxGone) as info:
        channel.read(1)
    assert SECRET not in str(info.value)
    assert SECRET in caplog.text


# Claims: 1.12
@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (RuntimeError(SECRET), SandboxError),
        (ConnectionError(SECRET), ProviderUnavailable),
        (K8sApiError(404, "NotFound", SECRET), SandboxGone),
        (K8sApiError(400, "BadRequest", SECRET), SandboxGone),
        (K8sApiError(500, "InternalError", SECRET), SandboxError),
    ],
)
@pytest.mark.parametrize("op", ["read", "write", "resize"])
def test_terminal_errors_are_translated_and_never_leak_raw_text(
    error: Exception, expected: type[SandboxError], op: str
) -> None:
    channel, api, _ = _channel()
    stream = api.terminals[0]

    def fail(*_a: object) -> Any:
        raise error

    setattr(stream, op, fail)
    with pytest.raises(expected) as info:
        if op == "read":
            channel.read(1)
        elif op == "write":
            channel.write(b"x")
        else:
            channel.resize(1, 1)
    assert SECRET not in str(info.value)


# Claims: 1.11
@pytest.mark.parametrize("how", ["vanish", "delete", "evict"])
def test_attach_terminal_to_a_lost_pod_is_gone_without_raw_text(how: str) -> None:
    provider, api, _, handle = _setup()
    if how == "vanish":
        api.vanish_pod(NS, handle.sandbox_id)
    elif how == "delete":
        api.delete_pod(NS, handle.sandbox_id)
    else:
        api.evict_pod(NS, handle.sandbox_id)
    with pytest.raises(SandboxGone) as info:
        provider.attach_terminal(handle)
    assert str(info.value) == "sandbox is gone"
    assert api.terminals == []


# Claims: 1.11
def test_attach_terminal_api_failures_do_not_leak_raw_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    provider, api, _, handle = _setup()
    api.fail_next("terminal", K8sApiError(500, "InternalError", SECRET))
    with caplog.at_level(logging.WARNING), pytest.raises(SandboxError) as info:
        provider.attach_terminal(handle)
    assert SECRET not in str(info.value)
    assert SECRET in caplog.text
    api.fail_next("terminal", ConnectionError(SECRET))
    with pytest.raises(ProviderUnavailable) as info2:
        provider.attach_terminal(handle)
    assert SECRET not in str(info2.value)


# Claims: 5.6
def test_timeout_runs_the_command_under_an_in_container_timeout_kill() -> None:
    """A second exec cannot start once a fork bomb has used the pod PID limit, so the command
    carries its own ``timeout -s KILL`` (as in the Docker provider); the client limit is only a
    backstop after it. Without ``timeout_s`` there is no wrapper (freeze and thaw need that)."""
    provider, api, _, handle = _setup()
    provider.exec(handle, ["sleep", "3"], as_user="learner", env={}, cwd="/", timeout_s=5)
    provider.exec(handle, ["sleep", "3"], as_user="learner", env={}, cwd="/")
    bounded, unbounded = api.exec_calls[0][3], api.exec_calls[1][3]
    i = bounded.index("/usr/bin/timeout")
    assert bounded[i : i + 4] == ("/usr/bin/timeout", "-s", "KILL", "5.000")
    # Inside the timeout: the OOM-score step (the command dies first on OOM, not the timeout),
    # then the command itself.
    assert bounded[i + 4 : i + 6] == ("/bin/sh", "-c")
    assert "oom_score_adj" in bounded[i + 6]
    assert bounded[-2:] == ("sleep", "3")
    assert "/usr/bin/timeout" not in unbounded


def _start_failures(api: FakeK8sApi, count: int) -> list[int]:
    """Make the next ``count`` exec starts fail as the runtime does at the pod PID limit."""
    original = api.exec_command
    left = [count]

    def exec_command(*args, **kwargs):  # type: ignore[no-untyped-def]
        if left[0] > 0:
            left[0] -= 1
            raise K8sApiError(
                500, "InternalError", 'failed to exec in container: failed to start exec "x"'
            )
        return original(*args, **kwargs)

    api.exec_command = exec_command  # type: ignore[method-assign]
    return left


# Claims: 5.6, 1.11
def test_exec_start_failure_is_retried_until_it_starts() -> None:
    provider, api, clock, handle = _setup()
    left = _start_failures(api, 3)
    before = clock.now()
    result = provider.exec(handle, ["true"], as_user="learner", env={}, cwd="/")
    assert result.exit_code == 0 and left == [0]
    assert clock.now() - before >= timedelta(seconds=0.25 + 0.5 + 1.0)


# Claims: 5.6, 1.11
def test_exec_start_failure_gives_up_at_the_operation_timeout() -> None:
    from edu_it_domain.config import SANDBOX_OPERATION_TIMEOUT
    from edu_it_domain.ports.sandbox import SandboxError

    provider, api, clock, handle = _setup()
    _start_failures(api, 10_000)
    before = clock.now()
    with pytest.raises(SandboxError):
        provider.exec(handle, ["true"], as_user="learner", env={}, cwd="/")
    assert clock.now() - before <= SANDBOX_OPERATION_TIMEOUT


# Claims: 1.11
def test_other_server_errors_are_not_retried() -> None:
    from edu_it_domain.ports.sandbox import SandboxError

    provider, api, _, handle = _setup()
    original = api.exec_command
    calls = []

    def exec_command(*args, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(1)
        raise K8sApiError(500, "InternalError", "etcd unavailable")

    api.exec_command = exec_command  # type: ignore[method-assign]
    with pytest.raises(SandboxError):
        provider.exec(handle, ["true"], as_user="learner", env={}, cwd="/")
    assert calls == [1]
    api.exec_command = original  # type: ignore[method-assign]
