"""The ``Fake_SandboxProvider`` (hands-on-labs Req 1.2, 1.3, 25.7).

An in-memory :class:`~edu_it_domain.ports.sandbox.SandboxProvider`. Sandboxes
are plain data: a set of users, a file map (path -> bytes) and a list of
"processes". Every call advances the injected :class:`Test_Clock` by a
configurable virtual duration, so timing rules (provision timeouts, the grading
budget) are testable without sleeping.

It can be scripted to fail, hang (raise ``ProvisionTimeout``) or partially
create a sandbox, and ``exec`` understands only the few commands the grading
flow needs (``kill -s STOP|CONT|KILL -1``, ``ps``) plus an optional scripted
handler. It imports no subprocess, socket or Docker module.
"""

from __future__ import annotations

import itertools
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import timedelta
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from edu_it_domain.ports.sandbox import (
    ExecResult,
    HealthStatus,
    ProviderUnavailable,
    ProvisionError,
    ProvisionTimeout,
    SandboxGone,
    SandboxHandle,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from edu_it_domain.labs.models import SandboxSpec
    from edu_it_domain.ports.sandbox import TerminalChannel

    from edu_it_infrastructure.fakes.clock import Test_Clock

__all__ = [
    "ExecHandler",
    "FakeProcess",
    "FakeSandbox",
    "Fake_SandboxProvider",
    "ProvisionMode",
]

_PROVIDER = "fake"
_KILLED_EXIT = 137

type ExecHandler = Callable[[FakeSandbox, Sequence[str], str], ExecResult | None]
"""Scripted ``exec`` hook: ``(sandbox, command, as_user)``; ``None`` falls through."""


class ProvisionMode(Enum):
    """How one ``provision`` call behaves."""

    OK = "ok"
    FAIL = "fail"
    HANG = "hang"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


@dataclass(slots=True)
class FakeProcess:
    """A process modeled as data. ``state`` uses ps letters: R, S, T, Z."""

    pid: int
    user: str
    command: str
    state: str = "S"


@dataclass(slots=True)
class FakeSandbox:
    """In-memory sandbox state."""

    handle: SandboxHandle
    spec: SandboxSpec
    users: set[str] = field(default_factory=lambda: {"root", "learner", "grader"})
    files: dict[str, bytes] = field(default_factory=dict)
    processes: list[FakeProcess] = field(default_factory=list)
    destroyed: bool = False
    healthy: bool = True


class _FakeTerminal:
    """Loopback TTY: written bytes are echoed back to ``read``."""

    def __init__(self, sandbox: FakeSandbox) -> None:
        self._sandbox = sandbox
        self._buffer = bytearray()
        self._closed = False
        self.size: tuple[int, int] = (24, 80)
        self.written: list[bytes] = []

    def _check(self) -> None:
        if self._closed or self._sandbox.destroyed:
            raise SandboxGone("terminal closed")

    def read(self, max_bytes: int, timeout_s: float | None = None) -> bytes:
        self._check()
        chunk = bytes(self._buffer[:max_bytes])
        del self._buffer[:max_bytes]
        return chunk

    def write(self, data: bytes) -> None:
        self._check()
        self.written.append(data)
        self._buffer.extend(data)

    def resize(self, rows: int, cols: int) -> None:
        self._check()
        self.size = (rows, cols)

    def close(self) -> None:
        self._closed = True


class Fake_SandboxProvider:
    """In-memory sandbox provider driven by a :class:`Test_Clock`."""

    def __init__(
        self,
        clock: Test_Clock,
        *,
        call_duration: timedelta = timedelta(0),
        provision_duration: timedelta | None = None,
        hang_duration: timedelta = timedelta(seconds=3600),
        default_mode: ProvisionMode = ProvisionMode.OK,
    ) -> None:
        self._clock = clock
        self.call_duration = call_duration
        self.provision_duration = provision_duration
        self.hang_duration = hang_duration
        self.default_mode = default_mode
        self.exec_handler: ExecHandler | None = None
        self.calls: list[str] = []
        self._scripted: deque[ProvisionMode] = deque()
        self._sandboxes: dict[str, FakeSandbox] = {}
        self._ids = itertools.count(1)
        self._pids = itertools.count(100)
        self._lock = threading.RLock()

    # -- scripting ---------------------------------------------------------

    def script_provision(self, *modes: ProvisionMode) -> None:
        """Queue modes for the next ``provision`` calls; then ``default_mode``."""
        with self._lock:
            self._scripted.extend(modes)

    def sandbox(self, handle: SandboxHandle) -> FakeSandbox:
        """Inspect a sandbox's state (including destroyed ones)."""
        return self._sandboxes[handle.sandbox_id]

    def add_process(
        self, handle: SandboxHandle, user: str, command: str, state: str = "S"
    ) -> FakeProcess:
        """Add a process (as data) to a live sandbox."""
        with self._lock:
            sb = self._live(handle)
            proc = FakeProcess(next(self._pids), user, command, state)
            sb.processes.append(proc)
            return proc

    def vanish(self, handle: SandboxHandle) -> None:
        """Simulate the sandbox disappearing underneath the caller."""
        with self._lock:
            self._sandboxes[handle.sandbox_id].destroyed = True

    # -- internals ---------------------------------------------------------

    def _tick(self, name: str, duration: timedelta | None = None) -> None:
        self.calls.append(name)
        d = self.call_duration if duration is None else duration
        if d > timedelta(0):
            self._clock.advance(d)

    def _live(self, handle: SandboxHandle) -> FakeSandbox:
        sb = self._sandboxes.get(handle.sandbox_id)
        if sb is None or sb.destroyed:
            raise SandboxGone(handle.sandbox_id)
        return sb

    def _new_sandbox(self, spec: SandboxSpec) -> FakeSandbox:
        handle = SandboxHandle(
            sandbox_id=f"fake-{next(self._ids)}",
            session_stable_id=spec.session_stable_id,
            provider=_PROVIDER,
        )
        sb = FakeSandbox(handle=handle, spec=spec)
        self._sandboxes[handle.sandbox_id] = sb
        return sb

    # -- SandboxProvider ---------------------------------------------------

    def provision(self, spec: SandboxSpec) -> SandboxHandle:
        with self._lock:
            mode = self._scripted.popleft() if self._scripted else self.default_mode
            if mode is ProvisionMode.HANG:
                self._tick("provision", self.hang_duration)
                raise ProvisionTimeout("provision timed out")
            self._tick("provision", self.provision_duration)
            if mode is ProvisionMode.UNAVAILABLE:
                raise ProviderUnavailable("provider unavailable")
            if mode is ProvisionMode.FAIL:
                raise ProvisionError("provision failed")
            sb = self._new_sandbox(spec)
            if mode is ProvisionMode.PARTIAL:
                raise ProvisionError("partially created", partial_handle=sb.handle)
            return sb.handle

    def exec(
        self,
        handle: SandboxHandle,
        command: Sequence[str],
        *,
        as_user: str,
        env: Mapping[str, str],
        cwd: str,
        timeout_s: float | None = None,
    ) -> ExecResult:
        with self._lock:
            self._tick("exec")
            sb = self._live(handle)
            if as_user not in sb.users:
                return ExecResult(1, b"", b"unknown user", False)
            if self.exec_handler is not None:
                scripted = self.exec_handler(sb, command, as_user)
                if scripted is not None:
                    return scripted
            return self._builtin(sb, list(command), as_user)

    @staticmethod
    def _builtin(sb: FakeSandbox, command: list[str], as_user: str) -> ExecResult:
        name = PurePosixPath(command[0]).name if command else ""
        if name == "kill" and command[-1] == "-1" and "-s" in command:
            signal = command[command.index("-s") + 1].upper()
            mine = [p for p in sb.processes if as_user == "root" or p.user == as_user]
            if signal == "STOP":
                for p in mine:
                    if p.state != "Z":
                        p.state = "T"
            elif signal == "CONT":
                for p in mine:
                    if p.state == "T":
                        p.state = "S"
            elif signal == "KILL":
                sb.processes[:] = [p for p in sb.processes if p not in mine]
            return ExecResult(0, b"", b"", False)
        if name == "ps":
            rows = "".join(f"{p.user} {p.state} {p.pid}\n" for p in sb.processes)
            return ExecResult(0, rows.encode(), b"", False)
        return ExecResult(0, b"", b"", False)

    def attach_terminal(self, handle: SandboxHandle) -> TerminalChannel:
        with self._lock:
            self._tick("attach_terminal")
            return _FakeTerminal(self._live(handle))

    def copy_in(
        self,
        handle: SandboxHandle,
        source_ro: Path,
        dest: str,
        *,
        as_user: str | None = None,
    ) -> None:
        with self._lock:
            self._tick("copy_in")
            sb = self._live(handle)
            base = PurePosixPath(dest)
            if source_ro.is_dir():
                for path in sorted(p for p in source_ro.rglob("*") if p.is_file()):
                    rel = PurePosixPath(*path.relative_to(source_ro).parts)
                    sb.files[str(base / rel)] = path.read_bytes()
            else:
                sb.files[str(base)] = source_ro.read_bytes()

    def copy_out(self, handle: SandboxHandle, source: str, dest: Path) -> None:
        with self._lock:
            self._tick("copy_out")
            sb = self._live(handle)
            if source not in sb.files:
                raise FileNotFoundError(source)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(sb.files[source])

    def destroy(self, handle: SandboxHandle) -> None:
        with self._lock:
            self._tick("destroy")
            sb = self._sandboxes.get(handle.sandbox_id)
            if sb is not None:
                sb.destroyed = True

    def health(self, handle: SandboxHandle) -> HealthStatus:
        with self._lock:
            self._tick("health")
            sb = self._sandboxes.get(handle.sandbox_id)
            if sb is None or sb.destroyed:
                return HealthStatus(False, "gone")
            return HealthStatus(sb.healthy)

    def list_sandboxes(self) -> Sequence[SandboxHandle]:
        with self._lock:
            self._tick("list_sandboxes")
            return tuple(s.handle for s in self._sandboxes.values() if not s.destroyed)
