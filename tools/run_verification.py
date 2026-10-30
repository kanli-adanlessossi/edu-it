#!/usr/bin/env python3
"""Learning-core verification suite runner (Requirement 30, task 24.1).

Aggregates the Requirement 30 checks into one command. Each check is a named pytest
invocation (or, for the recorded-approval check, an inline inspection); the runner
executes them, prints the command and the pass/fail outcome of each, and can write the
results as JSON for the phase-2 report (task 25, Requirement 31). Failures are recorded,
never omitted. The exit status is non-zero when any executed check fails.

Usage::

    python tools/run_verification.py [--skip-coverage] [--only 30.2 30.5] [--json PATH]

Checks that need other tooling (30.9 Playwright + axe-core, task 24.2) report ``not-run``
until that task wires a command in.

Hands-on-labs suite (task 22.3; Requirements 31, 33, 35)::

    python tools/run_verification.py --suite labs [--skip-perf] [--only labs-unit]
        [--json PATH] [--perf-log PATH] [--phase-report [PATH]]

``--suite labs`` runs the labs acceptance checks (unit, property, docker-marked, web,
Playwright, axe-core, content validate strict and pending, ADR pin test, traceability,
performance, image build and scan), prints every performance attempt, and runs each
Requirement 33 test group. ``--phase-report`` then asks ``tools/phase_report.py`` to write
``docs/reports/phase-3.md``; the writer refuses (exit 1, nothing written) when any check
failed or could not be run, or when the final performance attempt missed a target.
A tool that is not installed makes its check ``not-run``, never ``passed``.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SEED_DIR = REPO / "content" / "linux-fundamentals"
sys.path.insert(0, str(Path(__file__).resolve().parent))
import phase_report  # noqa: E402

_SUITE = "tools/tests/test_learning_verification_suite.py"
_INFRA = "packages/infrastructure/tests"
_CONTENT = "packages/content/tests"
_API = "apps/api/tests"


@dataclass
class Check:
    """One Requirement 30 check: its id, description, and the command that proves it."""

    check_id: str
    requirement: str
    description: str
    command: list[str] | None
    status: str = "not-run"  # passed | failed | not-run
    detail: str = ""
    seconds: float = 0.0
    cwd: str = ""  # relative to the repository root; empty means the root

    def command_text(self) -> str:
        if not self.command:
            return "(no command wired)"
        text = " ".join(self.command)
        return f"(cd {self.cwd} && {text})" if self.cwd else text


def _pytest(*targets: str, extra: tuple[str, ...] = ()) -> list[str]:
    return [
        "python",
        "-m",
        "pytest",
        "-q",
        "--no-header",
        "-p",
        "no:cacheprovider",
        *extra,
        *targets,
    ]


def build_checks(*, skip_coverage: bool) -> list[Check]:
    coverage_command = (
        None
        if skip_coverage
        else _pytest("--cov=edu_it_domain", "--cov-fail-under=90", "--cov-report=term")
    )
    return [
        Check(
            "30.1-mapping",
            "30.1",
            "Every domain rule maps to a requirement and a test; coverage gate configured",
            _pytest(
                f"{_SUITE}::test_every_domain_rule_maps_to_a_requirement_and_a_test",
                f"{_SUITE}::test_every_mapping_entry_matches_a_real_rule",
                f"{_SUITE}::test_domain_coverage_gate_is_at_least_90_percent_and_wired_into_runners",
            ),
        ),
        Check(
            "30.1-coverage",
            "30.1",
            "Domain statement coverage is at least 90% across the whole suite",
            coverage_command,
        ),
        Check(
            "30.2",
            "30.2",
            "content validate fails on missing translation, broken reference, cycle, "
            "unknown MDX component, AI item without reviewer",
            _pytest(_SUITE, f"{_CONTENT}/test_validate_properties.py"),
        ),
        Check(
            "30.3",
            "30.3",
            "content sync twice is a no-op; content edits never reset completions",
            _pytest(
                f"{_CONTENT}/test_sync.py",
                f"{_CONTENT}/test_sync_properties.py",
                f"{_SUITE}::test_second_sync_of_seed_makes_no_changes",
                f"{_SUITE}::test_content_edit_updates_item_but_never_resets_completions",
            ),
        ),
        Check(
            "30.4",
            "30.4",
            "Both lessons complete in fr and en; locks lift only through mastery",
            _pytest(
                f"{_SUITE}::test_both_seed_lessons_complete_in_each_language_and_locks_lift_by_mastery",
                f"{_INFRA}/test_unlocking_completion_properties.py",
                f"{_API}/test_errors_locale_fallback_property.py",
                f"{_APP}/test_get_lesson_fallback.py",
            ),
        ),
        Check(
            "30.5",
            "30.5",
            "Shuffling, answer matching, cooldown, anti-farming, hint multipliers",
            _pytest(
                f"{_INFRA}/test_scoring_properties.py",
                f"{_INFRA}/test_cooldown_properties.py",
                f"{_INFRA}/test_antifarming_properties.py",
                f"{_INFRA}/test_hints_properties.py",
                f"{_API}/test_quiz_attempt_cooldown.py",
                f"{_API}/test_quiz_cooldown_property.py",
            ),
        ),
        Check(
            "30.6",
            "30.6",
            "Clock-driven review lifecycle: due, passed, failed with interval changes",
            _pytest(
                f"{_INFRA}/test_learning_integration.py::test_enrollment_to_project_submission_journey",
                f"{_INFRA}/test_review_properties.py",
                f"{_INFRA}/test_decay_properties.py",
                f"{_INFRA}/test_worked_example_e2e.py",
            ),
        ),
        Check(
            "30.7",
            "30.7",
            "A duplicate quiz submission is counted once",
            _pytest(
                f"{_INFRA}/test_property_idempotency_key.py",
                f"{_INFRA}/test_adapter_idempotency_store.py",
                f"{_INFRA}/test_learning_integration.py::test_shared_redis_idempotency_across_two_worker_stores",
            ),
        ),
        Check(
            "30.8",
            "30.8",
            "Export and deletion include all learning data",
            _pytest(
                f"{_INFRA}/test_gdpr_learning_data_coverage.py",
                f"{_INFRA}/test_gdpr_learning_data_coverage_properties.py",
                f"{_INFRA}/test_learning_integration.py::test_export_and_deletion_cover_every_personal_data_table",
            ),
        ),
        Check(
            "30.9",
            "30.9",
            "Playwright + axe-core on every learner page under /fr and /en (task 24.2)",
            None,
        ),
        Check(
            "30.10-flow",
            "30.10",
            "Approval records the reviewer and marks the Golden_Set; no approval marks nothing",
            _pytest(
                f"{_CONTENT}/test_sync_approval.py",
                f"{_SUITE}::test_approval_records_reviewer_and_marks_seed_as_golden_set",
                f"{_SUITE}::test_without_approval_nothing_is_synced_attributed_or_marked",
            ),
        ),
        Check(
            "30.10-recorded",
            "30.10",
            "The committed Seed_Content carries a recorded reviewer (user approval)",
            ["inspect", str(SEED_DIR)],
        ),
    ]


_APP = "packages/application/tests"

# --- hands-on-labs suite (task 22.3) ------------------------------------------------------

PERF_LOG = "tools/perf/lab-perf-raw.json"
LAB_IMAGES = ("edu-it/lab-linux-base:dev", "edu-it/lab-python:dev")
_E2E = "e2e"


def _perf_command() -> list[str]:
    """The harness invocation: one sample per driver run; ``lab_perf`` applies the protocol."""
    interpreter = Path(sys.executable).as_posix()
    return [
        "python",
        "tools/perf/lab_perf.py",
        "--start-cmd",
        f"{interpreter} tools/perf/start_driver.py",
        "--keystroke-cmd",
        f"{interpreter} tools/perf/keystroke_driver.py",
        "--check-cmd",
        f"{interpreter} tools/perf/check_driver.py",
        "--out",
        PERF_LOG,
    ]


def _trivy(image: str) -> list[str]:
    # Same flags as .github/workflows/lab-images.yml: every severity, fixed or not, no ignore file.
    return [
        "trivy",
        "image",
        "--scanners",
        "vuln",
        "--severity",
        "UNKNOWN,LOW,MEDIUM,HIGH,CRITICAL",
        "--exit-code",
        "1",
        image,
    ]


def _traceability_targets() -> list[str]:
    found = sorted(p.name for p in (REPO / "tools" / "tests").glob("test_labs_verification*.py"))
    return [f"tools/tests/{name}" for name in found] or [
        "tools/tests/test_labs_verification_suite.py"
    ]


def build_labs_checks(*, skip_perf: bool = False) -> list[Check]:
    """The labs acceptance checks, in report order. ``skip_perf`` leaves the check not-run."""
    no_docker = ("-m", "not docker")
    web = "apps/web"
    return [
        Check(
            "labs-unit",
            "5-30",
            "Labs unit tests (Docker-marked tests excluded)",
            _pytest(*no_docker, "-k", "not propert"),
        ),
        Check(
            "labs-property",
            "1-30",
            "Labs property-based tests (Docker-marked tests excluded)",
            _pytest(*no_docker, "-k", "propert"),
        ),
        Check(
            "labs-docker",
            "3, 10, 14, 28, 29",
            "Docker-marked tests against the Docker_Provider and the lab images",
            _pytest("-m", "docker"),
        ),
        Check(
            "labs-web",
            "15, 16, 18",
            "Web unit and property tests (vitest)",
            ["npm", "test"],
            cwd=web,
        ),
        Check(
            "labs-playwright",
            "15, 32",
            "Playwright seed-lab page tests against the deterministic mock API",
            ["npx", "playwright", "test", f"{_E2E}/seed-labs.spec.ts"],
            cwd=web,
        ),
        Check(
            "labs-playwright-real",
            "32",
            "Playwright learner journey against the real stack (needs the stack and E2E_MAIL_LOG)",
            ["npm", "run", "test:e2e:real"],
            cwd=web,
        ),
        Check(
            "labs-axe",
            "16",
            "axe-core accessibility on the lab view and every learner page, /fr and /en",
            [
                "npx",
                "playwright",
                "test",
                f"{_E2E}/lab-view-a11y.spec.ts",
                f"{_E2E}/learner-a11y.spec.ts",
            ],
            cwd=web,
        ),
        Check(
            "labs-content-strict",
            "20, 23.8",
            "content validate, strict (no tolerated failure)",
            ["python", "-m", "edu_it_content.cli", "validate", "--content-root", "content"],
        ),
        Check(
            "labs-content-pending",
            "20.12, 20.13",
            "content validate, --allow-pending (the CI mode)",
            [
                "python",
                "-m",
                "edu_it_content.cli",
                "validate",
                "--allow-pending",
                "--content-root",
                "content",
            ],
        ),
        Check(
            "labs-review-approval",
            "23",
            "Approval flow: reviewer recorded, Golden_Set only after confirmation, rejection path",
            _pytest(f"{_CONTENT}/test_lab_approval.py"),
        ),
        Check(
            "labs-adr-pins",
            "4.13, 4.14",
            "ADR 0001 pins match the manifests for the dependencies this spec introduces",
            _pytest("tools/tests/test_labs_adr_pins.py"),
        ),
        Check(
            "labs-traceability",
            "33",
            "Every labs rule maps to a requirement and an existing test",
            _pytest(*_traceability_targets()),
        ),
        Check(
            "labs-performance",
            "31",
            "Start-to-ready, keystroke and shell-Check performance (30/200 samples, 3 warm-ups, "
            "at most one re-measurement)",
            None if skip_perf else _perf_command(),
        ),
        Check(
            "labs-image-build",
            "4",
            "Build both Lab_Images with docker buildx bake",
            ["docker", "buildx", "bake", "--load"],
            cwd="lab-images",
        ),
        *(
            Check(
                f"labs-image-scan-{image.split('/')[1].split(':')[0]}",
                "4",
                f"Trivy scan of {image}: fail on any finding, every severity",
                _trivy(image),
            )
            for image in LAB_IMAGES
        ),
    ]


def _inspect_seed_approval(seed_dir: Path) -> tuple[bool, str]:
    """Pass only when every provenanced seed item records ``reviewed_by``."""
    missing = 0
    total = 0
    for path in sorted(seed_dir.glob("*.json")):
        items = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(items, list):  # e.g. pending-review.json is an object, not items
            continue
        for item in items:
            provenance = item.get("provenance")
            if isinstance(provenance, dict):
                total += 1
                if not provenance.get("reviewed_by"):
                    missing += 1
    if missing:
        return (
            False,
            f"{missing} of {total} seed items have no recorded reviewer (approval pending)",
        )
    return True, f"all {total} seed items record a reviewer"


def run_check(check: Check) -> None:
    if check.command is None:
        check.status, check.detail = "not-run", "no command wired for this check"
        return
    started = time.monotonic()
    if check.command[0] == "inspect":
        ok, check.detail = _inspect_seed_approval(Path(check.command[1]))
        check.status = "passed" if ok else "failed"
    else:
        if check.command[0] == "python":
            command = [sys.executable, *check.command[1:]]
        else:
            tool = shutil.which(check.command[0])
            if tool is None:
                check.status = "not-run"
                check.detail = f"tool not found on PATH: {check.command[0]}"
                return
            command = [tool, *check.command[1:]]
        try:
            result = subprocess.run(
                command,
                cwd=REPO / check.cwd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
        except OSError as exc:
            check.status, check.detail = "not-run", f"could not start: {exc}"
            return
        check.status = "passed" if result.returncode == 0 else "failed"
        tail = (result.stdout + result.stderr).strip().splitlines()
        check.detail = tail[-1] if tail else ""
    check.seconds = round(time.monotonic() - started, 1)


def _run_groups(checks: list[Check]) -> tuple[dict[str, phase_report.GroupRecord], str]:
    """Run each Requirement 33 test group (a mapped test file) and record its real outcome."""
    try:
        rule_map = phase_report.load_rule_map()
    except (OSError, ImportError, AttributeError, ValueError, SyntaxError) as exc:
        return {}, f"{type(exc).__name__}: {exc}"
    groups = phase_report.group_mapping(rule_map)
    by_id = {c.check_id: c for c in checks}
    for path, group in groups.items():
        if path.endswith(".py"):
            if not (REPO / path).is_file():
                group.status, group.detail = "failed", "test file does not exist"
                continue
            probe = Check(f"group:{path}", "33", path, _pytest(path))
            run_check(probe)
            group.status, group.detail = probe.status, probe.detail
            continue
        # TypeScript tests run inside the web checks; the group takes that check's outcome.
        if path.endswith((".test.ts", ".test.tsx")):
            owner = "labs-web"
        elif path.endswith(".e2e.ts"):
            owner = "labs-playwright-real"
        elif path.endswith(".spec.ts"):
            owner = "labs-axe" if "a11y" in path else "labs-playwright"
        else:
            group.status, group.detail = "failed", "no runner for this test file type"
            continue
        owner_check = by_id.get(owner)
        group.status = owner_check.status if owner_check else "not-run"
        group.detail = f"covered by check {owner}"
    return groups, ""


def _label(check: Check) -> str:
    return (
        f"[{check.status.upper():7}] {check.check_id} (Req {check.requirement}) {check.description}"
    )


def _to_record(check: Check) -> phase_report.CheckRecord:
    return phase_report.CheckRecord(
        check.check_id,
        check.requirement,
        check.description,
        check.command_text(),
        check.status,
        check.detail,
    )


def _main_labs(args: argparse.Namespace) -> int:
    checks = build_labs_checks(skip_perf=args.skip_perf)
    required = [c.check_id for c in checks]
    if args.only:
        checks = [c for c in checks if any(c.check_id.startswith(p) for p in args.only)]
    perf_started = 0.0
    for check in checks:
        if check.check_id == "labs-performance":
            perf_started = time.time()
        run_check(check)
        print(_label(check))
        print(f"          command: {check.command_text()}")
        print(f"          result : {check.detail}")

    perf_log = args.perf_log
    perf = phase_report.load_perf_log(perf_log)
    for line in phase_report.format_perf_attempts(perf):  # every attempt, failed ones included
        print(f"          {line}")

    groups: dict[str, phase_report.GroupRecord] = {}
    mapping_error = ""
    if args.phase_report is not None or not args.only:
        groups, mapping_error = _run_groups(checks)
        for group in groups.values():
            print(f"[{group.status.upper():7}] group {group.test_file} {group.detail}")
        if mapping_error:
            print(f"[NOT-RUN] Requirement 33 mapping unavailable: {mapping_error}")

    if args.json:
        payload = {
            "checks": [asdict(c) for c in checks],
            "groups": [asdict(g) for g in groups.values()],
            "performance_attempts": phase_report.format_perf_attempts(perf),
        }
        args.json.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    failed = any(c.status != "passed" for c in checks) or any(
        g.status != "passed" for g in groups.values()
    )
    if args.phase_report is None:
        return 1 if failed else 0

    by_id = {c.check_id: c.status for c in checks}
    review = phase_report.read_review_outcome(
        REPO,
        strict_validation=by_id.get("labs-content-strict", "not-run"),
        approval_flow=by_id.get("labs-review-approval", "not-run"),
    )
    records = [_to_record(c) for c in checks]
    extra: list[str] = []
    if "labs-performance" in by_id and (
        not perf_log.is_file() or perf_log.stat().st_mtime < perf_started
    ):
        extra.append("performance attempt log was not produced by this run")
    try:
        if extra:
            raise phase_report.PhaseReportRefused(extra)
        rule_map = phase_report.load_rule_map() if not mapping_error else []
        path = phase_report.write_phase_report(
            args.phase_report,
            records,
            perf,
            rule_map,
            groups,
            review,
            required_check_ids=required,
            mapping_error=mapping_error,
        )
    except phase_report.PhaseReportRefused as refusal:
        # Also surface the other blockers when the freshness guard fired first.
        blockers = list(refusal.blockers)
        if extra:
            blockers += phase_report.report_blockers(
                records,
                perf,
                groups,
                review,
                required_check_ids=required,
                mapping_error=mapping_error,
            )
        print("PHASE REPORT REFUSED: no report was written", file=sys.stderr)
        for blocker in dict.fromkeys(blockers):
            print(f"  - {blocker}", file=sys.stderr)
        return 1
    print(f"phase report written: {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-coverage", action="store_true", help="skip the full-suite coverage run"
    )
    parser.add_argument(
        "--only", nargs="*", help="run only checks whose id starts with one of these"
    )
    parser.add_argument("--json", type=Path, help="write the results to this JSON file")
    parser.add_argument(
        "--suite",
        choices=("learning", "labs"),
        default="learning",
        help="which acceptance suite to run (default: learning)",
    )
    parser.add_argument(
        "--skip-perf", action="store_true", help="labs: leave the performance check not-run"
    )
    parser.add_argument(
        "--perf-log",
        type=Path,
        default=REPO / PERF_LOG,
        help="labs: performance attempt log written by tools/perf/lab_perf.py",
    )
    parser.add_argument(
        "--phase-report",
        nargs="?",
        type=Path,
        const=phase_report.DEFAULT_REPORT,
        default=None,
        help="labs: write docs/reports/phase-3.md (or PATH) only if every check passed",
    )
    args = parser.parse_args(argv)
    if args.phase_report is not None and args.suite != "labs":
        parser.error("--phase-report requires --suite labs")
    if args.suite == "labs":
        return _main_labs(args)

    checks = build_checks(skip_coverage=args.skip_coverage)
    if args.only:
        checks = [c for c in checks if any(c.check_id.startswith(prefix) for prefix in args.only)]
    for check in checks:
        run_check(check)
        print(
            f"[{check.status.upper():7}] {check.check_id} (Req {check.requirement}) "
            f"{check.description}"
        )
        print(f"          command: {check.command_text()}")
        print(f"          result : {check.detail}")
    if args.json:
        args.json.write_text(json.dumps([asdict(c) for c in checks], indent=2), encoding="utf-8")
    return 1 if any(c.status == "failed" for c in checks) else 0


if __name__ == "__main__":
    raise SystemExit(main())
