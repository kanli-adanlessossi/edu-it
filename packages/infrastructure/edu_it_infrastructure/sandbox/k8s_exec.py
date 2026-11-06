"""Exec, copy and terminal adapter of the Kubernetes provider (kubernetes-deployment task 5.3).

Everything goes through the ``pods/exec`` subresource behind :class:`K8sApiPort` (ADR 0009); no
agent runs in the lab image. The container that runs a command follows the layout recorded in
ADR 0009 (layout B), through ``container_for_user`` and the ``as_user`` table of
``k8s_pod_spec``: ``learner`` to ``learner``, ``grader`` to ``grader``, ``root`` to ``owner``.
An unknown ``as_user`` raises ``SandboxError``.

``exec``
    The command is wrapped so that it starts in ``cwd`` with ``env`` and carries a random marker
    variable ``EDU_IT_EXEC`` that every descendant inherits. When ``timeout_s`` expires the port
    reports ``timed_out``; a second exec in the same container then kills every process that
    carries the marker (the command, its process group and any process that left the group or
    was orphaned) with SIGKILL, and the result is exit code 137 with ``timed_out=True``. This is
    the strategy of the Docker provider. SIGKILL is delivered even to stopped processes, and the
    kill runs as the same UID as the command, so no capability is needed. PID 1 of the container
    never carries the marker, so the pod survives and stays usable.

``copy_in`` / ``copy_out``
    A tar archive is written to ``tar -x`` over exec stdin, or read from ``tar -c`` over exec
    stdout. A destination under ``/grader`` is written in the ``owner`` container, so the files
    are owned by the owner UID and are available to the grader through the group on ``/grader``
    (group read, no group write: the learner container does not mount it at all). Any other
    destination is written in the container of ``as_user`` (default ``learner``). The host
    source is only read.

``attach_terminal``
    ``/bin/bash -l`` with a TTY in the ``learner`` container, wrapped in a ``TerminalChannel``.
    A pod that is gone, or a closed stream, raises ``SandboxGone`` from ``read``, ``write`` and
    ``resize`` (and from ``attach_terminal`` itself).

Raw API text is logged here and never put in an exception message. Time comes from the injected
``Clock``.

Requirements: kubernetes-deployment 1.1, 1.10, 1.11, 1.12, 5.1, 5.2, 5.6.
"""

from __future__ import annotations

import contextlib
import io
import logging
import os
import re
import secrets
import tarfile
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from datetime import timedelta
from pathlib import Path, PurePosixPath
from typing import IO, TYPE_CHECKING, Final

from edu_it_domain.config import SANDBOX_OPERATION_TIMEOUT
from edu_it_domain.ports.sandbox import (
    ExecResult,
    ProviderUnavailable,
    SandboxError,
    SandboxGone,
    SandboxHandle,
    TerminalChannel,
)

from edu_it_infrastructure.sandbox.k8s_api import (
    ExecOutput,
    K8sApiError,
    K8sApiPort,
    K8sTerminalStream,
)
from edu_it_infrastructure.sandbox.k8s_errors import map_api_exception
from edu_it_infrastructure.sandbox.k8s_pod_spec import (
    CONTAINER_LEARNER,
    CONTAINER_OWNER,
    container_for_user,
)

if TYPE_CHECKING:
    from edu_it_domain.ports.clock import Clock

__all__ = [
    "EXEC_MARKER_VAR",
    "K8sExecAdapter",
]

_LOG = logging.getLogger(__name__)

#: PATH of the learner terminal (the exec environment is cleared, so it is set explicitly).
_TERMINAL_PATH: Final[str] = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

#: Environment variable carrying the random per-command (or per-terminal) marker.
EXEC_MARKER_VAR: Final[str] = "EDU_IT_EXEC"

_SH: Final[str] = "/bin/sh"
_ENV: Final[str] = "/usr/bin/env"
_TIMEOUT: Final[str] = "/usr/bin/timeout"
_TAR: Final[str] = "/bin/tar"
_BASH: Final[str] = "/bin/bash"

#: ``$1`` is the working directory; the rest is the command (``env MARKER=... cmd ...``).
_RUN_SCRIPT: Final[str] = 'cd "$1" || exit 126; shift; exec "$@"'
#: Last step before the command: raise the OOM score of the command and every descendant to the
#: maximum (a non-root process may raise its own score), so a memory OOM in a container picks the
#: command's processes, never the container's init (an OOM-killed init ends the container, and
#: with ``restartPolicy: Never`` the pod) and never the ``timeout`` that must still end the
#: command. A builtin with a redirection, then ``exec``: nothing forks and the PID is kept.
_OOM_FIRST_SCRIPT: Final[str] = 'echo 1000 > /proc/self/oom_score_adj 2>/dev/null; exec "$@"'
#: ``$1`` is the marker. Runs in bash with builtins only (``read -d ''`` splits the NUL-separated
#: environment, ``kill`` is a builtin), so it forks nothing: after a fork bomb has used the pod PID
#: limit it can still run and clear the processes, where a ``tr | grep`` pipeline per PID cannot.
#: Three rounds catch processes forked while the first round ran.
_KILL_MARKED_SCRIPT: Final[str] = (
    "for i in 1 2 3; do "
    "for d in /proc/[0-9]*; do "
    'while IFS= read -r -d "" v; do '
    f'if [[ $v == "{EXEC_MARKER_VAR}=$1" ]]; then kill -s KILL "${{d#/proc/}}"; break; fi; '
    'done 2>/dev/null < "$d/environ"; '
    "done; done; exit 0"
)

_KILLED_EXIT: Final[int] = 137
_GRADER_DIR: Final[PurePosixPath] = PurePosixPath("/grader")
#: Timing tolerance when deciding that an exit status 137 came from the timeout.
_TIMEOUT_EPSILON: Final[float] = 0.25
#: Slack added to the copy timeout for the client-side wait.
_COPY_GRACE: Final[float] = 2.0
#: Slack added to ``timeout_s`` for the client-side backstop of ``exec``.
_BACKSTOP_GRACE: Final[float] = 2.0
#: Backoff of the retry when the runtime could not start an exec process.
_START_RETRY_FIRST: Final[float] = 0.25
_START_RETRY_MAX: Final[float] = 2.0
_START_FAILED_TEXT: Final[tuple[str, ...]] = ("failed to start exec", "failed to exec in container")


def _is_exec_start_failure(exc: K8sApiError) -> bool:
    """A 500 from the exec upgrade whose message says the process could not be started."""

    return exc.status == 500 and any(text in str(exc) for text in _START_FAILED_TEXT)


#: Per-stream cap on captured ``exec`` output (grading harness data only).
_MAX_CAPTURE: Final[int] = 1024 * 1024
_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")

_GONE_STATUSES: Final[frozenset[int]] = frozenset({400, 404})


def _to_sandbox_error(action: str, exc: BaseException) -> SandboxError:
    """Log ``exc`` and return the fixed-message error for it. 404, 400 and closed streams
    (a pod that is gone or not running) are ``SandboxGone``."""

    _LOG.warning("kubernetes %s failed: %s", action, exc, exc_info=True)
    if isinstance(exc, K8sApiError) and exc.status in _GONE_STATUSES:
        return SandboxGone("sandbox is gone")
    return map_api_exception(exc)


@contextlib.contextmanager
def _translate(action: str) -> Iterator[None]:
    try:
        yield
    except SandboxError:
        raise
    except Exception as exc:
        raise _to_sandbox_error(action, exc) from None


def _wrap_command(
    command: Sequence[str],
    env: Mapping[str, str],
    cwd: str,
    marker: str,
    timeout_s: float | None = None,
) -> list[str]:
    if not command:
        raise ValueError("command must not be empty")
    pairs: list[str] = []
    for key, value in env.items():
        if not _ENV_NAME.match(key) or key == EXEC_MARKER_VAR:
            raise SandboxError("invalid environment variable name")
        pairs.append(f"{key}={value}")
    return [
        _SH,
        "-c",
        _RUN_SCRIPT,
        "sh",
        cwd,
        _ENV,
        # ``-i``: the command sees exactly ``env`` plus the marker. Nothing of the container's
        # own environment (image ENV, the KUBERNETES_* service variables the kubelet injects)
        # reaches it, so a Check runs with the clean Config environment only.
        "-i",
        f"{EXEC_MARKER_VAR}={marker}",
        *pairs,
        *([_TIMEOUT, "-s", "KILL", f"{timeout_s:.3f}"] if timeout_s is not None else []),
        _SH,
        "-c",
        _OOM_FIRST_SCRIPT,
        "sh",
        *command,
    ]


def _is_grader_path(path: PurePosixPath) -> bool:
    return path == _GRADER_DIR or _GRADER_DIR in path.parents


def _tar_mode(st_mode: int, *, is_dir: bool, group_read: bool) -> int:
    """Mode of an archive member. ``group_read`` is the bundle form: group read, no group write,
    nothing for others."""

    if group_read:
        if os.name == "nt":
            return 0o750 if is_dir else 0o640
        base = (st_mode & 0o750) | 0o040
        return base | 0o010 if is_dir else base
    if os.name == "nt":
        return 0o755 if is_dir else 0o644
    return st_mode & 0o777


def _write_tar(source: Path, arcname: str | None, out: IO[bytes], *, group_read: bool) -> None:
    """Write ``source`` as a GNU tar to ``out`` (read-only on the host).

    A directory is archived by its contents; a file under ``arcname``. Owner is normalized to
    0:0 (extraction by a non-root user ignores it), symlinks and special files are refused, and
    the archive is padded to the tar record size so ``tar -x`` ends at the end-of-archive marker.
    """

    with tarfile.open(fileobj=out, mode="w", format=tarfile.GNU_FORMAT) as tf:

        def info(path: Path, name: str) -> tarfile.TarInfo:
            if path.is_symlink():
                raise SandboxError("refusing to copy a symlink")
            st = path.stat()
            ti = tarfile.TarInfo(name)
            ti.mtime = int(st.st_mtime)
            ti.uid = ti.gid = 0
            ti.uname = ti.gname = ""
            if path.is_dir():
                ti.type = tarfile.DIRTYPE
                ti.mode = _tar_mode(st.st_mode, is_dir=True, group_read=group_read)
            elif path.is_file():
                ti.type = tarfile.REGTYPE
                ti.size = st.st_size
                ti.mode = _tar_mode(st.st_mode, is_dir=False, group_read=group_read)
            else:
                raise SandboxError("refusing to copy a special file")
            return ti

        if source.is_dir():
            if source.is_symlink():
                raise SandboxError("refusing to copy a symlink")
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


def _unpack(archive: IO[bytes], name: str, dest: Path) -> None:
    """Unpack the ``tar -c`` output of ``name`` into ``dest`` (file, or directory contents)."""

    with tarfile.open(fileobj=archive, mode="r:") as tf:
        members = tf.getmembers()
        single = next((m for m in members if m.name == name), None)
        if single is not None and single.isreg() and len(members) == 1:
            fh = tf.extractfile(single)
            if fh is None:
                raise SandboxError("copy_out: unreadable archive member")
            dest.parent.mkdir(parents=True, exist_ok=True)
            with fh, dest.open("wb") as out:
                while chunk := fh.read(64 * 1024):
                    out.write(chunk)
            return
        dest.mkdir(parents=True, exist_ok=True)
        for member in members:
            parts = PurePosixPath(member.name).parts
            if not parts or parts[0] != name:
                raise SandboxError("copy_out: unexpected archive member")
            if len(parts) == 1:
                continue
            member.name = "/".join(parts[1:])
            tf.extract(member, dest, filter="data")


class _K8sTerminal:
    """``TerminalChannel`` over a :class:`K8sTerminalStream`."""

    def __init__(self, stream: K8sTerminalStream, on_close: Callable[[], None]) -> None:
        self._stream = stream
        self._on_close = on_close
        self._closed = False

    def _check(self) -> None:
        if self._closed:
            raise SandboxGone("terminal closed")

    def read(self, max_bytes: int, timeout_s: float | None = None) -> bytes:
        self._check()
        try:
            return self._stream.read(max_bytes, timeout_s)
        except Exception as exc:
            raise _to_sandbox_error("terminal read", exc) from None

    def write(self, data: bytes) -> None:
        self._check()
        try:
            self._stream.write(data)
        except Exception as exc:
            raise _to_sandbox_error("terminal write", exc) from None

    def resize(self, rows: int, cols: int) -> None:
        self._check()
        try:
            self._stream.resize(rows, cols)
        except Exception as exc:
            raise _to_sandbox_error("terminal resize", exc) from None

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._stream.close()
        except Exception:
            _LOG.debug("terminal stream close failed", exc_info=True)
        try:
            self._on_close()
        except Exception:
            _LOG.debug("terminal shell cleanup failed", exc_info=True)


class K8sExecAdapter:
    """Exec, copy and terminal operations of one namespace, through :class:`K8sApiPort`."""

    def __init__(
        self,
        api: K8sApiPort,
        namespace: str,
        clock: Clock,
        *,
        operation_timeout: timedelta = SANDBOX_OPERATION_TIMEOUT,
        copy_timeout: timedelta = timedelta(seconds=60),
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._api = api
        self._namespace = namespace
        self._clock = clock
        self._sleep = sleep
        self._operation_timeout = operation_timeout.total_seconds()
        self._copy_timeout = copy_timeout.total_seconds()

    # ------------------------------------------------------------------ exec

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
        """Run one command; on ``timeout_s`` kill its processes and report ``timed_out``."""

        if timeout_s is not None and timeout_s <= 0:
            raise ValueError("timeout_s must be positive")
        container = container_for_user(as_user)
        marker = secrets.token_hex(16)
        # The in-container ``timeout -s KILL`` kills the command and its process group without a
        # new exec, so it works even when a fork bomb has used the pod PID limit (where a second
        # exec cannot start). The client-side limit is only the backstop. Same as the Docker
        # provider. The marker kill below then clears what left the group.
        argv = _wrap_command(command, env, cwd, marker, timeout_s)
        started = self._clock.now()
        backstop = None if timeout_s is None else timeout_s + _BACKSTOP_GRACE
        output = self._run(handle, container, argv, timeout_s=backstop)
        elapsed = (self._clock.now() - started).total_seconds()
        timed_out = timeout_s is not None and (
            output.timed_out
            or (output.exit_code == _KILLED_EXIT and elapsed >= timeout_s - _TIMEOUT_EPSILON)
        )
        if timed_out:
            self._kill_marked(handle, container, marker)
            return ExecResult(
                _KILLED_EXIT, output.stdout[:_MAX_CAPTURE], output.stderr[:_MAX_CAPTURE], True
            )
        return ExecResult(
            output.exit_code, output.stdout[:_MAX_CAPTURE], output.stderr[:_MAX_CAPTURE], False
        )

    def _run(
        self,
        handle: SandboxHandle,
        container: str,
        argv: Sequence[str],
        *,
        timeout_s: float | None,
        stdin: bytes | None = None,
    ) -> ExecOutput:
        """One exec. A start failure (the process never ran) is retried until the operation
        timeout: right after a fork bomb the pod is at its PID limit for a moment, and the
        runtime cannot start the exec process, while the in-container ``timeout`` clears it.
        A retry cannot run the command twice, because the first attempt never started."""

        deadline = self._clock.now() + timedelta(seconds=self._operation_timeout)
        delay = _START_RETRY_FIRST
        with _translate("exec"):
            while True:
                try:
                    return self._api.exec_command(
                        self._namespace,
                        handle.sandbox_id,
                        container,
                        argv,
                        timeout_s=timeout_s,
                        stdin=stdin,
                    )
                except K8sApiError as exc:
                    if (
                        not _is_exec_start_failure(exc)
                        or self._clock.now() + timedelta(seconds=delay) > deadline
                    ):
                        raise
                    _LOG.info("exec in %s could not start; retrying", handle.sandbox_id)
                self._sleep(delay)
                delay = min(delay * 2, _START_RETRY_MAX)

    def _kill_marked(self, handle: SandboxHandle, container: str, marker: str) -> None:
        """Best-effort SIGKILL of every process that carries ``marker``."""

        argv = [_BASH, "-c", _KILL_MARKED_SCRIPT, "bash", marker]
        try:
            self._run(handle, container, argv, timeout_s=self._operation_timeout)
        except (SandboxGone, ProviderUnavailable):
            return
        except SandboxError:
            _LOG.warning("post-timeout kill in %s failed", handle.sandbox_id, exc_info=True)

    # ------------------------------------------------------------------ terminal

    def attach_terminal(self, handle: SandboxHandle) -> TerminalChannel:
        """Open ``/bin/bash -l`` with a TTY in the ``learner`` container."""

        marker = secrets.token_hex(16)
        argv = _wrap_command(
            [_BASH, "-l"],
            {"TERM": "xterm-256color", "HOME": "/home/learner", "PATH": _TERMINAL_PATH},
            "/workspace",
            marker,
        )
        with _translate("attach_terminal"):
            stream = self._api.open_terminal(
                self._namespace, handle.sandbox_id, CONTAINER_LEARNER, argv
            )

        def kill_shell() -> None:
            self._kill_marked(handle, CONTAINER_LEARNER, marker)

        return _K8sTerminal(stream, kill_shell)

    # ------------------------------------------------------------------ copy

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
        becomes the file ``dest``. Under ``/grader`` the owner container extracts it.
        """

        if not source_ro.exists():
            raise FileNotFoundError(str(source_ro))
        requested = container_for_user(as_user) if as_user is not None else CONTAINER_LEARNER
        dest_path = PurePosixPath(dest)
        if not dest_path.is_absolute():
            raise SandboxError("copy destination must be an absolute path")
        into_grader = _is_grader_path(dest_path)
        container = CONTAINER_OWNER if into_grader else requested
        if source_ro.is_dir():
            target_dir, arcname = str(dest_path), None
        else:
            target_dir, arcname = str(dest_path.parent), dest_path.name

        buffer = io.BytesIO()
        _write_tar(source_ro, arcname, buffer, group_read=into_grader)
        tar_argv = [_TAR, "-x", "--no-same-owner"]
        if into_grader:
            tar_argv.append("-p")
        tar_argv += ["-C", target_dir, "-f", "-"]
        argv = [_TIMEOUT, "-s", "KILL", f"{self._copy_timeout:.0f}", *tar_argv]

        output = self._run(
            handle,
            container,
            argv,
            timeout_s=self._copy_timeout + _COPY_GRACE,
            stdin=buffer.getvalue(),
        )
        if output.timed_out or output.exit_code != 0:
            _LOG.warning(
                "copy_in to %s failed: exit=%s stderr=%s",
                dest,
                output.exit_code,
                output.stderr.decode("utf-8", "replace").strip(),
            )
            raise SandboxError(f"copy_in to {dest} failed")

    def copy_out(self, handle: SandboxHandle, source: str, dest: Path) -> None:
        """Stream ``source`` out of the sandbox with ``tar -c``.

        A file is written to the file ``dest``; a directory's contents are extracted into the
        directory ``dest``. Raises ``FileNotFoundError`` if ``source`` does not exist. ``/grader``
        is read in the ``owner`` container, everything else in ``learner``.
        """

        src = PurePosixPath(source)
        if not src.is_absolute() or not src.name:
            raise SandboxError("copy source must be an absolute path")
        container = CONTAINER_OWNER if _is_grader_path(src) else CONTAINER_LEARNER
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
        output = self._run(handle, container, argv, timeout_s=self._copy_timeout + _COPY_GRACE)
        if output.timed_out or output.exit_code != 0:
            text = output.stderr.decode("utf-8", "replace").strip()
            if "No such file" in text:
                raise FileNotFoundError(source)
            _LOG.warning(
                "copy_out from %s failed: exit=%s stderr=%s", source, output.exit_code, text
            )
            raise SandboxError(f"copy_out from {source} failed")
        _unpack(io.BytesIO(output.stdout), src.name, dest)
