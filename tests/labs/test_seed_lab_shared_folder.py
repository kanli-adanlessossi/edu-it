"""Seed lab 1 "Fix the shared folder" (hands-on-labs task 17.1).

Requirements 21.1 to 21.9, 15.12, 20.13 and 29.9. The content tests read the authored JSON. The
Docker tests (``docker`` marker, skipped without a daemon or ``edu-it/lab-linux-base:dev``) run the
real setup script the way ``ProvisionSandbox`` runs it, populate ``/grader`` the way ``GradeRun``
does, and run each real Check script as the Grader_User with the clean environment: a reference
solution passes every Check, and partial or tampered solutions each fail the expected Check.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from edu_it_application.labs.provision_sandbox import setup_script_argv
from edu_it_domain.labs.grading import UNSAFE_PATH_EXIT_CODE
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

#: The exit-code mapping of the design's seed-lab table (the reserved code 90 is added below).
DESIGN_CODES: dict[str, dict[str, int]] = {
    "dirs-exist": {"pass": 0, "missing-docs": 11, "missing-scripts": 12, "not-a-directory": 13},
    "group-setgid": {"pass": 0, "wrong-group": 21, "setgid-missing": 22, "team-not-directory": 23},
    "no-world-writable": {"pass": 0, "world-writable-remains": 31},
    "planted-intact": {"pass": 0, "planted-missing": 41, "planted-changed": 42},
}


def _load(name: str) -> Any:
    return json.loads((TRACK / name).read_text(encoding="utf-8"))


def _lab() -> dict[str, Any]:
    return next(lab for lab in _load("labs.json") if lab["provenance"]["stable_id"] == LAB_ID)


def _tasks() -> list[dict[str, Any]]:
    tasks = [t for t in _load("lab_tasks.json") if t["lab_stable_id"] == LAB_ID]
    return sorted(tasks, key=lambda t: t["ordinal"])


def _checks() -> dict[str, dict[str, Any]]:
    task_ids = {t["stable_id"] for t in _tasks()}
    return {
        c["stable_id"].removeprefix(PFX): c
        for c in _load("lab_checks.json")
        if c["lab_task_stable_id"] in task_ids
    }


# -- content ------------------------------------------------------------------


def test_lab_is_an_ai_terminal_lab_on_changing_permissions() -> None:
    lab = _lab()
    assert lab["kind"] == "terminal"
    assert lab["provenance"]["origin"] == "ai"
    assert lab["lesson_stable_id"] == "lesson.changing-permissions"
    assert lab["concept_stable_ids"] == ["concept.changing-permissions"]
    assert (REPO_ROOT / lab["setup_script"]).is_file()
    for part in ("setup", "sandbox", "expected"):
        assert (REPO_ROOT / lab["check_bundle"] / part).is_dir()


def test_lab_is_reviewed_and_not_pending() -> None:
    assert LAB_ID not in _load("pending-review.json")["stable_ids"]


def test_tasks_have_progressive_bilingual_hints() -> None:
    tasks = _tasks()
    assert len(tasks) == 3
    for task in tasks:
        assert len(task["hints"]) >= 2
        assert all(h["fr"].strip() and h["en"].strip() for h in task["hints"])


def test_checks_follow_the_design_exit_codes_with_distinct_keys() -> None:
    checks = _checks()
    assert set(checks) == set(CHECKS)
    keys: list[str] = []
    for name, check in checks.items():
        codes = {o["code"]: o["exit_code"] for o in check["outcomes"]}
        assert codes == {**DESIGN_CODES[name], "unsafe-path": UNSAFE_PATH_EXIT_CODE}
        assert check["script"] == f"checks/{PFX}{name}.sh"
        script = REPO_ROOT / _lab()["check_bundle"] / "sandbox" / check["script"]
        assert b"\r" not in script.read_bytes()
        keys.extend(o["feedback_key"] for o in check["outcomes"])
    assert len(keys) == len(set(keys))


# -- Docker -------------------------------------------------------------------


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
    handle = provider.provision(build_sandbox_spec(_Img(), "pytest-seed-lab-1"))
    try:
        # Populate /grader exactly as GradeRun does (root copy, grader-owned run dir).
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
    """Copy in and run the real setup script exactly as ``ProvisionSandbox`` does."""
    setup = REPO_ROOT / _lab()["setup_script"]
    provider.copy_in(handle, setup, "/tmp/setup.sh", as_user="learner")
    result: ExecResult = provider.exec(
        handle, setup_script_argv(10), as_user="learner", env={}, cwd="/workspace", timeout_s=15
    )
    return result.exit_code


def _fresh(provider: Any, handle: SandboxHandle) -> None:
    _learner(provider, handle, "rm -rf /workspace/team /workspace/real")
    assert _setup(provider, handle) == 0


def _grade(provider: Any, handle: SandboxHandle) -> dict[str, int | None]:
    results: dict[str, int | None] = {}
    for name in CHECKS:
        argv = ["/bin/sh", f"/grader/checks/{PFX}{name}.sh"]
        res = _exec(provider, handle, argv, "grader", RUN_DIR)
        assert not res.timed_out, name
        results[name] = res.exit_code
    return results


TEAM = "/workspace/team"
NOTES = f"{TEAM}/shared-notes.txt"
MK_DOCS = f"mkdir {TEAM}/docs"
MK_SCRIPTS = f"mkdir {TEAM}/scripts"
GROUP = f"chgrp devs {TEAM}"
SETGID = f"chmod g+s {TEAM}"
UNWRITE = f"chmod o-w {NOTES}"
REFERENCE = (MK_DOCS, MK_SCRIPTS, GROUP, SETGID, UNWRITE)
ALL_PASS = dict.fromkeys(CHECKS, 0)
U = UNSAFE_PATH_EXIT_CODE


def _without(*steps: str) -> tuple[str, ...]:
    return tuple(s for s in REFERENCE if s not in steps)


#: Learner commands run after setup, and the expected exit code of each Check.
SCENARIOS: dict[str, tuple[tuple[str, ...], dict[str, int]]] = {
    "fresh-setup": (
        (),
        {"dirs-exist": 11, "group-setgid": 21, "no-world-writable": 31, "planted-intact": 0},
    ),
    "reference": (REFERENCE, ALL_PASS),
    "missing-scripts": (_without(MK_SCRIPTS), {**ALL_PASS, "dirs-exist": 12}),
    "docs-is-a-file": (
        (f"touch {TEAM}/docs", *_without(MK_DOCS)),
        {**ALL_PASS, "dirs-exist": 13},
    ),
    "wrong-group": (_without(GROUP), {**ALL_PASS, "group-setgid": 21}),
    "setgid-missing": (_without(SETGID), {**ALL_PASS, "group-setgid": 22}),
    "world-write-kept": (_without(UNWRITE), {**ALL_PASS, "no-world-writable": 31}),
    "folder-world-writable": (
        (*REFERENCE, f"chmod o+w {TEAM}"),
        {**ALL_PASS, "no-world-writable": 31},
    ),
    "notes-deleted": ((*REFERENCE, f"rm {NOTES}"), {**ALL_PASS, "planted-intact": 41}),
    "notes-changed": (
        (*REFERENCE, f"echo extra >> {NOTES}"),
        {**ALL_PASS, "planted-intact": 42},
    ),
    "notes-symlink": (
        (*REFERENCE, f"rm {NOTES}", f"ln -s /etc/passwd {NOTES}"),
        {**ALL_PASS, "planted-intact": U},
    ),
    "notes-fifo": (
        (*REFERENCE, f"rm {NOTES}", f"mkfifo -m 0644 {NOTES}"),
        {**ALL_PASS, "planted-intact": U},
    ),
    "docs-symlink": (
        (*REFERENCE, f"rmdir {TEAM}/docs", f"ln -s /tmp {TEAM}/docs"),
        {**ALL_PASS, "dirs-exist": U},
    ),
    "team-symlink": (
        (*REFERENCE, f"mv {TEAM} /workspace/real", f"ln -s real {TEAM}"),
        dict.fromkeys(CHECKS, U),
    ),
}


@pytest.mark.docker
@pytest.mark.parametrize("scenario", list(SCENARIOS))
def test_checks_grade_learner_states(sandbox: tuple[Any, SandboxHandle], scenario: str) -> None:
    provider, handle = sandbox
    steps, expected = SCENARIOS[scenario]
    _fresh(provider, handle)
    if steps:
        _learner(provider, handle, " && ".join(steps))
    assert _grade(provider, handle) == expected


@pytest.mark.docker
def test_setup_is_idempotent_and_keeps_learner_work(sandbox: tuple[Any, SandboxHandle]) -> None:
    provider, handle = sandbox
    _fresh(provider, handle)
    assert _setup(provider, handle) == 0
    assert _grade(provider, handle)["planted-intact"] == 0
    _learner(provider, handle, " && ".join(REFERENCE))
    assert _setup(provider, handle) == 0
    assert _grade(provider, handle) == ALL_PASS
    mode = _exec(provider, handle, ["/bin/stat", "-c", "%a", NOTES], "learner", "/workspace")
    assert mode.stdout.strip() == b"664"
