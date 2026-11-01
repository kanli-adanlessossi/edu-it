"""Property 26 for GradeRun (task 6.12).

Feature: hands-on-labs, Property 26: Grading calls never depend on learner-controlled state

EVIDENCE CLASS: this test verifies the *call logic* of ``GradeRun`` against the
``Fake_SandboxProvider`` only. It is NOT security evidence. Tamper resistance
(learner env vars, PATH, aliases, planted executables, symlinks, background
processes) is evidenced by the ``docker``-marked tamper tests that run the real
kernel, users and file permissions (Requirements 10.12, 29.11).

**Validates: Requirements 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 14.3, 14.4, 29.1, 29.2, 29.3, 29.4**
"""

from __future__ import annotations

import tempfile
from pathlib import Path, PurePosixPath

from edu_it_application.labs import GradeRun, RecordCheckResult
from edu_it_domain import config
from edu_it_domain.labs.models import GradingRun, LabSession, RunStatus, SessionStatus
from edu_it_domain.labs.sandbox_spec import build_sandbox_spec
from edu_it_domain.ports.lab_catalog import LabCheckDefinition, LabDefinition, LabTaskDefinition
from edu_it_domain.ports.sandbox import ExecResult
from edu_it_infrastructure.fakes import (
    Fake_LabCatalog,
    Fake_SandboxProvider,
    In_Memory_GradingRunRepository,
    In_Memory_LabSessionRepository,
    In_Memory_MasteryRepository,
    Test_Clock,
)
from hypothesis import given, settings
from hypothesis import strategies as st

_LEARNER_WRITABLE = ("/home", "/tmp", "/var/tmp", "/dev/shm", "/run/user")
_CLEAN_ENV = {"PATH": "/usr/bin:/bin", "LANG": "C", "HOME": "/grader/run"}
_CHECK_IDS = ("c1", "c2")

_path_chars = st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789_.-", min_size=1, max_size=8)
_names = st.one_of(
    st.sampled_from(["kill", "ps", "sh", "bash", "python3", "c1.sh", "c2.sh", "run_cases.py"]),
    _path_chars,
)
_dirs = st.sampled_from(
    [
        "/home/learner",
        "/home/learner/bin",
        "/tmp",
        "/usr/local/bin",
        "/bin",
        "/usr/bin",
        "/grader/checks",
        "/grader/run",
        "/grader",
    ]
)
_files = st.dictionaries(
    st.builds(lambda d, n: f"{d}/{n}", _dirs, _names),
    st.binary(max_size=40),
    max_size=8,
)
_processes = st.lists(
    st.tuples(st.sampled_from(["R", "S", "T"]), st.sampled_from(["bash", "kill", "sh", "evil"])),
    max_size=6,
)
_failure = st.sampled_from(["none", "check-fails", "check-raises", "check-times-out"])


def _lab() -> LabDefinition:
    return LabDefinition(
        lab_id="lab1",
        kind="terminal",
        image="img",
        concept_ids=("cn1",),
        tasks=(LabTaskDefinition("t1", required=True),),
        checks=tuple(LabCheckDefinition(c, "t1", 5.0, {"pass": "fb.ok"}) for c in _CHECK_IDS),
        bundle_path="lab1",
    )


def _run(
    root: Path, files: dict[str, bytes], procs: list[tuple[str, str]], failure: str
) -> tuple[list[tuple], list[tuple], Fake_SandboxProvider, RunStatus | None, Path]:
    """Grade one run; return (exec calls, copy_in calls, provider, status, bundle root)."""
    lab = _lab()
    (root / "lab1" / "sandbox" / "checks").mkdir(parents=True, exist_ok=True)
    for c in _CHECK_IDS:
        (root / "lab1" / "sandbox" / "checks" / f"{c}.sh").write_text("exit 0\n")
    clock = Test_Clock()
    runs = In_Memory_GradingRunRepository()
    sessions = In_Memory_LabSessionRepository(runs)
    sandbox = Fake_SandboxProvider(clock)
    handle = sandbox.provision(build_sandbox_spec(lab, "s1"))
    sandbox.add_process(handle, "learner", "bash")
    for state, command in procs:
        sandbox.add_process(handle, "learner", command, state)
    sandbox.sandbox(handle).files.update(files)  # learner-planted state
    now = clock.now()
    sessions.add_if_no_active(
        LabSession("s1", "u1", "lab1", SessionStatus.GRADING, now, now + config.LAB_SESSION_TTL)
    )
    sessions.transition(
        "s1",
        expected={SessionStatus.GRADING},
        to=SessionStatus.GRADING,
        sandbox_ref=handle.sandbox_id,
    )
    runs.add(GradingRun("r1", "s1", RunStatus.RUNNING, now, now + config.LAB_GRADING_RUN_DEADLINE))

    execs: list[tuple] = []
    copies: list[tuple] = []
    real_exec, real_copy = sandbox.exec, sandbox.copy_in

    def spy_exec(h, command, **kw):  # type: ignore[no-untyped-def]
        execs.append((tuple(command), kw["as_user"], dict(kw["env"]), kw["cwd"], kw["timeout_s"]))
        is_check = list(command)[:1] == ["/bin/sh"]
        if is_check and failure == "check-raises" and command[1].endswith("/c1.sh"):
            raise RuntimeError("provider blew up")
        if is_check and failure == "check-fails":
            return ExecResult(1, b"", b"", False)
        if is_check and failure == "check-times-out":
            return ExecResult(124, b"", b"", True)
        return real_exec(h, command, **kw)

    def spy_copy(h, source, dest, *, as_user=None):  # type: ignore[no-untyped-def]
        copies.append((source, dest, as_user))
        return real_copy(h, source, dest, as_user=as_user)

    sandbox.exec = spy_exec  # type: ignore[method-assign]
    sandbox.copy_in = spy_copy  # type: ignore[method-assign]
    job = GradeRun(
        sessions=sessions,
        runs=runs,
        catalog=Fake_LabCatalog([lab]),
        sandbox=sandbox,
        record=RecordCheckResult(runs=runs, mastery=In_Memory_MasteryRepository(), clock=clock),
        clock=clock,
        bundle_root=root,
    )
    status = job.execute("r1")
    session = sessions.get("s1")
    assert session is not None and session.status is SessionStatus.READY
    return execs, copies, sandbox, status, root


@settings(max_examples=100, deadline=None)
@given(files=_files, procs=_processes, failure=_failure)
def test_grading_calls_never_depend_on_learner_controlled_state(
    files: dict[str, bytes], procs: list[tuple[str, str]], failure: str
) -> None:
    # Feature: hands-on-labs, Property 26: Grading calls never depend on learner-controlled state
    with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
        base_execs, base_copies, _, base_status, _ = _run(Path(a), {}, [], failure)
        execs, copies, _sandbox, status, root = _run(Path(b), files, procs, failure)

    # Identical provider-call sequence to an untampered sandbox (args compared, root-relative).
    assert execs == base_execs
    assert status == base_status
    assert [(c[1], c[2]) for c in copies] == [(c[1], c[2]) for c in base_copies]

    # Bundle copied from the read-only source tree into the root-owned /grader.
    assert len(copies) == 1
    source, dest, as_user = copies[0]
    assert dest == "/grader" and as_user == "root"
    assert Path(source) == root / "lab1" / "sandbox"

    # Every exec: absolute command path, no learner-writable location, explicit cwd.
    for argv, user, env, cwd, _timeout in execs:
        assert PurePosixPath(argv[0]).is_absolute()
        assert not argv[0].startswith(_LEARNER_WRITABLE)
        assert PurePosixPath(cwd).is_absolute()
        assert env == _CLEAN_ENV
        assert user in {"root", "grader", "learner"}

    # Checks run as the Grader_User from the grader working directory.
    checks = [e for e in execs if e[0][:1] == ("/bin/sh",)]
    assert checks
    for argv, user, _env, cwd, _t in checks:
        assert user == "grader" and cwd == "/grader/run"
        assert argv[1].startswith("/grader/checks/")

    # Freeze (as learner) precedes copy_in; bundle removed (root) then thaw, even on failure.
    stop = next(i for i, e in enumerate(execs) if e[0][-3:] == ("STOP", "-1") or "STOP" in e[0])
    first_check = execs.index(checks[0])
    assert stop < first_check
    clean = [i for i, e in enumerate(execs) if e[1] == "root" and "/grader/*" in e[0][-1]]
    assert len(clean) == 1
    thaw = [i for i, e in enumerate(execs) if "CONT" in e[0]]
    assert thaw == [len(execs) - 1]
    assert clean[0] < thaw[0]
    assert execs[thaw[0]][1] == "learner"
