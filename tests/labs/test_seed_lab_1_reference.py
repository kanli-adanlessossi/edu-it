"""Seed lab 1 reference and partial-solution tests (hands-on-labs task 17.6).

Requirements 21.2, 21.3, 21.4, 21.7, 21.9. The bundle is read straight from the repository files and
copied into a Docker sandbox the way ``GradeRun`` does; nothing is synced or served. Every Check
exit code is translated to an outcome code and Feedback_Key through the authored outcome mapping,
so the tests assert the key the learner would see, not just a number.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from edu_it_application.labs.provision_sandbox import setup_script_argv
from edu_it_domain.labs.sandbox_spec import build_sandbox_spec
from edu_it_domain.ports.sandbox import ExecResult, SandboxHandle

REPO_ROOT = Path(__file__).resolve().parents[2]
TRACK = REPO_ROOT / "content" / "linux-fundamentals"
LAB_ID = "lab.fix-the-shared-folder"
PFX = "lab-check.fix-the-shared-folder."
CHECKS = ("dirs-exist", "group-setgid", "no-world-writable", "planted-intact")
IMAGE = "edu-it/lab-linux-base:dev"
CLEAN_ENV = {"PATH": "/usr/bin:/bin", "LANG": "C", "HOME": "/grader/run"}
RUN_DIR = "/grader/run"
TEAM = "/workspace/team"
NOTES = f"{TEAM}/shared-notes.txt"


def _load(name: str) -> Any:
    return json.loads((TRACK / name).read_text(encoding="utf-8"))


def _lab() -> dict[str, Any]:
    return next(x for x in _load("labs.json") if x["provenance"]["stable_id"] == LAB_ID)


def _outcomes() -> dict[str, dict[int, tuple[str, str]]]:
    """Per Check: exit code -> (outcome code, Feedback_Key), from the authored lab_checks.json."""
    task_ids = {t["stable_id"] for t in _load("lab_tasks.json") if t["lab_stable_id"] == LAB_ID}
    return {
        c["stable_id"].removeprefix(PFX): {
            o["exit_code"]: (o["code"], o["feedback_key"]) for o in c["outcomes"]
        }
        for c in _load("lab_checks.json")
        if c["lab_task_stable_id"] in task_ids
    }


def _daemon_ready() -> bool:
    try:
        import docker

        client = docker.from_env(timeout=3)
        client.ping()
        client.images.get(IMAGE)
    except Exception:
        return False
    return True


@dataclass(frozen=True)
class _Img:
    # The authored catalog name ("linux-base"); the Docker provider maps it to `IMAGE`.
    image: str = _lab()["image"]


def _exec(provider: Any, handle: SandboxHandle, argv: list[str], user: str, cwd: str) -> ExecResult:
    env = {"PATH": "/usr/bin:/bin"} if user == "learner" else CLEAN_ENV
    result: ExecResult = provider.exec(handle, argv, as_user=user, env=env, cwd=cwd, timeout_s=10)
    return result


@pytest.fixture(scope="module")
def sandbox() -> Iterator[tuple[Any, SandboxHandle]]:
    if not _daemon_ready():
        pytest.skip("no Docker daemon or lab image")
    from edu_it_infrastructure.sandbox.docker_provider import DockerSandboxProvider

    provider = DockerSandboxProvider()
    handle = provider.provision(build_sandbox_spec(_Img(), "pytest-seed-lab-1-ref"))
    try:
        bundle = REPO_ROOT / _lab()["check_bundle"]
        provider.copy_in(handle, bundle / "sandbox", "/grader", as_user="root")
        for argv, user in (
            (["/bin/chmod", "0775", "/grader"], "root"),
            (["/bin/mkdir", "-m", "0700", "--", RUN_DIR], "grader"),
            (["/bin/chmod", "0755", "/grader"], "root"),
        ):
            assert _exec(provider, handle, argv, user, "/").exit_code == 0, argv
        yield provider, handle
    finally:
        provider.destroy(handle)


def _learner(provider: Any, handle: SandboxHandle, script: str) -> None:
    res = _exec(provider, handle, ["/bin/sh", "-c", script], "learner", "/workspace")
    assert res.exit_code == 0, script


def _setup(provider: Any, handle: SandboxHandle) -> int | None:
    setup = REPO_ROOT / _lab()["setup_script"]
    provider.copy_in(handle, setup, "/tmp/setup.sh", as_user="learner")
    result: ExecResult = provider.exec(
        handle, setup_script_argv(10), as_user="learner", env={}, cwd="/workspace", timeout_s=15
    )
    return result.exit_code


def _fresh(provider: Any, handle: SandboxHandle) -> None:
    _learner(provider, handle, "rm -rf /workspace/team /workspace/real")
    assert _setup(provider, handle) == 0


def _grade(provider: Any, handle: SandboxHandle) -> dict[str, tuple[str, str]]:
    """Run every real Check and map each exit code to (outcome code, Feedback_Key)."""
    mapping = _outcomes()
    graded: dict[str, tuple[str, str]] = {}
    for name in CHECKS:
        argv = ["/bin/sh", f"/grader/checks/{PFX}{name}.sh"]
        res = _exec(provider, handle, argv, "grader", RUN_DIR)
        assert not res.timed_out, name
        assert res.exit_code in mapping[name], (name, res.exit_code)
        graded[name] = mapping[name][res.exit_code]
    return graded


def _expect(**failing: str) -> dict[str, tuple[str, str]]:
    """Expected grading: ``pass`` except the named Checks, which map to the given code."""
    mapping = _outcomes()
    expected: dict[str, tuple[str, str]] = {}
    for name in CHECKS:
        code = failing.get(name.replace("-", "_"), "pass")
        by_code = {c: (c, k) for c, k in mapping[name].values()}
        expected[name] = by_code[code]
    return expected


MK_DOCS = f"mkdir {TEAM}/docs"
MK_SCRIPTS = f"mkdir {TEAM}/scripts"
GROUP = f"chgrp devs {TEAM}"
SETGID = f"chmod g+s {TEAM}"
UNWRITE = f"chmod o-w {NOTES}"
REFERENCE = (MK_DOCS, MK_SCRIPTS, GROUP, SETGID, UNWRITE)


def _without(*steps: str) -> tuple[str, ...]:
    return tuple(s for s in REFERENCE if s not in steps)


#: Partial solutions: learner commands, then the expected failing Check and outcome code.
PARTIALS: dict[str, tuple[tuple[str, ...], dict[str, str]]] = {
    "missing-scripts": (_without(MK_SCRIPTS), {"dirs_exist": "missing-scripts"}),
    "missing-docs": (_without(MK_DOCS), {"dirs_exist": "missing-docs"}),
    "wrong-group": (_without(GROUP), {"group_setgid": "wrong-group"}),
    "setgid-missing": (_without(SETGID), {"group_setgid": "setgid-missing"}),
    "world-write-kept": (_without(UNWRITE), {"no_world_writable": "world-writable-remains"}),
}


def test_every_outcome_has_a_distinct_feedback_key() -> None:
    keys = [k for per in _outcomes().values() for _, k in per.values()]
    assert len(keys) == len(set(keys))
    assert all(k.strip() for k in keys)


@pytest.mark.docker
def test_reference_solution_passes_every_check(sandbox: tuple[Any, SandboxHandle]) -> None:
    provider, handle = sandbox
    _fresh(provider, handle)
    _learner(provider, handle, " && ".join(REFERENCE))
    graded = _grade(provider, handle)
    assert graded == _expect()
    assert all(code == "pass" for code, _ in graded.values())


@pytest.mark.docker
@pytest.mark.parametrize("partial", list(PARTIALS))
def test_partial_solution_fails_expected_check_with_expected_key(
    sandbox: tuple[Any, SandboxHandle], partial: str
) -> None:
    provider, handle = sandbox
    steps, failing = PARTIALS[partial]
    _fresh(provider, handle)
    _learner(provider, handle, " && ".join(steps))
    graded = _grade(provider, handle)
    assert graded == _expect(**failing)
    (check,) = [n for n, (code, _) in graded.items() if code != "pass"]
    assert graded[check][1] in {k for _, k in _outcomes()[check].values()}
    assert graded[check][0] == next(iter(failing.values()))


@pytest.mark.docker
def test_setup_script_run_twice_is_idempotent(sandbox: tuple[Any, SandboxHandle]) -> None:
    provider, handle = sandbox
    _fresh(provider, handle)
    snapshot = ["/bin/sh", "-c", f"find {TEAM} -exec stat -c '%n %a %U:%G %s' {{}} + | sort"]
    first = _exec(provider, handle, snapshot, "learner", "/workspace")
    assert _setup(provider, handle) == 0
    second = _exec(provider, handle, snapshot, "learner", "/workspace")
    assert first.exit_code == 0 and first.stdout == second.stdout
    assert _grade(provider, handle) == _expect(
        dirs_exist="missing-docs",
        group_setgid="wrong-group",
        no_world_writable="world-writable-remains",
    )
