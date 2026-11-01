"""``DockerSandboxProvider``: the dev-only Docker backend (hands-on-labs Req 1.2, 1.9,
2.1, 2.3, 3.7, 4.15, 8.4).

Implements every :class:`~edu_it_domain.ports.sandbox.SandboxProvider` operation on
top of the Docker SDK for Python. It is for local development only: containers share
the host kernel and run under ``runc``, which is not a strong enough boundary for
untrusted code in production (Req 2.1; composition refuses it there, Req 2.2).

Command forms
-------------
Every command run *inside* a sandbox uses the absolute paths and argv forms recorded by
the flag-compatibility spike in ``lab-images/README.md`` ("Command forms used inside
sandboxes"): ``/bin/kill -s <SIG> -1``, ``/bin/ps -e -o ...``,
``/usr/bin/timeout -s KILL <s> <cmd>``, ``/bin/tar -c|-x ...``. The ``kill -KILL -- -1``
form is never used (BusyBox rejects ``--``).

Deadlines
---------
* The SDK client is built with ``timeout=SANDBOX_OPERATION_TIMEOUT``, so a call to an
  unreachable daemon fails within that bound with :class:`ProviderUnavailable`. The client
  is created lazily and a failed creation is not cached, so no provider, Lab_Session or
  Sandbox state changes when the daemon is down (Req 1.9).
* ``exec`` with ``timeout_s`` runs the command under ``/usr/bin/timeout -s KILL`` (exit
  status 137 on expiry; the wrapper kills its whole process group) and adds a client-side
  backstop of ``timeout_s + _BACKSTOP_GRACE``.

Decision: killing leftover processes after a timeout without stopping the sandbox
--------------------------------------------------------------------------------
The design asks for ``kill -s KILL -1`` as the same user after a timeout, so that nothing the
user started (including processes that left the timeout wrapper's process group with
``setsid``, or were orphaned) survives. The spike found that with ``--init`` this also
kills the container's own keep-alive process, which is owned by the container user
(uid 1000); ``docker-init`` then exits and the container stops.

Resolution used here (``_kill_user_processes``), with no change to the user, capabilities
or lockdown: the keep-alive command is a known ``/bin/sleep infinity`` whose PID is
recorded at provision time (and re-discovered from ``ps`` if the provider restarted:
the lowest PID whose parent is PID 1 and whose args are the keep-alive command). Cleanup
then (1) freezes every process the user may signal with ``/bin/kill -s STOP -1`` so nothing
can fork while it works, (2) lists PIDs with ``/bin/ps``, (3) sends ``/bin/kill -s KILL``
to every PID except PID 1 and the keep-alive process, repeating until the list is empty,
and (4) thaws only the keep-alive process with ``/bin/kill -s CONT <pid>``. The result
is the same as ``kill -s KILL -1`` for everything the user started, while the sandbox
stays alive for the next ``exec``, ``copy_out`` or grading step.

A learner who runs ``kill -s KILL -1`` *themselves* in the terminal still ends their own
container, because the lockdown forbids anything that would protect the keep-alive
process. The sandbox then reports unhealthy / gone, which only affects that learner.

Streaming
---------
``copy_in`` / ``copy_out`` stream a tar archive through an internal ``exec`` (``tar -x`` /
``tar -c``) and never use ``put_archive`` / ``get_archive``. Archives are padded to the
tar record size, so ``tar -x`` finishes at the end-of-archive marker; this matters on
Windows named pipes, which cannot half-close stdin.
"""

from __future__ import annotations

import contextlib
import logging
import os
import queue
import secrets
import socket as _socket
import tarfile
import tempfile
import threading
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path, PurePosixPath
from typing import IO, Any

import docker
import docker.errors
import requests
from docker.utils import kwargs_from_env
from docker.utils.socket import STDOUT, frames_iter, read
from edu_it_domain.config import SANDBOX_OPERATION_TIMEOUT
from edu_it_domain.labs.models import SandboxSpec
from edu_it_domain.ports.sandbox import (
    ExecResult,
    HealthStatus,
    ProviderUnavailable,
    ProvisionError,
    ProvisionTimeout,
    SandboxError,
    SandboxGone,
    SandboxHandle,
    TerminalChannel,
)

from edu_it_infrastructure.sandbox.docker_spec import (
    DEFAULT_LAB_IMAGE_TAG,
    LABEL_MANAGED,
    LABEL_SESSION,
    to_container_kwargs,
)

__all__ = [
    "KEEPALIVE_COMMAND",
    "PROVIDER_NAME",
    "RUNC_WARNING",
    "DockerSandboxProvider",
]

_LOG = logging.getLogger(__name__)

PROVIDER_NAME = "docker"

RUNC_WARNING = (
    "DockerSandboxProvider is a DEVELOPMENT-ONLY sandbox backend: containers share the "
    "host kernel and run under the default runc runtime, which is not a sufficient "
    "isolation boundary for untrusted code. Do not use it in production."
)

#: Keep-alive main process of every sandbox (image default CMD would exit at once).
KEEPALIVE_COMMAND: tuple[str, ...] = ("/bin/sleep", "infinity")
_KEEPALIVE_ARGS = " ".join(KEEPALIVE_COMMAND)

# Absolute paths and argv forms from lab-images/README.md (spike 3.4).
_KILL = "/bin/kill"
_PS = "/bin/ps"
_TIMEOUT = "/usr/bin/timeout"
_TAR = "/bin/tar"
_PS_ARGV = (_PS, "-e", "-o", "user=", "-o", "pid=", "-o", "ppid=", "-o", "args=")

_SH = "/bin/sh"
_TR = "/usr/bin/tr"
_GREP = "/bin/grep"

#: Environment variable carrying a random per-terminal marker; the shell and every process
#: it starts inherit it, so closing the terminal can kill exactly those processes.
_TERMINAL_MARKER_VAR = "EDU_IT_TERMINAL"
#: ``$1`` is the marker. BusyBox grep has no ``-z``, so NULs are turned into newlines first.
_KILL_MARKED_SCRIPT = (
    "for d in /proc/[0-9]*; do "
    f'{_TR} "\\000" "\\n" < "$d/environ" 2>/dev/null '
    f'| {_GREP} -qxF "{_TERMINAL_MARKER_VAR}=$1" '
    f'&& {_KILL} -s KILL "${{d#/proc/}}" 2>/dev/null; '
    "done; exit 0"
)

_KILLED_EXIT = 137
_LEARNER = "learner"
_ROOT = "root"
_GRADER_DIR = "/grader"

#: Slack added to ``timeout_s`` for the client-side backstop.
_BACKSTOP_GRACE = 2.0
#: Timing tolerance when deciding that an exit status 137 came from ``timeout``.
_TIMEOUT_EPSILON = 0.25
#: Per-stream cap on captured ``exec`` output (grading harness data only).
_MAX_CAPTURE = 1024 * 1024
_CHUNK = 64 * 1024
_CLEANUP_ROUNDS = 3
_KILL_BATCH = 100
_CLEANUP_EXEC_ATTEMPTS = 3


# --------------------------------------------------------------------------
# error classification
# --------------------------------------------------------------------------


def _is_connectivity_error(exc: BaseException) -> bool:
    """True when ``exc`` means the daemon could not be reached in time."""

    if isinstance(exc, docker.errors.APIError):
        return False
    if isinstance(
        exc,
        (
            requests.exceptions.ConnectionError,
            requests.exceptions.Timeout,
            ConnectionError,
            TimeoutError,
            OSError,
            docker.errors.DockerException,
        ),
    ):
        return True
    # ``pywintypes.error`` is raised by the Windows named-pipe transport when the
    # daemon is not running; the module is only importable on Windows.
    return type(exc).__module__ == "pywintypes"


def _is_read_timeout(exc: BaseException) -> bool:
    return isinstance(exc, requests.exceptions.ReadTimeout)


def _api_error_text(exc: docker.errors.APIError) -> str:
    return str(exc.explanation or exc)


@contextlib.contextmanager
def _translate(what: str) -> Iterator[None]:
    """Map SDK/transport failures of one daemon call to port errors."""

    try:
        yield
    except SandboxError:
        raise
    except docker.errors.NotFound as exc:
        raise SandboxGone(what) from exc
    except docker.errors.APIError as exc:
        text = _api_error_text(exc)
        if exc.status_code == 409 and ("not running" in text or "paused" in text):
            raise SandboxGone(f"{what}: {text}") from exc
        raise SandboxError(f"{what}: {text}") from exc
    except Exception as exc:
        if _is_connectivity_error(exc):
            raise ProviderUnavailable(f"{what}: Docker daemon unreachable ({exc})") from exc
        raise


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------


class _Capture:
    """Bounded in-memory sink."""

    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._buf = bytearray()

    def __call__(self, data: bytes) -> None:
        room = self._limit - len(self._buf)
        if room > 0:
            self._buf.extend(data[:room])

    def value(self) -> bytes:
        return bytes(self._buf)


@dataclass(frozen=True, slots=True)
class _Row:
    user: str
    pid: int
    ppid: int
    args: str


def _parse_ps(text: str) -> list[_Row]:
    rows: list[_Row] = []
    for line in text.splitlines():
        parts = line.split(None, 3)
        if len(parts) < 3 or not parts[1].isdigit() or not parts[2].isdigit():
            continue
        rows.append(
            _Row(parts[0], int(parts[1]), int(parts[2]), parts[3] if len(parts) > 3 else "")
        )
    return rows


def _raw(sock: Any) -> Any:
    """The writable socket behind a hijacked ``exec`` connection.

    On a unix-socket daemon (Linux) the Docker SDK returns a ``socket.SocketIO`` wrapper,
    which has no ``sendall`` or ``shutdown``; the socket itself is its ``_sock``. Named
    pipes (Windows) and TLS connections already return an object with ``sendall``.
    """

    return sock._sock if isinstance(sock, _socket.SocketIO) else sock


def _half_close(sock: Any) -> None:
    """Signal EOF on stdin where the transport supports it.

    Windows named pipes cannot half-close (``shutdown`` would close the whole pipe), so
    callers rely on a record-padded tar archive instead.
    """

    if type(sock).__name__ == "NpipeSocket":
        return
    with contextlib.suppress(Exception):
        _raw(sock).shutdown(_socket.SHUT_WR)


def _send_stream(sock: Any, stream: IO[bytes]) -> None:
    raw = _raw(sock)
    while chunk := stream.read(_CHUNK):
        raw.sendall(chunk)


def _tar_mode(st_mode: int, *, is_dir: bool) -> int:
    if os.name == "nt":
        return 0o755 if is_dir else 0o644
    return st_mode & 0o777


def _write_tar(source: Path, arcname: str | None, out: IO[bytes]) -> None:
    """Write ``source`` as a GNU tar to ``out``.

    A directory is archived by its *contents* (no leading directory); a file is archived
    under ``arcname``. Owner is normalized to 0:0 (extraction ignores it anyway), symlinks
    and special files are refused (no-follow rule), and the archive is padded to the tar
    record size by ``close``.
    """

    with tarfile.open(fileobj=out, mode="w", format=tarfile.GNU_FORMAT) as tf:

        def info(path: Path, name: str) -> tarfile.TarInfo:
            if path.is_symlink():
                raise SandboxError(f"refusing to copy symlink: {path}")
            st = path.stat()
            ti = tarfile.TarInfo(name)
            ti.mtime = int(st.st_mtime)
            ti.uid = ti.gid = 0
            ti.uname = ti.gname = ""
            if path.is_dir():
                ti.type = tarfile.DIRTYPE
                ti.mode = _tar_mode(st.st_mode, is_dir=True)
            elif path.is_file():
                ti.type = tarfile.REGTYPE
                ti.size = st.st_size
                ti.mode = _tar_mode(st.st_mode, is_dir=False)
            else:
                raise SandboxError(f"refusing to copy special file: {path}")
            return ti

        if source.is_dir():
            if source.is_symlink():
                raise SandboxError(f"refusing to copy symlink: {source}")
            for path in sorted(source.rglob("*")):
                ti = info(path, path.relative_to(source).as_posix())
                if ti.isdir():
                    tf.addfile(ti)
                else:
                    with path.open("rb") as fh:
                        tf.addfile(ti, fh)
        else:
            ti = info(source, arcname or source.name)
            with source.open("rb") as fh:
                tf.addfile(ti, fh)


# --------------------------------------------------------------------------
# terminal
# --------------------------------------------------------------------------


class _DockerTerminal:
    """Synchronous TTY channel over a hijacked ``exec`` connection.

    A daemon pump thread moves bytes from the socket to a queue so ``read`` can honour
    ``timeout_s`` on every platform (named pipes ignore socket timeouts).
    """

    def __init__(
        self,
        api: Any,
        exec_id: str,
        sock: Any,
        on_close: Callable[[], None] | None = None,
    ) -> None:
        self._api = api
        self._exec_id = exec_id
        self._sock = sock
        self._on_close = on_close
        self._queue: queue.Queue[bytes | None] = queue.Queue()
        self._pending = bytearray()
        self._closed = False
        self._eof = False
        self._lock = threading.Lock()
        threading.Thread(target=self._pump, name="edu-it-terminal", daemon=True).start()

    def _pump(self) -> None:
        try:
            while True:
                data = read(self._sock, 4096)
                if not data:
                    break
                self._queue.put(bytes(data))
        except Exception:
            _LOG.debug("terminal pump ended", exc_info=True)
        finally:
            self._queue.put(None)

    def _check(self) -> None:
        if self._closed:
            raise SandboxGone("terminal closed")

    def read(self, max_bytes: int, timeout_s: float | None = None) -> bytes:
        self._check()
        if not self._pending and not self._eof:
            try:
                item = (
                    self._queue.get(timeout=timeout_s)
                    if timeout_s is not None
                    else self._queue.get()
                )
            except queue.Empty:
                return b""
            if item is None:
                self._eof = True
            else:
                self._pending.extend(item)
        if self._pending:
            chunk = bytes(self._pending[:max_bytes])
            del self._pending[:max_bytes]
            return chunk
        if self._eof:
            raise SandboxGone("terminal closed")
        return b""

    def write(self, data: bytes) -> None:
        self._check()
        try:
            _raw(self._sock).sendall(data)
        except Exception as exc:
            raise SandboxGone("terminal closed") from exc

    def resize(self, rows: int, cols: int) -> None:
        self._check()
        try:
            self._api.exec_resize(self._exec_id, height=rows, width=cols)
        except Exception as exc:
            raise SandboxGone("terminal closed") from exc

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        with contextlib.suppress(Exception):
            self._sock.close()
        # Unblock a reader that is waiting without a timeout.
        self._queue.put(None)
        # Docker does not end an exec when its hijacked client disconnects, so the shell
        # (and anything it started) would outlive the connection and each reconnect would
        # leak processes until the pids limit is reached.
        if self._on_close is not None:
            try:
                self._on_close()
            except Exception:
                _LOG.warning("terminal shell cleanup failed", exc_info=True)


# --------------------------------------------------------------------------
# provider
# --------------------------------------------------------------------------


class DockerSandboxProvider:
    """Dev-only :class:`SandboxProvider` backed by a local Docker daemon."""

    def __init__(
        self,
        *,
        operation_timeout: timedelta = SANDBOX_OPERATION_TIMEOUT,
        base_url: str | None = None,
        client: Any | None = None,
        copy_timeout: timedelta = timedelta(seconds=60),
        image_tag: str = DEFAULT_LAB_IMAGE_TAG,
    ) -> None:
        self._image_tag = image_tag
        self._operation_timeout = operation_timeout.total_seconds()
        self._copy_timeout = copy_timeout.total_seconds()
        self._client_kwargs: dict[str, Any] = dict(kwargs_from_env())
        if base_url is not None:
            self._client_kwargs["base_url"] = base_url
        self._client: Any | None = client
        self._client_lock = threading.Lock()
        self._main_pids: dict[str, int] = {}
        self._pid_lock = threading.Lock()
        _LOG.warning(RUNC_WARNING)

    # -- client ------------------------------------------------------------

    def _docker(self) -> Any:
        """Return the SDK client, creating it lazily.

        Creation talks to the daemon (API version negotiation), bounded by the client
        timeout; a failure is raised as ``ProviderUnavailable`` and not cached.
        """

        with self._client_lock:
            if self._client is None:
                try:
                    self._client = docker.DockerClient(
                        version="auto",
                        timeout=self._operation_timeout,
                        **self._client_kwargs,
                    )
                except Exception as exc:
                    raise ProviderUnavailable(f"Docker daemon unreachable ({exc})") from exc
            return self._client

    def _api(self) -> Any:
        return self._docker().api

    @staticmethod
    def _container_id(handle: SandboxHandle) -> str:
        if handle.provider != PROVIDER_NAME:
            raise SandboxGone(f"not a docker sandbox: {handle.sandbox_id}")
        return handle.sandbox_id

    # -- provisioning ------------------------------------------------------

    def provision(self, spec: SandboxSpec) -> SandboxHandle:
        client = self._docker()
        kwargs = to_container_kwargs(spec, self._image_tag)
        try:
            container = client.containers.create(command=list(KEEPALIVE_COMMAND), **kwargs)
        except Exception as exc:
            raise self._provision_failure(exc, None) from exc
        handle = SandboxHandle(container.id, spec.session_stable_id, PROVIDER_NAME)
        try:
            container.start()
            container.reload()
            if container.status != "running":
                logs = container.logs(tail=5).decode("utf-8", "replace").strip()
                raise ProvisionError(f"sandbox did not stay running: {logs}")
            main_pid = self._discover_main_pid(handle)
            if main_pid is not None:
                with self._pid_lock:
                    self._main_pids[handle.sandbox_id] = main_pid
        except Exception as exc:
            self._remove_after_failure(handle, exc)
            raise self._provision_failure(exc, None) from exc
        return handle

    def _remove_after_failure(self, handle: SandboxHandle, original: BaseException) -> None:
        try:
            self._api().remove_container(handle.sandbox_id, force=True, v=True)
        except docker.errors.NotFound:
            return
        except Exception as cleanup_exc:
            raise ProvisionError(
                f"provisioning failed ({original}); cleanup failed ({cleanup_exc})",
                partial_handle=handle,
            ) from original

    @staticmethod
    def _provision_failure(exc: BaseException, handle: SandboxHandle | None) -> SandboxError:
        if isinstance(exc, ProvisionError):
            return exc
        if isinstance(exc, ProviderUnavailable):
            return exc
        if isinstance(exc, SandboxError):
            return ProvisionError(str(exc), partial_handle=handle)
        if handle is None and _is_connectivity_error(exc):
            return ProviderUnavailable(f"provision: Docker daemon unreachable ({exc})")
        if _is_read_timeout(exc):
            return ProvisionTimeout(f"provision timed out ({exc})")
        if isinstance(exc, docker.errors.APIError):
            return ProvisionError(f"provision failed: {_api_error_text(exc)}")
        return ProvisionError(f"provision failed: {exc}")

    # -- exec --------------------------------------------------------------

    def _run(
        self,
        handle: SandboxHandle,
        argv: Sequence[str],
        *,
        user: str,
        env: Mapping[str, str] | None = None,
        cwd: str | None = None,
        stdin: IO[bytes] | None = None,
        stdout: Any,
        stderr: Any,
        backstop_s: float,
    ) -> tuple[int, bool, float]:
        """Run one internal exec; return ``(exit_code, backstop_fired, elapsed_s)``."""

        cid = self._container_id(handle)
        api = self._api()
        with _translate("exec"):
            created = api.exec_create(
                cid,
                list(argv),
                stdin=stdin is not None,
                stdout=True,
                stderr=True,
                tty=False,
                user=user,
                environment=dict(env) if env else None,
                workdir=cwd,
            )
            exec_id = created["Id"]
            started = time.monotonic()
            sock = api.exec_start(exec_id, socket=True)

        done = threading.Event()

        def pump() -> None:
            try:
                if stdin is not None:
                    try:
                        _send_stream(sock, stdin)
                        _half_close(sock)
                    except Exception:
                        _LOG.debug("exec stdin ended early", exc_info=True)
                for stream, data in frames_iter(sock, False):
                    (stdout if stream == STDOUT else stderr)(bytes(data))
            except Exception:
                _LOG.debug("exec output pump ended", exc_info=True)
            finally:
                done.set()

        threading.Thread(target=pump, name="edu-it-exec", daemon=True).start()
        fired = not done.wait(backstop_s)
        if fired:
            with contextlib.suppress(Exception):
                sock.close()
        try:
            exit_code = self._await_exit(api, exec_id, started + backstop_s, fired)
        finally:
            with contextlib.suppress(Exception):
                sock.close()
        if exit_code is None:
            fired = True
            exit_code = _KILLED_EXIT
        return exit_code, fired, time.monotonic() - started

    @staticmethod
    def _await_exit(api: Any, exec_id: str, deadline: float, fired: bool) -> int | None:
        """Exit code of a finished exec; ``None`` while it still runs past ``deadline``."""

        while True:
            with _translate("exec"):
                info = api.exec_inspect(exec_id)
            if not info.get("Running"):
                code = info.get("ExitCode")
                return int(code) if code is not None else -1
            if fired or time.monotonic() >= deadline:
                return None
            time.sleep(0.05)

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
        argv = list(command)
        backstop = self._operation_timeout
        if timeout_s is not None:
            if timeout_s <= 0:
                raise ValueError("timeout_s must be positive")
            argv = [_TIMEOUT, "-s", "KILL", f"{timeout_s:.3f}", *argv]
            backstop = timeout_s + _BACKSTOP_GRACE
        out, err = _Capture(_MAX_CAPTURE), _Capture(_MAX_CAPTURE)
        code, fired, elapsed = self._run(
            handle,
            argv,
            user=as_user,
            env=env,
            cwd=cwd,
            stdout=out,
            stderr=err,
            backstop_s=backstop,
        )
        timed_out = timeout_s is not None and (
            fired or (code == _KILLED_EXIT and elapsed >= timeout_s - _TIMEOUT_EPSILON)
        )
        if timed_out:
            code = _KILLED_EXIT
            self._kill_user_processes(handle, as_user)
        return ExecResult(code, out.value(), err.value(), bool(timed_out))

    def _collect(
        self, handle: SandboxHandle, argv: Sequence[str], user: str, backstop_s: float | None = None
    ) -> tuple[int, bytes, bytes]:
        out, err = _Capture(_MAX_CAPTURE), _Capture(_MAX_CAPTURE)
        code, _fired, _elapsed = self._run(
            handle,
            argv,
            user=user,
            stdout=out,
            stderr=err,
            backstop_s=backstop_s or self._operation_timeout,
        )
        return code, out.value(), err.value()

    # -- post-timeout cleanup ----------------------------------------------

    def _discover_main_pid(self, handle: SandboxHandle) -> int | None:
        code, out, _ = self._collect(handle, _PS_ARGV, _LEARNER)
        if code != 0:
            return None
        return self._find_main_pid(_parse_ps(out.decode("utf-8", "replace")))

    @staticmethod
    def _find_main_pid(rows: Sequence[_Row]) -> int | None:
        candidates = [r.pid for r in rows if r.ppid == 1 and r.args == _KEEPALIVE_ARGS]
        return min(candidates) if candidates else None

    def _main_pid(self, handle: SandboxHandle, rows: Sequence[_Row]) -> int | None:
        with self._pid_lock:
            known = self._main_pids.get(handle.sandbox_id)
        if known is not None:
            return known
        found = self._find_main_pid(rows)
        if found is not None:
            with self._pid_lock:
                self._main_pids[handle.sandbox_id] = found
        return found

    def _cleanup_exec(
        self, handle: SandboxHandle, argv: Sequence[str], user: str
    ) -> tuple[int, bytes]:
        """Run one cleanup command, retrying when the exec itself cannot start.

        Exec can fail transiently while the PID limit is exhausted (for example by a fork
        bomb that was only just killed). Failures never propagate: cleanup is best effort.
        """

        for attempt in range(_CLEANUP_EXEC_ATTEMPTS):
            try:
                code, out, _ = self._collect(handle, argv, user)
                return code, out
            except SandboxGone:
                raise
            except SandboxError:
                _LOG.debug("cleanup exec failed (attempt %d)", attempt + 1, exc_info=True)
                time.sleep(0.2 * (attempt + 1))
        return -1, b""

    def _kill_user_processes(self, handle: SandboxHandle, user: str) -> None:
        """Kill everything ``user`` started except PID 1 and the keep-alive process.

        See the module docstring for why ``kill -s KILL -1`` is not used directly.
        """

        main_pid: int | None = None
        try:
            for _round in range(_CLEANUP_ROUNDS):
                self._cleanup_exec(handle, (_KILL, "-s", "STOP", "-1"), user)
                code, out = self._cleanup_exec(handle, _PS_ARGV, user)
                rows = _parse_ps(out.decode("utf-8", "replace")) if code == 0 else []
                main_pid = self._main_pid(handle, rows)
                protected = {1} | ({main_pid} if main_pid is not None else set())
                targets = [str(r.pid) for r in rows if r.pid not in protected]
                if not targets:
                    break
                for i in range(0, len(targets), _KILL_BATCH):
                    batch = targets[i : i + _KILL_BATCH]
                    self._cleanup_exec(handle, (_KILL, "-s", "KILL", *batch), user)
        except SandboxGone:
            return
        except SandboxError:
            _LOG.warning("post-timeout cleanup failed", exc_info=True)
        finally:
            # Thaw only the keep-alive process; user processes were killed (or, if the
            # cleanup could not finish, deliberately stay frozen).
            if main_pid is not None:
                with contextlib.suppress(SandboxError):
                    self._cleanup_exec(handle, (_KILL, "-s", "CONT", str(main_pid)), user)

    # -- terminal ----------------------------------------------------------

    def attach_terminal(self, handle: SandboxHandle) -> TerminalChannel:
        cid = self._container_id(handle)
        api = self._api()
        marker = secrets.token_hex(16)
        with _translate("attach_terminal"):
            created = api.exec_create(
                cid,
                ["/bin/bash"],
                stdin=True,
                stdout=True,
                stderr=True,
                tty=True,
                user=_LEARNER,
                environment={
                    "TERM": "xterm-256color",
                    "HOME": "/home/learner",
                    _TERMINAL_MARKER_VAR: marker,
                },
                workdir="/workspace",
            )
            sock = api.exec_start(created["Id"], tty=True, socket=True)

        def kill_shell() -> None:
            self._run(
                handle,
                [_SH, "-c", _KILL_MARKED_SCRIPT, "sh", marker],
                user=_LEARNER,
                env={},
                cwd="/",
                stdout=lambda _data: None,
                stderr=lambda _data: None,
                backstop_s=self._operation_timeout,
            )

        return _DockerTerminal(api, created["Id"], sock, on_close=kill_shell)

    # -- copy --------------------------------------------------------------

    def copy_in(
        self,
        handle: SandboxHandle,
        source_ro: Path,
        dest: str,
        *,
        as_user: str | None = None,
    ) -> None:
        """Stream ``source_ro`` into the sandbox with ``tar -x``.

        A directory is copied by its contents into the existing directory ``dest``; a file
        becomes the file ``dest`` (its parent directory must exist). Files are owned by
        ``as_user`` (default ``learner``); as ``root`` the README form with
        ``--no-same-owner`` is used (required for ``/grader``).
        """

        if not source_ro.exists():
            raise FileNotFoundError(str(source_ro))
        user = as_user or _LEARNER
        dest_path = PurePosixPath(dest)
        if source_ro.is_dir():
            target_dir, arcname = str(dest_path), None
        else:
            target_dir, arcname = str(dest_path.parent), dest_path.name
        tar_argv = [_TAR, "-x"]
        if user == _ROOT:
            tar_argv.append("--no-same-owner")
        tar_argv += ["-C", target_dir, "-f", "-"]
        argv = [_TIMEOUT, "-s", "KILL", f"{self._copy_timeout:.0f}", *tar_argv]

        with tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024) as archive:
            _write_tar(source_ro, arcname, archive)
            archive.seek(0)
            err = _Capture(_MAX_CAPTURE)
            code, fired, _ = self._run(
                handle,
                argv,
                user=user,
                stdin=archive,
                stdout=_Capture(0),
                stderr=err,
                backstop_s=self._copy_timeout + _BACKSTOP_GRACE,
            )
        if fired or code != 0:
            text = err.value().decode("utf-8", "replace").strip()
            raise SandboxError(f"copy_in to {dest} failed (exit {code}): {text}")

    def copy_out(self, handle: SandboxHandle, source: str, dest: Path) -> None:
        """Stream ``source`` out of the sandbox with ``tar -c``.

        A file is written to the file ``dest``; a directory's contents are extracted into
        the directory ``dest``. Raises ``FileNotFoundError`` if ``source`` does not exist.
        ``/grader`` is read as root, everything else as ``learner``.
        """

        src = PurePosixPath(source)
        grader = PurePosixPath(_GRADER_DIR)
        user = _ROOT if src == grader or grader in src.parents else _LEARNER
        argv = [
            _TIMEOUT,
            "-s",
            "KILL",
            f"{self._copy_timeout:.0f}",
            _TAR,
            "-c",
            "-C",
            str(src.parent),
            "-f",
            "-",
            src.name,
        ]
        with tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024) as archive:
            err = _Capture(_MAX_CAPTURE)
            code, fired, _ = self._run(
                handle,
                argv,
                user=user,
                stdout=archive.write,
                stderr=err,
                backstop_s=self._copy_timeout + _BACKSTOP_GRACE,
            )
            if fired or code != 0:
                text = err.value().decode("utf-8", "replace").strip()
                if "No such file" in text:
                    raise FileNotFoundError(source)
                raise SandboxError(f"copy_out from {source} failed (exit {code}): {text}")
            archive.seek(0)
            self._unpack(archive, src.name, dest)

    @staticmethod
    def _unpack(archive: IO[bytes], name: str, dest: Path) -> None:
        with tarfile.open(fileobj=archive, mode="r:") as tf:
            members = tf.getmembers()
            single = next((m for m in members if m.name == name), None)
            if single is not None and single.isreg() and len(members) == 1:
                fh = tf.extractfile(single)
                if fh is None:
                    raise SandboxError("copy_out: unreadable archive member")
                dest.parent.mkdir(parents=True, exist_ok=True)
                with fh, dest.open("wb") as out:
                    while chunk := fh.read(_CHUNK):
                        out.write(chunk)
                return
            dest.mkdir(parents=True, exist_ok=True)
            for member in members:
                parts = PurePosixPath(member.name).parts
                if not parts or parts[0] != name:
                    raise SandboxError(f"copy_out: unexpected archive member {member.name!r}")
                if len(parts) == 1:
                    continue
                member.name = "/".join(parts[1:])
                tf.extract(member, dest, filter="data")

    # -- lifecycle / inspection --------------------------------------------

    def destroy(self, handle: SandboxHandle) -> None:
        """Remove the container; idempotent (unknown or already-removed is success)."""

        if handle.provider != PROVIDER_NAME:
            return
        cid = handle.sandbox_id
        try:
            with _translate("destroy"):
                self._api().remove_container(cid, force=True, v=True)
        except SandboxGone:
            pass
        except SandboxError as exc:
            if "already in progress" not in str(exc):
                raise
        finally:
            with self._pid_lock:
                self._main_pids.pop(cid, None)

    def health(self, handle: SandboxHandle) -> HealthStatus:
        cid = self._container_id(handle)
        try:
            with _translate("health"):
                state = self._api().inspect_container(cid).get("State", {})
        except SandboxGone:
            return HealthStatus(False, "gone")
        if state.get("Running") and not state.get("Paused"):
            return HealthStatus(True)
        if state.get("OOMKilled"):
            return HealthStatus(False, "oom-killed")
        return HealthStatus(False, str(state.get("Status", "stopped")))

    def list_sandboxes(self) -> Sequence[SandboxHandle]:
        """Handles of managed containers only (label ``edu-it.managed``), running or not."""

        with _translate("list_sandboxes"):
            rows = self._api().containers(all=True, filters={"label": [LABEL_MANAGED]})
        return tuple(
            SandboxHandle(
                row["Id"], (row.get("Labels") or {}).get(LABEL_SESSION, ""), PROVIDER_NAME
            )
            for row in rows
        )
