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
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SEED_DIR = REPO / "content" / "linux-fundamentals"

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

    def command_text(self) -> str:
        return " ".join(self.command) if self.command else "(no command wired)"


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


def _inspect_seed_approval(seed_dir: Path) -> tuple[bool, str]:
    """Pass only when every provenanced seed item records ``reviewed_by``."""
    missing = 0
    total = 0
    for path in sorted(seed_dir.glob("*.json")):
        items = json.loads(path.read_text(encoding="utf-8"))
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
        command = [sys.executable, *check.command[1:]]
        result = subprocess.run(command, cwd=REPO, capture_output=True, text=True, check=False)
        check.status = "passed" if result.returncode == 0 else "failed"
        tail = (result.stdout + result.stderr).strip().splitlines()
        check.detail = tail[-1] if tail else ""
    check.seconds = round(time.monotonic() - started, 1)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-coverage", action="store_true", help="skip the full-suite coverage run"
    )
    parser.add_argument(
        "--only", nargs="*", help="run only checks whose id starts with one of these"
    )
    parser.add_argument("--json", type=Path, help="write the results to this JSON file")
    args = parser.parse_args(argv)

    checks = build_checks(skip_coverage=args.skip_coverage)
    if args.only:
        checks = [c for c in checks if any(c.check_id.startswith(prefix) for prefix in args.only)]
    for check in checks:
        run_check(check)
        print(
            f"[{check.status.upper():7}] {check.check_id} (Req {check.requirement}) {check.description}"
        )
        print(f"          command: {check.command_text()}")
        print(f"          result : {check.detail}")
    if args.json:
        args.json.write_text(json.dumps([asdict(c) for c in checks], indent=2), encoding="utf-8")
    return 1 if any(c.status == "failed" for c in checks) else 0


if __name__ == "__main__":
    raise SystemExit(main())
