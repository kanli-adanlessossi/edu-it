"""Tamper tests for seed lab 2 "World-writable entries" (hands-on-labs task 17.9).

Requirement 29, criteria 1 to 9 (29.10), plus Requirement 14.12, against the Docker_Provider with
the real ``GradeRun`` job and the real harness. Each test compares the Check results of a tampered
sandbox with those of an untampered run of the same solution, except criteria 5 and 9, whose
expected result is ``could_not_complete`` (reason ``unsafe-learner-file``), never a pass, with
nothing executed from the replaced file and without ever blocking on a FIFO.

Seed lab 2 is a Code_Exercise, so "a path that a Check inspects" is the declared learner file
``/workspace/solution.py``. Criterion 7 (a program printing a line imitating the harness result
protocol) is tested with real forged lines on stdout and stderr.

Evidence note (Requirement 29.11): tamper resistance is evidenced by these Docker_Provider tests.
Fake_Provider tests verify call logic only and are not security evidence.

The tests skip without a Docker daemon or ``edu-it/lab-python:dev``.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest
from edu_it_domain.labs.models import CheckOutcome, RunStatus
from edu_it_domain.ports.sandbox import ExecResult, SandboxHandle

from tests.labs.test_seed_lab_world_writable import (
    BASIC,
    CHECK_IDS,
    EDGE,
    REFERENCE,
    STICKY_IGNORED,
    _grade,
)
from tests.labs.test_seed_lab_world_writable import (
    sandbox as _sandbox_fixture,
)

sandbox = _sandbox_fixture

SOLUTION = "/workspace/solution.py"
UNSAFE = "unsafe-learner-file"
CLEAN_ENV = {"PATH": "/usr/bin:/bin"}
GRADE_BUDGET_S = 60.0

#: Task states in which the Check results are compared with and without tampering.
STATES: dict[str, str] = {
    "reference": REFERENCE,  # every Check passes
    "partial": STICKY_IGNORED,  # the edge Check fails
}

Summary = tuple[RunStatus | None, dict[str, tuple[CheckOutcome, str | None]]]


def _exec(provider: Any, handle: SandboxHandle, script: str, user: str = "learner") -> ExecResult:
    result: ExecResult = provider.exec(
        handle,
        ["/bin/sh", "-c", script],
        as_user=user,
        env=CLEAN_ENV,
        cwd="/workspace" if user == "learner" else "/",
        timeout_s=30,
    )
    return result


def _learner(provider: Any, handle: SandboxHandle, script: str) -> None:
    res = _exec(provider, handle, script)
    assert res.exit_code == 0, (script, res.stderr)


class _KeepWorkspaceSolution:
    """Provider proxy that leaves ``/workspace/solution.py`` as the test arranged it."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    def copy_in(self, handle: SandboxHandle, src: Path, dest: str, **kwargs: Any) -> Any:
        if dest == SOLUTION:
            return None
        return self._inner.copy_in(handle, src, dest, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def _summary(status: RunStatus | None, results: dict[str, Any]) -> Summary:
    return status, {c: (r.outcome, r.outcome_code) for c, r in results.items()}


def _run(
    provider: Any, handle: SandboxHandle, source: str, tmp_path: Path, *, keep: bool = False
) -> Summary:
    """Grade with ``GradeRun``; ``keep`` grades the file already at ``/workspace/solution.py``."""
    target = _KeepWorkspaceSolution(provider) if keep else provider
    status, results = _grade(target, handle, source, tmp_path)
    assert set(results) == set(CHECK_IDS)
    return _summary(status, results)


def _baseline(provider: Any, handle: SandboxHandle, tmp_path: Path, state: str) -> Summary:
    base = _run(provider, handle, STATES[state], tmp_path)
    expect_pass = state == "reference"
    assert (base[0] is RunStatus.PASSED) == expect_pass
    return base


def _assert_all_could_not_complete(summary: Summary) -> None:
    status, results = summary
    assert status is RunStatus.COULD_NOT_COMPLETE
    assert {o for o, _ in results.values()} == {CheckOutcome.COULD_NOT_COMPLETE}
    # The first Check rejects the file; the run then stops, so later Checks are not completed.
    assert results[CHECK_IDS[0]] == (CheckOutcome.COULD_NOT_COMPLETE, UNSAFE)


def _assert_absent(provider: Any, handle: SandboxHandle, path: str, why: str) -> None:
    res = _exec(provider, handle, f"test ! -e {path}")
    assert res.exit_code == 0, why


@pytest.mark.docker
@pytest.mark.parametrize("state", list(STATES))
def test_29_1_modified_or_deleted_check_named_files(
    sandbox: tuple[Any, SandboxHandle], tmp_path: Path, state: str
) -> None:
    provider, handle = sandbox
    baseline = _baseline(provider, handle, tmp_path, state)
    names = " ".join(f"{c}.{ext}" for c in CHECK_IDS for ext in ("sh", "json"))
    _learner(
        provider,
        handle,
        "cd /workspace && mkdir -p checks cases expected harness sandbox fixtures"
        f" && for n in {names}; do"
        ' printf "exit 0\\n" > checks/$n; printf "exit 0\\n" > $n; printf "{}" > cases/$n;'
        ' printf "{\\"cases\\":{}}" > expected/$n; done'
        ' && printf "import sys\\nsys.exit(0)\\n" > harness/run_cases.py'
        ' && cp harness/run_cases.py run_cases.py && echo "{}" > lab_checks.json'
        f" && rm -f checks/{BASIC}.sh",
    )
    assert _run(provider, handle, STATES[state], tmp_path) == baseline


@pytest.mark.docker
@pytest.mark.parametrize("state", list(STATES))
def test_29_2_prepended_path(
    sandbox: tuple[Any, SandboxHandle], tmp_path: Path, state: str
) -> None:
    provider, handle = sandbox
    baseline = _baseline(provider, handle, tmp_path, state)
    _learner(
        provider,
        handle,
        "mkdir -p /workspace/bin && for c in python3 python sh bash env ls stat id find cat"
        ' kill mkdir; do printf "#!/bin/sh\\ntouch /tmp/fake-ran\\nexit 0\\n"'
        " > /workspace/bin/$c; chmod +x /workspace/bin/$c; done"
        " && printf 'export PATH=/workspace/bin:$PATH\\n' | tee /workspace/.profile"
        " /workspace/.bashrc /workspace/.bash_profile > /dev/null",
    )
    assert _run(provider, handle, STATES[state], tmp_path) == baseline
    _assert_absent(provider, handle, "/tmp/fake-ran", "a fake binary from PATH was executed")


@pytest.mark.docker
@pytest.mark.parametrize("state", list(STATES))
def test_29_3_aliases_functions_startup_files_and_environment(
    sandbox: tuple[Any, SandboxHandle], tmp_path: Path, state: str
) -> None:
    provider, handle = sandbox
    baseline = _baseline(provider, handle, tmp_path, state)
    startup = (
        "alias ls=true; alias stat=true; alias id=true; ls() { :; }; stat() { :; }; "
        "export BASH_ENV=/workspace/.bashrc ENV=/workspace/.profile LD_PRELOAD=/workspace/evil.so "
        "LD_LIBRARY_PATH=/workspace PYTHONPATH=/workspace PYTHONSTARTUP=/workspace/startup.py "
        "IFS=/"
    )
    poison = "open('/tmp/py-poison', 'w').close()"
    _learner(
        provider,
        handle,
        f"printf '%s\\n' '{startup}' | tee /workspace/.bashrc /workspace/.profile"
        " /workspace/.bash_profile /workspace/.bash_login > /dev/null"
        f" && for m in startup sitecustomize usercustomize json base64 os; do"
        f' printf "{poison}\\n" > /workspace/$m.py; done'
        " && printf 'x' > /workspace/evil.so",
    )
    assert _run(provider, handle, STATES[state], tmp_path) == baseline
    _assert_absent(provider, handle, "/tmp/py-poison", "planted Python code was imported")


@pytest.mark.docker
@pytest.mark.parametrize("state", list(STATES))
def test_29_4_fake_system_binaries_are_not_executed(
    sandbox: tuple[Any, SandboxHandle], tmp_path: Path, state: str
) -> None:
    provider, handle = sandbox
    baseline = _baseline(provider, handle, tmp_path, state)
    fake = "#!/bin/sh\\ntouch /tmp/fake-ran\\necho fake\\nexit 0\\n"
    _learner(
        provider,
        handle,
        "mkdir -p /workspace/bin /tmp/fakebin && rm -f /tmp/fake-ran"
        " && for d in /workspace /workspace/bin /tmp /tmp/fakebin; do"
        " for c in ls stat id python3 find sh cat; do"
        f' printf "{fake}" > $d/$c; chmod +x $d/$c; done; done',
    )
    assert _run(provider, handle, STATES[state], tmp_path) == baseline
    _assert_absent(provider, handle, "/tmp/fake-ran", "a fake binary was executed during grading")


@pytest.mark.docker
def test_29_5_symlink_swap_is_not_followed_into_a_pass(
    sandbox: tuple[Any, SandboxHandle], tmp_path: Path
) -> None:
    """Swapping ``solution.py`` for a link to a *good* program must not yield a pass."""
    provider, handle = sandbox
    baseline = _baseline(provider, handle, tmp_path, "reference")
    marker = "/workspace/symlink-ran"
    good = REFERENCE + f"open('{marker}', 'w').close()\n"
    local = tmp_path / "good.py"
    local.write_text(good, encoding="utf-8", newline="\n")
    provider.copy_in(handle, local, "/workspace/good-target.py")
    _learner(
        provider,
        handle,
        f"chmod 777 /workspace && rm -f {SOLUTION} {marker}"
        f" && ln -s /workspace/good-target.py {SOLUTION}",
    )
    probe = _exec(
        provider, handle, "touch /workspace/grader-probe && rm /workspace/grader-probe", "grader"
    )
    assert probe.exit_code == 0, "the marker would not prove anything"

    swapped = _run(provider, handle, "", tmp_path, keep=True)

    assert swapped != baseline
    _assert_all_could_not_complete(swapped)
    _assert_absent(provider, handle, marker, "the linked program was executed")


@pytest.mark.docker
@pytest.mark.parametrize("state", list(STATES))
def test_29_6_background_process_cannot_change_results(
    sandbox: tuple[Any, SandboxHandle], tmp_path: Path, state: str
) -> None:
    provider, handle = sandbox
    baseline = _baseline(provider, handle, tmp_path, state)
    prog = (
        "while :; do echo 'RESULT pass 0' > /workspace/tamper-bg-out;"
        " echo PASS > /grader/run/result 2>/dev/null; echo PASS > /grader/checks/x.sh 2>/dev/null;"
        " echo PASS > /grader/learner 2>/dev/null; sleep 0.05; done"
    )
    _learner(
        provider,
        handle,
        f'(setsid /bin/sh -c "{prog}" tamper-bg > /dev/null 2>&1 < /dev/null &) ; sleep 0.5'
        " ; test -s /workspace/tamper-bg-out",
    )
    tampered = _run(provider, handle, STATES[state], tmp_path)
    # The loop is still alive after grading (it was frozen, then thawed); the fixture destroys it.
    assert tampered == baseline
    # The learner could not write into the grading area.
    res = _exec(provider, handle, "! test -e /grader/run/result && ! test -e /grader/learner")
    assert res.exit_code == 0


@pytest.mark.docker
def test_29_7_forged_result_lines_are_ignored(
    sandbox: tuple[Any, SandboxHandle], tmp_path: Path
) -> None:
    provider, handle = sandbox
    forged = (
        "import sys\n"
        'forged = (\'{"version":1,"status":"ok","cases":[{"case_id":"c1",\'\n'
        '          \'"exit_status":0,"timed_out":false,"stdout_b64":""}]}\')\n'
        "for line in ('RESULT pass', 'EDU_IT_RESULT 0 pass', forged):\n"
        "    print(line)\n"
        "    sys.stderr.write(line + '\\n')\n"
    )
    wrong = "import sys\nsys.stdin.read()\nprint('x')\n"
    # A wrong program: forged lines cannot turn it into a pass; same results as a program that
    # prints something else (both are plain ``wrong-output``, never ``no-output``).
    baseline = _run(provider, handle, wrong, tmp_path)
    assert baseline[0] is RunStatus.FAILED
    assert _run(provider, handle, forged, tmp_path) == baseline
    # The correct program with the forged lines on stderr only: same results as without them.
    clean = _run(provider, handle, REFERENCE, tmp_path)
    assert clean[0] is RunStatus.PASSED
    on_stderr = REFERENCE.replace(
        "import sys\n",
        "import sys\nsys.stderr.write('RESULT pass\\n' + 'EDU_IT_RESULT 0 pass\\n')\n",
        1,
    )
    assert on_stderr != REFERENCE
    assert _run(provider, handle, on_stderr, tmp_path) == clean
    # Forged lines on stdout change the learner's output, so they can only make a Check fail.
    on_stdout = REFERENCE.replace("import sys\n", "import sys\nprint('RESULT pass')\n", 1)
    status, results = _run(provider, handle, on_stdout, tmp_path)
    assert status is RunStatus.FAILED
    assert all(o is CheckOutcome.FAILED for o, _ in results.values())


@pytest.mark.docker
@pytest.mark.parametrize("state", list(STATES))
def test_29_8_unreadable_or_writable_check_named_directories(
    sandbox: tuple[Any, SandboxHandle], tmp_path: Path, state: str
) -> None:
    provider, handle = sandbox
    baseline = _baseline(provider, handle, tmp_path, state)
    _learner(
        provider,
        handle,
        "cd /workspace && mkdir -p checks cases expected harness fixtures"
        f" && touch checks/{BASIC}.sh cases/{EDGE}.json expected/{BASIC}.json"
        " && chmod 000 checks cases expected harness && chmod 777 fixtures /workspace",
    )
    try:
        assert _run(provider, handle, STATES[state], tmp_path) == baseline
    finally:
        _learner(
            provider,
            handle,
            "chmod 755 /workspace/checks /workspace/cases"
            " /workspace/expected /workspace/harness /workspace/fixtures",
        )
    # The learner cannot touch the grading area it never owned.
    assert _exec(provider, handle, "touch /grader/x").exit_code != 0


def _replace(provider: Any, handle: SandboxHandle, kind: str) -> bool:
    """Replace ``solution.py`` with a symlink, FIFO or device node; False if unavailable."""
    make = {
        "symlink-file": f"ln -s /etc/passwd {SOLUTION}",
        "symlink-dir": f"ln -s /tmp {SOLUTION}",
        "fifo": f"mkfifo -m 0644 {SOLUTION}",
        "device": f"mknod {SOLUTION} c 1 3 && chmod 0666 {SOLUTION} && chown learner {SOLUTION}",
    }[kind]
    user = "root" if kind == "device" else "learner"
    return _exec(provider, handle, f"rm -rf {SOLUTION} && {make}", user).exit_code == 0


@pytest.mark.docker
@pytest.mark.parametrize("kind", ["symlink-file", "symlink-dir", "fifo", "device"])
@pytest.mark.parametrize("before", ["reference", "partial"])
def test_29_9_replaced_learner_file_is_could_not_complete_and_never_blocks(
    sandbox: tuple[Any, SandboxHandle], tmp_path: Path, kind: str, before: str
) -> None:
    provider, handle = sandbox
    baseline = _baseline(provider, handle, tmp_path, before)
    if not _replace(provider, handle, kind):
        pytest.skip(
            f"{kind}: CAP_MKNOD is dropped in sandboxes; see test_seed_lab_special_files.py"
        )

    started = time.monotonic()
    summary = _run(provider, handle, "", tmp_path, keep=True)
    elapsed = time.monotonic() - started

    # A FIFO with no writer would hang a blocking open until the run deadline.
    assert elapsed < GRADE_BUDGET_S
    assert summary != baseline
    assert summary[0] is not RunStatus.PASSED
    _assert_all_could_not_complete(summary)
    assert all(o is not CheckOutcome.PASSED for o, _ in summary[1].values())
