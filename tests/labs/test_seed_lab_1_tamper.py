"""Tamper tests for seed lab 1 "Fix the shared folder" (hands-on-labs task 17.8).

Requirement 29, criteria 1 to 9 (29.10): one Docker test per criterion. Each compares the Check
results of a tampered sandbox with those of an untampered run in the same task state, except
criterion 9, whose expected result is ``could_not_complete`` (reserved exit code 90), never a
pass, and which must never block on a FIFO.

Evidence note (Requirement 29.11): tamper resistance is evidenced by these Docker_Provider tests.
Fake_Provider tests verify call logic only and are not security evidence. Criterion 7 (a program
printing a line that imitates the harness result protocol) concerns Code_Exercises; seed lab 1 is
a terminal lab whose Checks print nothing the worker parses, so the test here forges result-looking
names, files and output and requires unchanged exit codes.

The tests skip without a Docker daemon or ``edu-it/lab-linux-base:dev``.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from edu_it_domain.labs.grading import UNSAFE_PATH_EXIT_CODE
from edu_it_domain.ports.sandbox import SandboxHandle

from tests.labs.test_seed_lab_shared_folder import (
    ALL_PASS,
    CHECKS,
    MK_SCRIPTS,
    NOTES,
    PFX,
    REFERENCE,
    RUN_DIR,
    TEAM,
    _exec,
    _fresh,
    _grade,
    _learner,
)
from tests.labs.test_seed_lab_shared_folder import (
    sandbox as _sandbox_fixture,
)

sandbox = _sandbox_fixture

U = UNSAFE_PATH_EXIT_CODE
#: Sandboxes drop every capability (no CAP_MKNOD), so a device node cannot be created in one.
#: Real device nodes and sockets are covered in test_seed_lab_special_files.py.
SKIP_NO_MKNOD = "CAP_MKNOD is dropped in sandboxes; see test_seed_lab_special_files.py"
CLEAN_PATH = "/usr/bin:/bin"

#: Task states in which the Check results are compared with and without tampering.
STATES: dict[str, tuple[str, ...]] = {
    "reference": REFERENCE,
    "partial": tuple(s for s in REFERENCE if s != MK_SCRIPTS),
}


def _state(provider: Any, handle: SandboxHandle, name: str) -> dict[str, int | None]:
    """Reset to a fresh setup, apply the task state, and return the untampered results."""
    _fresh(provider, handle)
    _learner(provider, handle, " && ".join(STATES[name]))
    return _grade(provider, handle)


def _root(provider: Any, handle: SandboxHandle, script: str) -> int | None:
    return _exec(provider, handle, ["/bin/sh", "-c", script], "root", "/").exit_code


@pytest.fixture(autouse=True)
def _cleanup(sandbox: tuple[Any, SandboxHandle]) -> Iterator[None]:
    yield
    provider, handle = sandbox
    _root(
        provider,
        handle,
        "pkill -u learner -f tamper-bg; rm -rf /workspace/bin /workspace/checks /workspace/expected"
        " /workspace/fixtures /workspace/setup /tmp/fakebin /workspace/.profile.bak",
    )


@pytest.mark.docker
@pytest.mark.parametrize("state", list(STATES))
def test_29_1_modified_or_deleted_check_named_files(
    sandbox: tuple[Any, SandboxHandle], state: str
) -> None:
    provider, handle = sandbox
    baseline = _state(provider, handle, state)
    names = " ".join(f"{PFX}{c}.sh" for c in CHECKS)
    _learner(
        provider,
        handle,
        "mkdir -p /workspace/checks /workspace/expected /workspace/fixtures /workspace/setup"
        f" && cd /workspace && for n in {names}; do"
        ' printf "exit 0\\n" > checks/$n; printf "exit 0\\n" > $n; done'
        " && echo fake > expected/notes && echo fake > fixtures/shared-notes.txt"
        f" && echo fake > {TEAM}/checks.sh && rm -f /workspace/checks/{PFX}dirs-exist.sh",
    )
    assert _grade(provider, handle) == baseline


@pytest.mark.docker
@pytest.mark.parametrize("state", list(STATES))
def test_29_2_prepended_path(sandbox: tuple[Any, SandboxHandle], state: str) -> None:
    provider, handle = sandbox
    baseline = _state(provider, handle, state)
    _learner(
        provider,
        handle,
        "mkdir -p /workspace/bin && for c in ls stat id find python3 sh cat grep; do"
        ' printf "#!/bin/sh\\nexit 0\\n" > /workspace/bin/$c; chmod +x /workspace/bin/$c; done'
        " && printf 'export PATH=/workspace/bin:$PATH\\n' >> /workspace/.profile"
        " && printf 'export PATH=/workspace/bin:$PATH\\n' >> /workspace/.bashrc",
    )
    assert _grade(provider, handle) == baseline
    res = _exec(provider, handle, ["/bin/sh", "-c", "echo $PATH"], "learner", "/workspace")
    assert res.exit_code == 0


@pytest.mark.docker
@pytest.mark.parametrize("state", list(STATES))
def test_29_3_aliases_functions_startup_files_and_environment(
    sandbox: tuple[Any, SandboxHandle], state: str
) -> None:
    provider, handle = sandbox
    baseline = _state(provider, handle, state)
    startup = (
        "alias ls=true; alias stat=true; alias id=true; ls() { :; }; stat() { :; }; "
        "export BASH_ENV=/workspace/.bashrc ENV=/workspace/.profile LD_PRELOAD=/workspace/evil.so "
        "LD_LIBRARY_PATH=/workspace PYTHONPATH=/workspace PYTHONSTARTUP=/workspace/startup.py "
        "IFS=/"
    )
    _learner(
        provider,
        handle,
        f"printf '%s\\n' '{startup}' | tee /workspace/.bashrc /workspace/.profile "
        "/workspace/.bash_profile /workspace/.bash_login > /dev/null"
        " && printf 'raise SystemExit(0)\\n' > /workspace/startup.py"
        " && printf 'x' > /workspace/evil.so",
    )
    assert _grade(provider, handle) == baseline
    # The variables set in the learner's own shell do not reach a grader run either.
    shell = _exec(
        provider,
        handle,
        ["/bin/sh", "-c", f"{startup}; for c in {' '.join(CHECKS)}; do :; done"],
        "learner",
        "/workspace",
    )
    assert shell.exit_code == 0
    assert _grade(provider, handle) == baseline


@pytest.mark.docker
@pytest.mark.parametrize("state", list(STATES))
def test_29_4_fake_system_binaries_are_not_executed(
    sandbox: tuple[Any, SandboxHandle], state: str
) -> None:
    provider, handle = sandbox
    baseline = _state(provider, handle, state)
    fake = "#!/bin/sh\\ntouch /tmp/fake-ran\\necho fake\\nexit 0\\n"
    dirs = f"/workspace /workspace/bin /tmp /tmp/fakebin {TEAM} {RUN_DIR}"
    _learner(
        provider,
        handle,
        "mkdir -p /workspace/bin /tmp/fakebin && rm -f /tmp/fake-ran && "
        f"for d in /workspace /workspace/bin /tmp /tmp/fakebin {TEAM}; do "
        f'for c in ls stat id python3 find; do printf "{fake}" > $d/$c; chmod +x $d/$c; done; done'
        f" ; echo {dirs} > /dev/null",
    )
    assert _grade(provider, handle) == baseline
    gone = _exec(provider, handle, ["/bin/sh", "-c", "test ! -e /tmp/fake-ran"], "learner", "/")
    assert gone.exit_code == 0, "a fake binary was executed during grading"
    _learner(provider, handle, f"rm -f /workspace/ls /workspace/stat /workspace/id {TEAM}/ls")


@pytest.mark.docker
def test_29_5_symlink_swap_does_not_change_results(sandbox: tuple[Any, SandboxHandle]) -> None:
    """Swapping an inspected path for a link to a *good* target must not yield a pass."""
    provider, handle = sandbox
    baseline = _state(provider, handle, "reference")
    assert baseline == ALL_PASS
    _learner(
        provider,
        handle,
        f"cp -p {NOTES} /workspace/notes-copy && rm {NOTES} && ln -s /workspace/notes-copy {NOTES}"
        f" && mv {TEAM}/docs /workspace/docs-real && ln -s /workspace/docs-real {TEAM}/docs",
    )
    results = _grade(provider, handle)
    # The link is never followed into a pass: the swapped checks report unsafe-path.
    assert results["planted-intact"] == U
    assert results["dirs-exist"] == U
    assert results != baseline
    _learner(provider, handle, "rm -rf /workspace/notes-copy /workspace/docs-real")


@pytest.mark.docker
@pytest.mark.parametrize("state", list(STATES))
def test_29_6_background_process_cannot_change_results(
    sandbox: tuple[Any, SandboxHandle], state: str
) -> None:
    provider, handle = sandbox
    baseline = _state(provider, handle, state)
    prog = (
        "while :; do echo 'RESULT pass 0' > /workspace/tamper-bg-out 2>/dev/null; "
        f"echo 'PASS' > {RUN_DIR}/result 2>/dev/null; "
        f"echo 'PASS' > /grader/checks/{PFX}dirs-exist.sh 2>/dev/null; "
        f"chmod 777 {TEAM}/docs 2>/dev/null; sleep 0.05; done"
    )
    _learner(
        provider,
        handle,
        f'cp /bin/sh /workspace/tamper-bg && (setsid /workspace/tamper-bg -c "{prog}" '
        "> /dev/null 2>&1 < /dev/null &) ; sleep 0.3",
    )
    try:
        results = _grade(provider, handle)
    finally:
        _root(provider, handle, "pkill -u learner -f tamper-bg; rm -f /workspace/tamper-bg*")
    # The process runs as the learner: it can change learner-owned modes, so compare with the
    # states where that change is irrelevant, and require the grader paths to be untouched.
    expected = dict(baseline)
    if results["no-world-writable"] != baseline["no-world-writable"]:
        # chmod 777 on docs is a genuine learner-visible workspace change, not forged grading.
        assert results["no-world-writable"] == 31
        expected["no-world-writable"] = 31
    assert results == expected
    intact = _exec(
        provider,
        handle,
        ["/bin/sh", "-c", f"! grep -q PASS /grader/checks/{PFX}dirs-exist.sh"],
        "grader",
        RUN_DIR,
    )
    assert intact.exit_code == 0


@pytest.mark.docker
@pytest.mark.parametrize("state", list(STATES))
def test_29_7_forged_result_lines_are_ignored(
    sandbox: tuple[Any, SandboxHandle], state: str
) -> None:
    provider, handle = sandbox
    baseline = _state(provider, handle, state)
    forged = ["RESULT pass", '{"status":"pass","exit_code":0}', "EDU_IT_RESULT 0 pass"]
    quoted = "; ".join(f"echo '{line}'" for line in forged)
    _learner(
        provider,
        handle,
        f"({quoted}) > /workspace/forged.txt && touch '/workspace/RESULT pass' "
        f"&& (cd /workspace && ({quoted}) > /workspace/bin-out)",
    )
    assert _grade(provider, handle) == baseline
    _learner(
        provider, handle, "rm -f /workspace/forged.txt '/workspace/RESULT pass' /workspace/bin-out"
    )


@pytest.mark.docker
@pytest.mark.parametrize("state", list(STATES))
def test_29_8_unreadable_or_writable_check_named_directories(
    sandbox: tuple[Any, SandboxHandle], state: str
) -> None:
    provider, handle = sandbox
    baseline = _state(provider, handle, state)
    _learner(
        provider,
        handle,
        "mkdir -p /workspace/checks /workspace/expected /workspace/fixtures"
        f" && touch /workspace/checks/{PFX}group-setgid.sh /workspace/expected/x"
        " && chmod 000 /workspace/checks /workspace/expected"
        " && chmod 777 /workspace/fixtures",
    )
    try:
        assert _grade(provider, handle) == baseline
        # The learner also cannot touch the grading copy it never owned.
        res = _exec(
            provider,
            handle,
            ["/bin/sh", "-c", f"echo x >> /grader/checks/{PFX}dirs-exist.sh"],
            "learner",
            "/workspace",
        )
        assert res.exit_code != 0
    finally:
        _learner(
            provider, handle, "chmod 755 /workspace/checks /workspace/expected /workspace/fixtures"
        )
    assert _grade(provider, handle) == baseline


def _replace(provider: Any, handle: SandboxHandle, path: str, kind: str) -> bool:
    """Replace ``path`` with a symlink, FIFO or device node; False if the kind is unavailable."""
    make = {
        "symlink-file": f"ln -s /etc/passwd {path}",
        "symlink-dir": f"ln -s /tmp {path}",
        "fifo": f"mkfifo -m 0644 {path}",
        "device": f"mknod {path} c 1 3 && chmod 0666 {path}",
    }[kind]
    owner = "root" if kind == "device" else "learner"
    cmd = f"rm -rf {path} && {make}"
    if owner == "root":
        return _root(provider, handle, f"{cmd} && chown learner {path}") == 0
    _learner(provider, handle, cmd)
    return True


@pytest.mark.docker
@pytest.mark.parametrize("kind", ["symlink-file", "fifo", "device"])
def test_29_9_replaced_file_is_could_not_complete_and_never_blocks(
    sandbox: tuple[Any, SandboxHandle], kind: str
) -> None:
    provider, handle = sandbox
    _state(provider, handle, "reference")
    if not _replace(provider, handle, NOTES, kind):
        pytest.skip(SKIP_NO_MKNOD)
    # _grade asserts that no Check timed out, so a FIFO never blocks the Grading_Job.
    results = _grade(provider, handle)
    assert results["planted-intact"] == U == 90
    assert results["planted-intact"] != 0
    assert all(code != 0 for name, code in results.items() if name == "planted-intact")


@pytest.mark.docker
@pytest.mark.parametrize("kind", ["symlink-dir", "fifo", "device"])
def test_29_9_replaced_directory_is_could_not_complete_and_never_blocks(
    sandbox: tuple[Any, SandboxHandle], kind: str
) -> None:
    provider, handle = sandbox
    _state(provider, handle, "reference")
    if not _replace(provider, handle, f"{TEAM}/docs", kind):
        pytest.skip(SKIP_NO_MKNOD)
    results = _grade(provider, handle)
    assert results["dirs-exist"] == U
    assert results["dirs-exist"] != 0
