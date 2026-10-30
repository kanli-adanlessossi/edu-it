"""In-sandbox code-exercise harness (Requirements 14.3, 14.4, 14.5, 14.10, 14.11, 29.7, 29.9).

Runs as the Grader_User, started by the worker as::

    /usr/bin/python3 -I /grader/harness/run_cases.py <check_id>

It performs three steps and prints exactly one JSON document on stdout:

1. **Intake.** ``/workspace`` is opened once with ``O_DIRECTORY | O_NOFOLLOW``. Each declared flat
   learner file is opened relative to that descriptor with ``O_RDONLY | O_NOFOLLOW | O_NONBLOCK``
   (a symlink fails with ``ELOOP``, a FIFO never blocks), the descriptor is ``fstat``-ed, only
   ``S_ISREG`` files within ``MAX_FILE_BYTES`` are accepted, and the bytes are read from that same
   descriptor. Nothing is written until every file has passed; then the bytes are written into the
   grader-owned ``/grader/run/learner/`` (mode 0700). Any symlink, FIFO, device node, socket,
   directory, missing or oversized file gives status ``unsafe-learner-file`` and executes nothing.
2. **Execution.** Only the copy is executed, per case, as a hardened subprocess: new session, closed
   file descriptors, clean environment, ``RLIMIT_CPU``, ``RLIMIT_AS`` (128 MiB), ``RLIMIT_NPROC``
   (32), ``RLIMIT_FSIZE``, stdin from a grader-owned file, stdout and stderr to grader-owned files.
   The harness is itself non-dumpable (``PR_SET_DUMPABLE=0``) and reads only ``/grader`` and the
   copies.
3. **Report.** One JSON object (schema version 1) matching ``parse_harness_report`` in
   ``edu_it_domain.labs.grading``. The learner's output only ever appears as base64 data, so text
   imitating a result line is just data. A case killed by a signal (resource limits) or by the wall
   deadline has ``exit_status`` null, which the worker maps to ``could_not_complete``.

Case inputs live in the sandbox part of the bundle, ``/grader/cases/<check_id>.json``; expected
values never enter the Sandbox (they are read by the worker from ``expected/<check_id>.json``)::

    {"learner_files": ["solution.py"], "entrypoint": "solution.py",
     "case_timeout_s": 2, "cases": [{"case_id": "c1", "stdin_b64": "..."}]}

Argv forms used here are the ones recorded in ``lab-images/README.md`` (task 3.4); the harness runs
no external command, only ``/usr/bin/python3`` for the learner program.
"""

from __future__ import annotations

import base64
import ctypes
import json
import os
import re
import resource
import signal
import stat
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

REPORT_VERSION: Final = 1  # HARNESS_REPORT_VERSION in edu_it_domain.labs.grading

#: Mirrors ``LAB_FILE_MAX_BYTES`` (64 KiB) in ``edu_it_domain.config``; the harness cannot import it.
MAX_FILE_BYTES: Final = 64 * 1024
MAX_FILES: Final = 10  # LAB_FILE_MAX_COUNT

GRADER_DIR: Final = "/grader"
WORKSPACE_DIR: Final = "/workspace"
PYTHON: Final = "/usr/bin/python3"

AS_LIMIT_BYTES: Final = 128 * 1024 * 1024
NPROC_LIMIT: Final = 32
FSIZE_LIMIT_BYTES: Final = 1024 * 1024  # also the cap on captured stdout / stderr per case
DEFAULT_CASE_TIMEOUT_S: Final = 2.0
MAX_CASE_TIMEOUT_S: Final = 10.0
MAX_CASES: Final = 200

CLEAN_ENV: Final[Mapping[str, str]] = {"PATH": "/usr/bin:/bin", "LANG": "C", "PYTHONIOENCODING": "utf-8"}

# Check ids are catalog stable ids such as ``lab-check.world-writable-entries.basic-entries``, so
# dots are allowed; no slash or backslash can occur, so the id stays a flat file name.
_CHECK_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}\Z")
_CASE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")

STATUS_OK: Final = "ok"
STATUS_UNSAFE: Final = "unsafe-learner-file"


class UnsafeLearnerFile(Exception):
    """A declared learner file is not a plain regular file within the cap.

    Carries no path or detail on purpose; the report only says ``unsafe-learner-file``.
    """


class ConfigError(Exception):
    """The bundle's case file is missing or invalid (a bundle bug, not a learner fault)."""


@dataclass(frozen=True, slots=True)
class CaseSpec:
    """One case: id and the bytes fed to the learner program's stdin."""

    case_id: str
    stdin: bytes


@dataclass(frozen=True, slots=True)
class CheckSpec:
    """Parsed ``/grader/cases/<check_id>.json``."""

    learner_files: tuple[str, ...]
    entrypoint: str
    case_timeout_s: float
    cases: tuple[CaseSpec, ...]


@dataclass(frozen=True, slots=True)
class CaseOutcome:
    """Observation of one case; ``exit_status`` is ``None`` after a signal death or timeout."""

    exit_status: int | None
    timed_out: bool
    stdout: bytes
    stderr: bytes


type Runner = Callable[[str, bytes, float, str], CaseOutcome]
"""``runner(path_of_copy, stdin, timeout_s, work_dir) -> CaseOutcome``."""


# --- Hardening ------------------------------------------------------------------


def set_non_dumpable() -> None:
    """``prctl(PR_SET_DUMPABLE, 0)``: a same-user child cannot ptrace or read harness memory."""
    pr_set_dumpable = 4
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(pr_set_dumpable, 0, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "prctl(PR_SET_DUMPABLE) failed")


# --- Intake ---------------------------------------------------------------------


def _valid_flat_name(name: object) -> bool:
    return (
        isinstance(name, str)
        and 0 < len(name.encode("utf-8", "surrogatepass")) <= 255
        and name not in {".", ".."}
        and "/" not in name
        and "\\" not in name
        and "\x00" not in name
    )


def _read_regular(dir_fd: int, name: str, max_bytes: int) -> bytes:
    """Open ``name`` under ``dir_fd`` without following links or blocking; read one descriptor."""
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(name, flags, dir_fd=dir_fd)
    except OSError as exc:  # ELOOP (symlink), ENOENT, ENXIO (socket), EACCES, ...
        raise UnsafeLearnerFile from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > max_bytes:
            raise UnsafeLearnerFile
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(fd, min(65536, max_bytes + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:  # grew after fstat
                raise UnsafeLearnerFile
            chunks.append(chunk)
        return b"".join(chunks)
    except OSError as exc:
        raise UnsafeLearnerFile from exc
    finally:
        os.close(fd)


def read_learner_files(
    workspace: str, names: Sequence[str], max_bytes: int = MAX_FILE_BYTES
) -> dict[str, bytes]:
    """Read every declared file safely, or raise :class:`UnsafeLearnerFile` for the first bad one."""
    if not names or len(names) > MAX_FILES or len(set(names)) != len(names):
        raise UnsafeLearnerFile
    if not all(_valid_flat_name(n) for n in names):
        raise UnsafeLearnerFile
    try:
        ws_fd = os.open(
            workspace, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        )
    except OSError as exc:
        raise UnsafeLearnerFile from exc
    try:
        return {name: _read_regular(ws_fd, name, max_bytes) for name in names}
    finally:
        os.close(ws_fd)


def write_copies(dest: str, files: Mapping[str, bytes]) -> dict[str, str]:
    """Write the already validated bytes into the grader-owned ``dest`` (0700); return copy paths."""
    os.makedirs(os.path.dirname(dest), mode=0o700, exist_ok=True)
    try:
        os.mkdir(dest, 0o700)
    except FileExistsError:
        info = os.lstat(dest)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
            raise ConfigError("learner copy directory is not grader-owned") from None
        os.chmod(dest, 0o700)
    dest_fd = os.open(dest, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    paths: dict[str, str] = {}
    try:
        for name, data in files.items():
            fd = os.open(
                name,
                os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW,
                0o600,
                dir_fd=dest_fd,
            )
            try:
                view = memoryview(data)
                while view:
                    view = view[os.write(fd, view) :]
            finally:
                os.close(fd)
            paths[name] = os.path.join(dest, name)
    finally:
        os.close(dest_fd)
    return paths


# --- Execution ------------------------------------------------------------------


def _child_limits(cpu_seconds: int) -> Callable[[], None]:
    def apply() -> None:
        os.umask(0o077)
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 1))
        resource.setrlimit(resource.RLIMIT_AS, (AS_LIMIT_BYTES, AS_LIMIT_BYTES))
        resource.setrlimit(resource.RLIMIT_NPROC, (NPROC_LIMIT, NPROC_LIMIT))
        resource.setrlimit(resource.RLIMIT_FSIZE, (FSIZE_LIMIT_BYTES, FSIZE_LIMIT_BYTES))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

    return apply


def _read_capped(path: str) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        return os.read(fd, FSIZE_LIMIT_BYTES)
    finally:
        os.close(fd)


def _reset_file(path: str, data: bytes = b"") -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    try:
        view = memoryview(data)
        while view:
            view = view[os.write(fd, view) :]
    finally:
        os.close(fd)


def run_program(path: str, stdin: bytes, timeout_s: float, work_dir: str) -> CaseOutcome:
    """Run the learner program copy once as a hardened subprocess and observe it."""
    in_path = os.path.join(work_dir, "stdin")
    out_path = os.path.join(work_dir, "stdout")
    err_path = os.path.join(work_dir, "stderr")
    _reset_file(in_path, stdin)
    _reset_file(out_path)
    _reset_file(err_path)
    cpu_seconds = max(1, int(timeout_s) + 1)
    in_fd = os.open(in_path, os.O_RDONLY | os.O_NOFOLLOW)
    out_fd = os.open(out_path, os.O_WRONLY | os.O_NOFOLLOW)
    err_fd = os.open(err_path, os.O_WRONLY | os.O_NOFOLLOW)
    timed_out = False
    try:
        proc = subprocess.Popen(  # noqa: S603 - fixed absolute interpreter, argv list, no shell
            [PYTHON, "-I", "-B", path],
            executable=PYTHON,
            stdin=in_fd,
            stdout=out_fd,
            stderr=err_fd,
            close_fds=True,
            env=dict(CLEAN_ENV),
            cwd=work_dir,
            start_new_session=True,
            preexec_fn=_child_limits(cpu_seconds),  # noqa: PLW1509 - single-threaded harness
        )
    finally:
        os.close(in_fd)
        os.close(out_fd)
        os.close(err_fd)
    try:
        proc.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        timed_out = True
    finally:
        # The session leader is gone or about to be; remove every process it started.
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except PermissionError:
            pass
        proc.wait()
    code = proc.returncode
    exit_status = None if timed_out or code is None or code < 0 else code
    return CaseOutcome(exit_status, timed_out, _read_capped(out_path), _read_capped(err_path))


# --- Case file and report -------------------------------------------------------


def load_check_spec(grader_dir: str, check_id: str) -> CheckSpec:
    """Load and validate ``<grader_dir>/cases/<check_id>.json`` (bundle-controlled, trusted shape)."""
    if not _CHECK_ID.match(check_id):
        raise ConfigError("invalid check id")
    try:
        with open(os.path.join(grader_dir, "cases", f"{check_id}.json"), "rb") as fh:
            doc: Any = json.loads(fh.read(1024 * 1024))
        files = tuple(doc["learner_files"])
        entry = doc["entrypoint"]
        timeout = float(doc.get("case_timeout_s", DEFAULT_CASE_TIMEOUT_S))
        raw_cases = doc["cases"]
        if entry not in files or not 0 < timeout <= MAX_CASE_TIMEOUT_S:
            raise ConfigError("bad entrypoint or timeout")
        if not isinstance(raw_cases, list) or not 0 < len(raw_cases) <= MAX_CASES:
            raise ConfigError("bad case list")
        cases = []
        for raw in raw_cases:
            case_id = raw["case_id"]
            if not isinstance(case_id, str) or not _CASE_ID.match(case_id):
                raise ConfigError("bad case id")
            cases.append(
                CaseSpec(case_id, base64.b64decode(raw.get("stdin_b64", ""), validate=True))
            )
        if len({c.case_id for c in cases}) != len(cases):
            raise ConfigError("duplicate case id")
    except ConfigError:
        raise
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ConfigError("unreadable case file") from exc
    return CheckSpec(files, entry, timeout, tuple(cases))


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def build_report(check_id: str, status: str, cases: Sequence[tuple[str, CaseOutcome]]) -> str:
    """Serialise the one report; keys match ``parse_harness_report`` exactly."""
    return json.dumps(
        {
            "version": REPORT_VERSION,
            "check_id": check_id,
            "status": status,
            "cases": [
                {
                    "case_id": case_id,
                    "exit_status": outcome.exit_status,
                    "timed_out": outcome.timed_out,
                    "stdout_b64": _b64(outcome.stdout),
                    "stderr_b64": _b64(outcome.stderr),
                }
                for case_id, outcome in cases
            ],
        },
        separators=(",", ":"),
    )


def run_check(
    check_id: str,
    *,
    grader_dir: str = GRADER_DIR,
    workspace: str = WORKSPACE_DIR,
    runner: Runner = run_program,
) -> str:
    """Intake, execute every case against the copy, and return the report text."""
    spec = load_check_spec(grader_dir, check_id)
    run_dir = os.path.join(grader_dir, "run")
    try:
        files = read_learner_files(workspace, spec.learner_files)
    except UnsafeLearnerFile:
        return build_report(check_id, STATUS_UNSAFE, [])
    copies = write_copies(os.path.join(run_dir, "learner"), files)
    work_dir = os.path.join(run_dir, "work")
    os.makedirs(work_dir, mode=0o700, exist_ok=True)
    target = copies[spec.entrypoint]  # always the grader-owned copy, never the workspace path
    observed = [
        (case.case_id, runner(target, case.stdin, spec.case_timeout_s, work_dir))
        for case in spec.cases
    ]
    return build_report(check_id, STATUS_OK, observed)


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point: ``run_cases.py <check_id>``. Prints one JSON report; exits 2 on bundle errors."""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        return 2
    try:
        set_non_dumpable()
        os.umask(0o077)
        report = run_check(args[0])
    except (ConfigError, OSError):
        return 2  # no report: the worker records missing-report -> could_not_complete
    sys.stdout.write(report + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
