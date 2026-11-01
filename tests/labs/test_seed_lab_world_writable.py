"""Seed lab 2 "World-writable entries" (hands-on-labs task 17.7).

Requirements 22.7, 22.8 and 22.9. The reference solution passes every Check and each wrong solution
(all ``w`` bits, symlinks reported, directories ignored, sticky directories ignored) fails the
expected Check(s) with the Feedback_Key of the harness outcome it produces. Bundles are loaded
directly from the repository files; nothing is synced or served.

What "distinct Feedback_Key" means here. Requirement 22.9 asks that each failing *outcome* maps to
its own translated key that does not include the expected output. The outcomes are ``wrong-output``,
``crashed`` and ``no-output`` (design.md, "Seed lab 2"), and each Check owns one key per outcome:
the tests prove the keys are pairwise distinct and answer-free, and that a crashing solution and a
silent solution land on their own keys. Requirement 22.8 only asks that every wrong solution
"fails the expected Check"; it does not ask for a separate key per wrong solution, and a
per-mistake key would have to name the mistake, which learner-facing text must not do. The four
wrong solutions are nevertheless distinguishable by the exact set of (Check, key) pairs they fail,
which is asserted below: all-w-bits fails both Checks with ``wrong-output``, symlinks-reported fails
only the basic Check, directories-ignored prints nothing where names are expected (``no-output``)
in both Checks, and sticky-directories-ignored does so in the edge Check only.

Two layers:

* The unmarked tests run each solution on the host interpreter against the authored case and
  expected files. They need no Docker and prove that the solutions below behave as labelled.
* The ``docker`` tests (skipped without a daemon or ``edu-it/lab-python:dev``) put each solution in
  a real Sandbox and grade it with the real ``GradeRun`` job and the real harness, as the
  Grader_User, with the authored cases and expected values.
"""

from __future__ import annotations

import base64
import json
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from edu_it_application.labs import GradeRun, RecordCheckResult
from edu_it_domain import config as cfg
from edu_it_domain.labs.models import (
    CheckOutcome,
    GradingRun,
    LabSession,
    RunStatus,
    SessionStatus,
)
from edu_it_domain.labs.sandbox_spec import build_sandbox_spec
from edu_it_domain.ports.lab_catalog import (
    LabCheckDefinition,
    LabDefinition,
    LabTaskDefinition,
)
from edu_it_domain.ports.sandbox import SandboxHandle
from edu_it_infrastructure.clock import System_Clock
from edu_it_infrastructure.fakes import (
    Fake_LabCatalog,
    In_Memory_GradingRunRepository,
    In_Memory_LabSessionRepository,
    In_Memory_MasteryRepository,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
TRACK = REPO_ROOT / "content" / "linux-fundamentals"
BUNDLES = TRACK / "lab-bundles"
BUNDLE_NAME = "world-writable-entries"
BUNDLE = BUNDLES / BUNDLE_NAME
LAB_ID = "lab.world-writable-entries"
PFX = "lab-check.world-writable-entries."
BASIC = f"{PFX}basic-entries"
EDGE = f"{PFX}edge-entries"
CHECK_IDS = (BASIC, EDGE)
IMAGE = "edu-it/lab-python:dev"

REFERENCE = """\
import sys

for line in sys.stdin:
    fields = line.rstrip("\\n").split(None, 8)
    if len(fields) < 9:  # "total" header, blank line
        continue
    mode, name = fields[0], fields[8]
    if mode[0] != "l" and mode[8] == "w":
        print(name)
"""

#: Reports any entry having a ``w`` anywhere in its mode (owner, group or other).
ALL_W_BITS = REFERENCE.replace(
    'mode[0] != "l" and mode[8] == "w"', 'mode[0] != "l" and "w" in mode'
)

#: Reports symbolic links too (their mode is always ``lrwxrwxrwx``).
SYMLINKS_REPORTED = REFERENCE.replace('mode[0] != "l" and ', "")

#: Skips every directory, so ``drwxrwxrwx`` and ``drwxrwxrwt`` are never reported.
DIRECTORIES_IGNORED = REFERENCE.replace('mode[0] != "l"', 'mode[0] not in "ld"')

#: Treats any sticky entry as not reported, even when it is world-writable (``drwxrwxrwt``).
STICKY_IGNORED = REFERENCE.replace('mode[0] != "l"', 'mode[0] != "l" and mode[9] not in "tT"')

#: Raises an uncaught exception on every input: exit status 1, nothing on stdout.
CRASHING = "raise RuntimeError('boom')\n"

#: Exits cleanly and prints nothing, although names are expected for some cases.
SILENT = "import sys\n\nsys.stdin.read()\n"

#: Prints the right names, then crashes: a non-zero exit wins over matching stdout.
CRASH_AFTER_OUTPUT = REFERENCE + "\nraise SystemExit(3)\n"

#: wrong solution -> {Check that must fail: its outcome code}. A Check not listed must pass.
#: ``no-output`` appears where the mistake makes the program print nothing for a case that expects
#: names (precedence ``crashed`` > ``no-output`` > ``wrong-output``, see ``parse_harness_report``).
WRONG: dict[str, tuple[str, dict[str, str]]] = {
    "all-w-bits": (ALL_W_BITS, {BASIC: "wrong-output", EDGE: "wrong-output"}),
    "symlinks-reported": (SYMLINKS_REPORTED, {BASIC: "wrong-output"}),
    "directories-ignored": (DIRECTORIES_IGNORED, {BASIC: "no-output", EDGE: "no-output"}),
    "sticky-directories-ignored": (STICKY_IGNORED, {EDGE: "no-output"}),
}


def _load(name: str) -> Any:
    return json.loads((TRACK / name).read_text(encoding="utf-8"))


def _feedback_keys() -> dict[str, dict[str, str]]:
    """Check stable id -> outcome code -> Feedback_Key, from the authored catalog."""
    return {
        c["stable_id"]: {o["code"]: o["feedback_key"] for o in c["outcomes"]}
        for c in _load("lab_checks.json")
        if c["stable_id"] in CHECK_IDS
    }


def _case_inputs(check_id: str) -> dict[str, bytes]:
    doc = json.loads((BUNDLE / "sandbox" / "cases" / f"{check_id}.json").read_text("utf-8"))
    return {c["case_id"]: base64.b64decode(c["stdin_b64"]) for c in doc["cases"]}


def _expected(check_id: str) -> dict[str, bytes]:
    doc = json.loads((BUNDLE / "expected" / f"{check_id}.json").read_text("utf-8"))
    return {k: base64.b64decode(v["stdout_b64"]) for k, v in doc["cases"].items()}


def _failing_checks_on_host(source: str, tmp_path: Path) -> set[str]:
    program = tmp_path / "solution.py"
    program.write_text(source, encoding="utf-8", newline="\n")
    failing: set[str] = set()
    for check_id in CHECK_IDS:
        expected = _expected(check_id)
        for case_id, stdin in _case_inputs(check_id).items():
            run = subprocess.run(
                [sys.executable, "-I", str(program)],
                input=stdin,
                capture_output=True,
                timeout=20,
                check=False,
            )
            assert run.returncode == 0, (check_id, case_id, run.stderr)
            if run.stdout.replace(b"\r\n", b"\n") != expected[case_id]:
                failing.add(check_id)
    return failing


# -- content and host-side behaviour ------------------------------------------


def test_each_check_has_distinct_wrong_output_feedback_keys() -> None:
    keys = _feedback_keys()
    assert set(keys) == set(CHECK_IDS)
    wrong = [keys[c]["wrong-output"] for c in CHECK_IDS]
    assert len(set(wrong)) == len(wrong)
    every = [k for outcomes in keys.values() for k in outcomes.values()]
    assert len(every) == len(set(every))


def test_every_failing_outcome_has_its_own_key_without_expected_output() -> None:
    """Requirement 22.9: wrong-output, crashed and no-output are distinct, answer-free keys."""
    doc = {c["stable_id"]: c for c in _load("lab_checks.json") if c["stable_id"] in CHECK_IDS}
    for check_id in CHECK_IDS:
        codes = {o["code"] for o in doc[check_id]["outcomes"]}
        assert {"pass", "wrong-output", "crashed", "no-output"} <= codes
        failing = [o for o in doc[check_id]["outcomes"] if o["code"] != "pass"]
        assert len({o["feedback_key"] for o in failing}) == len(failing)
        # Messages are fixed text: none of them contains any expected output line.
        expected_lines = {
            line
            for stdout in _expected(check_id).values()
            for line in stdout.decode().splitlines()
            if len(line) >= 4
        }
        assert expected_lines
        for outcome in failing:
            for text in outcome["message"].values():
                assert not any(line in text for line in expected_lines), (check_id, outcome["code"])


def test_reference_solution_passes_every_case_on_the_host(tmp_path: Path) -> None:
    assert _failing_checks_on_host(REFERENCE, tmp_path) == set()


@pytest.mark.parametrize("name", list(WRONG))
def test_wrong_solution_fails_the_expected_checks_on_the_host(name: str, tmp_path: Path) -> None:
    source, expected_codes = WRONG[name]
    assert source != REFERENCE
    assert _failing_checks_on_host(source, tmp_path) == set(expected_codes)


def test_the_four_wrong_solutions_have_pairwise_distinct_failure_signatures() -> None:
    """Which (Check, Feedback_Key) pairs fail tells the wrong solutions apart (Req 22.8, 22.9)."""
    keys = _feedback_keys()
    signatures = {
        name: frozenset(keys[c][code] for c, code in codes.items())
        for name, (_src, codes) in WRONG.items()
    }
    assert len(set(signatures.values())) == len(WRONG)


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


def _catalog_image() -> str:
    """The lab's authored ``image`` ("python"): the DockerSandboxProvider maps it to ``IMAGE``."""
    (lab,) = (x for x in _load("labs.json") if x["provenance"]["stable_id"] == LAB_ID)
    return str(lab["image"])


@dataclass(frozen=True)
class _Img:
    image: str = _catalog_image()


@pytest.fixture
def sandbox() -> Iterator[tuple[Any, SandboxHandle]]:
    if not _daemon_ready():
        pytest.skip("no Docker daemon or lab image")
    from edu_it_infrastructure.sandbox.docker_provider import DockerSandboxProvider

    provider = DockerSandboxProvider()
    handle = provider.provision(build_sandbox_spec(_Img(), "pytest-seed-lab-2"))
    try:
        yield provider, handle
    finally:
        provider.destroy(handle)


#: Test-environment grading config. Root cause of the flaky lab 2 tamper tests: ``GradeRun``
#: verifies the freeze (a ``kill -s STOP -1`` exec plus a ``ps`` exec) within
#: ``LAB_GRADING_FREEZE_BOUND`` (1 s) and fails closed, recording ``could_not_complete``, when it
#: cannot. On Docker Desktop (Windows named pipe) two execs take about 0.3 s alone but pass 1.0 s
#: under parallel Docker load; measured: ``_freeze`` aborted after 1.07 s in about 1 run in 10 with
#: four graders in parallel. That is the job working as designed, not a defect, so the tests widen
#: only the bound, and shift the run deadline by the same amount so the Usable_Run_Budget
#: (deadline - freeze bound - teardown reserve) is the production 12 s. The production bound itself
#: is exercised by test_docker_freeze_failure.py and the GradeRun unit tests.
FREEZE_BOUND_FOR_LOADED_HOSTS = timedelta(seconds=10)
GRADE_CONFIG = SimpleNamespace(
    **{name: getattr(cfg, name) for name in dir(cfg) if name.isupper()}
    | {
        "LAB_GRADING_FREEZE_BOUND": FREEZE_BOUND_FOR_LOADED_HOSTS,
        "LAB_GRADING_RUN_DEADLINE": cfg.LAB_GRADING_RUN_DEADLINE
        + FREEZE_BOUND_FOR_LOADED_HOSTS
        - cfg.LAB_GRADING_FREEZE_BOUND,
    }
)


def _check(check_id: str, task_id: str) -> LabCheckDefinition:
    """The Check with the authored outcome-code -> Feedback_Key map, as the catalog supplies it."""
    return LabCheckDefinition(check_id, task_id, 5.0, outcomes=_feedback_keys()[check_id])


def _grade(
    provider: Any, handle: SandboxHandle, source: str, tmp_path: Path
) -> tuple[RunStatus | None, dict[str, Any]]:
    """Put ``source`` in ``/workspace/solution.py`` and run the real ``GradeRun`` on the lab."""
    program = tmp_path / "solution.py"
    program.write_text(source, encoding="utf-8", newline="\n")
    provider.copy_in(handle, program, "/workspace/solution.py")

    lab = LabDefinition(
        lab_id=LAB_ID,
        kind="code",
        image=_catalog_image(),
        concept_ids=(),
        tasks=(
            LabTaskDefinition("lab-task.world-writable-entries.1-basic-entries", required=True),
            LabTaskDefinition("lab-task.world-writable-entries.2-edge-entries", required=True),
        ),
        checks=(
            _check(BASIC, "lab-task.world-writable-entries.1-basic-entries"),
            _check(EDGE, "lab-task.world-writable-entries.2-edge-entries"),
        ),
        bundle_path=BUNDLE_NAME,
    )
    clock = System_Clock()
    runs = In_Memory_GradingRunRepository()
    sessions = In_Memory_LabSessionRepository(runs)
    now = clock.now()
    sessions.add_if_no_active(
        LabSession("s1", "u1", lab.lab_id, SessionStatus.GRADING, now, now + cfg.LAB_SESSION_TTL)
    )
    sessions.transition(
        "s1",
        expected={SessionStatus.GRADING},
        to=SessionStatus.GRADING,
        sandbox_ref=handle.sandbox_id,
    )
    runs.add(GradingRun("r1", "s1", RunStatus.RUNNING, now, now + cfg.LAB_GRADING_RUN_DEADLINE))
    job = GradeRun(
        sessions=sessions,
        runs=runs,
        catalog=Fake_LabCatalog([lab]),
        sandbox=provider,
        record=RecordCheckResult(runs=runs, mastery=In_Memory_MasteryRepository(), clock=clock),
        clock=clock,
        bundle_root=BUNDLES,  # read straight from the repository files
        config=GRADE_CONFIG,
    )
    status = job.execute("r1")
    run = runs.get("r1")
    assert run is not None
    return status, {r.check_id: r for r in run.results}


@pytest.mark.docker
def test_reference_solution_passes_every_check(
    sandbox: tuple[Any, SandboxHandle], tmp_path: Path
) -> None:
    provider, handle = sandbox
    status, results = _grade(provider, handle, REFERENCE, tmp_path)
    assert set(results) == set(CHECK_IDS)
    assert {c: (r.outcome, r.outcome_code) for c, r in results.items()} == {
        c: (CheckOutcome.PASSED, "pass") for c in CHECK_IDS
    }
    assert status is RunStatus.PASSED


@pytest.mark.docker
@pytest.mark.parametrize("name", list(WRONG))
def test_wrong_solution_fails_the_expected_check_with_its_feedback_key(
    sandbox: tuple[Any, SandboxHandle], tmp_path: Path, name: str
) -> None:
    provider, handle = sandbox
    source, expected_codes = WRONG[name]
    status, results = _grade(provider, handle, source, tmp_path)
    keys = _feedback_keys()

    assert set(results) == set(CHECK_IDS)
    failed = {c for c, r in results.items() if r.outcome is CheckOutcome.FAILED}
    assert failed == set(expected_codes)
    for check_id, result in results.items():
        if check_id in expected_codes:
            code = expected_codes[check_id]
            assert result.outcome_code == code, (name, check_id)
            assert result.feedback_key == keys[check_id][code], (name, check_id)
        else:
            assert result.outcome is CheckOutcome.PASSED, (name, check_id)
    assert status is RunStatus.FAILED


@pytest.mark.docker
@pytest.mark.parametrize(
    ("label", "source", "code"),
    [
        ("crashing", CRASHING, "crashed"),
        ("crash-after-correct-output", CRASH_AFTER_OUTPUT, "crashed"),
        ("silent", SILENT, "no-output"),
    ],
)
def test_crashing_and_silent_solutions_map_to_their_own_feedback_keys(
    sandbox: tuple[Any, SandboxHandle], tmp_path: Path, label: str, source: str, code: str
) -> None:
    """Requirement 22.9 on a real harness run: crashed / no-output, not wrong-output."""
    provider, handle = sandbox
    status, results = _grade(provider, handle, source, tmp_path)
    keys = _feedback_keys()

    assert set(results) == set(CHECK_IDS)
    for check_id, result in results.items():
        assert result.outcome is CheckOutcome.FAILED, (label, check_id)
        assert result.outcome_code == code, (label, check_id)
        assert result.feedback_key == keys[check_id][code], (label, check_id)
        assert result.feedback_key != keys[check_id]["wrong-output"]
    assert status is RunStatus.FAILED
