#!/usr/bin/env python3
"""Phase-3 report writer (hands-on-labs task 22.3; Requirements 31.4, 31.5, 31.8, 33.3, 35).

Pure report logic, kept apart from ``run_verification.py`` so it can be tested without running
any acceptance check. The writer:

- consumes the performance attempt log written by ``tools/perf/lab_perf.py`` and re-derives each
  target's verdict from the raw samples with ``tools/perf/protocol.py`` (it never trusts a stored
  ``met`` flag); the FINAL attempt alone decides the pass (Requirements 31.8, 35.4);
- records, per acceptance check, the command invoked and its real outcome (Requirement 35.1);
- includes the performance measurements of the final attempt (sample count, nearest-rank method,
  values, machine setup; Requirement 31.4), the Requirement 33 rule-to-test mapping with the
  outcome of each test group (Requirement 33.3) and the Requirement 23 review outcome
  (Requirement 35.2);
- logs EVERY performance attempt, the failed one included (Requirements 31.8, 35.4);
- REFUSES to write ``docs/reports/phase-3.md`` when any check failed or could not be run, when a
  required check is missing, or when the final performance attempt missed a target or is invalid
  (Requirements 31.5, 35.3). A refusal writes nothing and raises ``PhaseReportRefused`` listing
  every blocker.

Standard library only (plus ``tools/perf/protocol.py``).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

TOOLS = Path(__file__).resolve().parent
REPO = TOOLS.parent
sys.path.insert(0, str(TOOLS / "perf"))
import protocol  # noqa: E402

DEFAULT_REPORT = REPO / "docs" / "reports" / "phase-3.md"
DEFAULT_PERF_LOG = TOOLS / "perf" / "lab-perf-raw.json"
RULE_SUITE = TOOLS / "tests" / "test_labs_verification_suite.py"

TARGET_LABELS = {
    protocol.START_TO_READY: "Start to ready (Req 31.1)",
    protocol.KEYSTROKE: "Keystroke round trip (Req 31.2)",
    protocol.SHELL_CHECK: "Shell Check run (Req 31.3)",
}


class PhaseReportRefused(Exception):
    """Raised when the report must not be written; carries every blocking reason."""

    def __init__(self, blockers: Sequence[str]) -> None:
        self.blockers = list(blockers)
        super().__init__("phase-3 report refused: " + "; ".join(self.blockers))


@dataclass
class CheckRecord:
    """The report's view of one acceptance check (a ``run_verification.Check``)."""

    check_id: str
    requirement: str
    description: str
    command: str
    status: str  # passed | failed | not-run
    detail: str = ""


@dataclass
class GroupRecord:
    """One Requirement 33 test group (a mapped test file) and its real outcome."""

    test_file: str
    status: str  # passed | failed | not-run
    detail: str = ""
    rules: list[str] = field(default_factory=list)
    requirements: list[str] = field(default_factory=list)


@dataclass
class ReviewOutcome:
    """Requirement 23 review outcome, read from the repository and the approval-flow check."""

    labs: dict[str, dict[str, str]]  # stable_id -> {reviewed_by, reviewed_at}
    unreviewed: list[str]
    pending_ids: list[str]
    strict_validation: str  # status of the strict content validate check
    approval_flow: str  # status of the approval-flow tests (golden-set confirmation)


# --------------------------------------------------------------------------------------------
# Performance attempt log
# --------------------------------------------------------------------------------------------


def load_perf_log(path: Path) -> dict[str, Any] | None:
    """Read the attempt log; accepts the raw harness output or a file embedding it.

    Returns ``None`` when the file is absent or is not valid JSON.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    if "attempts" not in data and isinstance(data.get("harness_output"), dict):
        data = data["harness_output"]
    result: dict[str, Any] = data
    return result


def evaluate_attempt(attempt: Mapping[str, Any]) -> dict[str, protocol.TargetResult]:
    """Recompute every target of one logged attempt from its raw samples."""
    targets = attempt.get("targets") or {}
    results: dict[str, protocol.TargetResult] = {}
    for name in (protocol.START_TO_READY, protocol.KEYSTROKE, protocol.SHELL_CHECK):
        entry = targets.get(name)
        if not isinstance(entry, Mapping):
            continue
        raw = [float(v) for v in entry.get("values_seconds") or []]
        results[name] = protocol.evaluate_target(name, raw, str(entry.get("method", "")))
    return results


def perf_blockers(perf: Mapping[str, Any] | None) -> list[str]:
    """Reasons the performance log forbids a report; empty when the final attempt passed."""
    if perf is None:
        return ["performance attempt log is missing or unreadable"]
    attempts = perf.get("attempts")
    if not isinstance(attempts, list) or not attempts:
        return ["performance attempt log has no attempts"]
    blockers: list[str] = []
    if len(attempts) > protocol.MAX_ATTEMPTS:
        blockers.append(
            f"performance log has {len(attempts)} attempts; at most one re-measurement is allowed"
        )
    numbers = [a.get("attempt") for a in attempts]
    if numbers != list(range(1, len(attempts) + 1)):
        blockers.append(f"performance attempts are not numbered 1..{len(attempts)}: {numbers}")
    evaluated = [evaluate_attempt(a) for a in attempts]
    for index, results in enumerate(evaluated[:-1], start=1):
        if len(results) == 3 and all(r.met for r in results.values()):
            blockers.append(
                f"attempt {index} met every target yet was followed by a re-measurement"
            )
    final = evaluated[-1]
    for name in (protocol.START_TO_READY, protocol.KEYSTROKE, protocol.SHELL_CHECK):
        result = final.get(name)
        if result is None:
            blockers.append(f"final performance attempt has no {name} measurement")
        elif not result.enough_samples:
            blockers.append(
                f"final attempt {name}: {result.counted_count} counted sample(s) after "
                f"{protocol.WARMUP_SAMPLES} warm-ups is below the minimum "
                f"{protocol.MIN_COUNTED[name]}"
            )
        elif not result.met:
            blockers.append(
                f"final attempt {name} missed its target (p95={result.p95}, "
                f"max={result.max_value}, limit={result.limit})"
            )
    if not isinstance(perf.get("machine"), Mapping) or not perf["machine"]:
        blockers.append("performance log records no machine setup")
    return blockers


def format_perf_attempts(perf: Mapping[str, Any] | None) -> list[str]:
    """One line per target per attempt, for the verification output (Requirements 31.8, 35.4)."""
    if not perf or not isinstance(perf.get("attempts"), list):
        return ["performance attempts: none logged"]
    lines: list[str] = []
    for attempt in perf["attempts"]:
        results = evaluate_attempt(attempt)
        number = attempt.get("attempt")
        if not results:
            lines.append(f"performance attempt {number}: no target measurements")
        for name, result in results.items():
            lines.append(
                f"performance attempt {number} {name}: counted={result.counted_count} "
                f"raw={result.raw_count} p95={result.p95} max={result.max_value} "
                f"limit={result.limit} met={result.met}"
            )
    final = perf["attempts"][-1].get("attempt")
    lines.append(f"performance final attempt (the one that counts): {final}")
    return lines


# --------------------------------------------------------------------------------------------
# Requirement 33 mapping and Requirement 23 review outcome
# --------------------------------------------------------------------------------------------


def load_rule_map(suite: Path = RULE_SUITE) -> list[tuple[str, list[str], list[tuple[str, str]]]]:
    """Import ``RULE_MAP`` from the labs traceability suite (the phase-2 pattern).

    Each entry is ``(rule key, requirement ids, [(test file, needle), ...])``. Raises
    ``FileNotFoundError`` when the suite does not exist yet.
    """
    if not suite.is_file():
        raise FileNotFoundError(f"traceability suite not found: {suite}")
    spec = importlib.util.spec_from_file_location("labs_traceability_suite", suite)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise ImportError(f"cannot load {suite}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    entries = []
    for key, requirements, tests in module.RULE_MAP:
        reqs = [requirements] if isinstance(requirements, str) else [str(r) for r in requirements]
        entries.append((str(key), reqs, [(str(f), str(n)) for f, n in tests]))
    return entries


def group_mapping(
    rule_map: Sequence[tuple[str, Sequence[str], Sequence[tuple[str, str]]]],
) -> dict[str, GroupRecord]:
    """Group the mapping by test file; one group per distinct mapped test file."""
    groups: dict[str, GroupRecord] = {}
    for key, requirements, tests in rule_map:
        for test_file, _needle in tests:
            group = groups.setdefault(test_file, GroupRecord(test_file, "not-run"))
            if key not in group.rules:
                group.rules.append(key)
            for requirement in requirements:
                if requirement not in group.requirements:
                    group.requirements.append(requirement)
    return groups


def read_review_outcome(repo: Path, *, strict_validation: str, approval_flow: str) -> ReviewOutcome:
    """Requirement 23 outcome: reviewer recorded on each lab, list emptied, flow confirmed."""
    seed = repo / "content" / "linux-fundamentals"
    labs: dict[str, dict[str, str]] = {}
    unreviewed: list[str] = []
    try:
        items = json.loads((seed / "labs.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        items = []
    for item in items if isinstance(items, list) else []:
        provenance = item.get("provenance") or {}
        stable_id = str(item.get("stable_id", "?"))
        reviewer = provenance.get("reviewed_by")
        if reviewer:
            labs[stable_id] = {
                "reviewed_by": str(reviewer),
                "reviewed_at": str(provenance.get("reviewed_at", "")),
            }
        else:
            unreviewed.append(stable_id)
    try:
        pending = json.loads((seed / "pending-review.json").read_text(encoding="utf-8"))
        pending_ids = [str(i) for i in pending.get("stable_ids", [])]
    except (OSError, ValueError, AttributeError):
        pending_ids = ["<pending-review.json unreadable>"]
    return ReviewOutcome(labs, unreviewed, pending_ids, strict_validation, approval_flow)


def review_blockers(review: ReviewOutcome) -> list[str]:
    blockers: list[str] = []
    if not review.labs and not review.unreviewed:
        blockers.append("Requirement 23: no seed labs found in the seed labs.json")
    if review.unreviewed:
        blockers.append(f"Requirement 23: labs without a recorded reviewer: {review.unreviewed}")
    if review.pending_ids:
        blockers.append(f"Requirement 23: Pending_Review_List is not empty: {review.pending_ids}")
    if review.strict_validation != "passed":
        blockers.append("Requirement 23: strict content validation did not pass")
    if review.approval_flow != "passed":
        blockers.append("Requirement 23: approval-flow tests (golden set) did not pass")
    return blockers


# --------------------------------------------------------------------------------------------
# Gate and rendering
# --------------------------------------------------------------------------------------------


def report_blockers(
    checks: Sequence[CheckRecord],
    perf: Mapping[str, Any] | None,
    groups: Mapping[str, GroupRecord],
    review: ReviewOutcome,
    *,
    required_check_ids: Sequence[str] = (),
    mapping_error: str = "",
) -> list[str]:
    """Every reason the report must not be written (Requirements 31.5, 35.3)."""
    blockers: list[str] = []
    present = {c.check_id for c in checks}
    for missing in required_check_ids:
        if missing not in present:
            blockers.append(f"acceptance check {missing} was not run")
    for check in checks:
        if check.status == "failed":
            blockers.append(f"acceptance check {check.check_id} failed: {check.detail}")
        elif check.status != "passed":
            blockers.append(f"acceptance check {check.check_id} could not be run: {check.detail}")
    blockers.extend(perf_blockers(perf))
    if mapping_error:
        blockers.append(f"Requirement 33 mapping unavailable: {mapping_error}")
    elif not groups:
        blockers.append("Requirement 33 mapping is empty")
    for group in groups.values():
        if group.status != "passed":
            blockers.append(f"test group {group.test_file} {group.status}: {group.detail}")
    blockers.extend(review_blockers(review))
    return blockers


def _fmt(value: float | None) -> str:
    """Format a seconds value; None (no counted samples) renders as 'n/a'."""
    return "n/a" if value is None else f"{value:.6f}"


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def render_phase_report(
    checks: Sequence[CheckRecord],
    perf: Mapping[str, Any],
    rule_map: Sequence[tuple[str, Sequence[str], Sequence[tuple[str, str]]]],
    groups: Mapping[str, GroupRecord],
    review: ReviewOutcome,
    *,
    generated_at: datetime | None = None,
) -> str:
    """Render the report. Callers must have passed the gate (``report_blockers`` empty)."""
    stamp = (generated_at or datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%SZ")
    out: list[str] = [
        "# Phase 3 Report: Hands-on labs",
        "",
        f"Generated {stamp} by `tools/run_verification.py --suite labs --phase-report`. The report "
        "is written only when every acceptance check ran and passed and the final performance "
        "attempt met every target (Requirements 35.1, 35.3).",
        "",
        "## 1. Acceptance checks: commands and outcomes (Requirement 35.1)",
        "",
        "| # | Check | Requirement | Description | Command | Outcome |",
        "|---|-------|-------------|-------------|---------|---------|",
    ]
    for number, check in enumerate(checks, start=1):
        outcome = check.status.upper() + (f": {check.detail}" if check.detail else "")
        out.append(
            f"| {number} | `{check.check_id}` | {check.requirement} | {_cell(check.description)} | "
            f"`{_cell(check.command)}` | {_cell(outcome)} |"
        )

    out += ["", "## 2. Performance measurements (Requirement 31)", ""]
    out += [
        f"Percentile method: nearest-rank (ceil(p/100 * n)-th smallest). The first "
        f"{protocol.WARMUP_SAMPLES} samples of each target are discarded as warm-ups. Only the "
        "final attempt decides the pass (Requirements 31.8, 35.4).",
        "",
        "### 2.1 Every attempt (Requirement 31.8)",
        "",
        "| Attempt | Target | Counted | Raw | p95 (s) | Max (s) | Limit (s) | Met |",
        "|---------|--------|---------|-----|---------|---------|-----------|-----|",
    ]
    attempts = perf["attempts"]
    for attempt in attempts:
        for name, result in evaluate_attempt(attempt).items():
            out.append(
                f"| {attempt['attempt']} | {name} | {result.counted_count} | {result.raw_count} | "
                f"{_fmt(result.p95)} | {_fmt(result.max_value)} | {result.limit} | "
                f"{'yes' if result.met else 'NO'} |"
            )
    final_attempt = attempts[-1]
    out += ["", f"### 2.2 Final attempt (attempt {final_attempt['attempt']}) measurements", ""]
    for name, result in evaluate_attempt(final_attempt).items():
        entry = final_attempt["targets"][name]
        criterion = (
            f"every counted run under {result.limit} s (max {_fmt(result.max_value)} s)"
            if name == protocol.SHELL_CHECK
            else f"nearest-rank p95 {_fmt(result.p95)} s under {result.limit} s"
        )
        out += [
            f"#### {TARGET_LABELS[name]}",
            "",
            f"- Sample count: {result.counted_count} counted "
            f"({result.raw_count} raw, {protocol.WARMUP_SAMPLES} warm-ups discarded; minimum "
            f"{protocol.MIN_COUNTED[name]})",
            "- Method: nearest-rank percentile (p95)",
            f"- Measurement: {_cell(str(entry.get('method', '')))}",
            f"- Result: {criterion}: {'MET' if result.met else 'MISSED'}",
            "- Values (seconds, counted samples in collection order):",
            "",
            "```",
            ", ".join(f"{v:.6f}" for v in result.counted_values),
            "```",
            "",
        ]
    out += [
        "### 2.3 Machine setup",
        "",
        "```json",
        json.dumps(perf.get("machine", {}), indent=2),
        "```",
        "",
    ]

    out += [
        "## 3. Traceability of labs rules to automated tests (Requirement 33)",
        "",
        "Fake_Provider tests verify call logic only and are not security evidence; tamper "
        "resistance and the security and grading-integrity rules are evidenced by the "
        "Docker_Provider tests (Requirements 10.12, 29.11).",
        "",
        "### 3.1 Rule mapping",
        "",
        "| Rule | Requirements | Tests (file :: needle) |",
        "|------|--------------|------------------------|",
    ]
    for key, requirements, tests in rule_map:
        tests_text = "<br>".join(f"`{f}` :: `{_cell(n)}`" for f, n in tests) or "none"
        out.append(f"| {_cell(key)} | {_cell(', '.join(requirements))} | {tests_text} |")
    out += [
        "",
        "### 3.2 Outcome of each test group",
        "",
        "| Test group | Rules | Outcome |",
        "|------------|-------|---------|",
    ]
    for group in groups.values():
        outcome = group.status.upper() + (f": {group.detail}" if group.detail else "")
        out.append(f"| `{group.test_file}` | {len(group.rules)} | {_cell(outcome)} |")

    out += ["", "## 4. Expert review outcome (Requirement 23)", ""]
    for stable_id, who in sorted(review.labs.items()):
        out.append(
            f"- `{stable_id}`: reviewer recorded: `{who['reviewed_by']}` at {who['reviewed_at']}"
        )
    out += [
        f"- Pending_Review_List: {'empty' if not review.pending_ids else review.pending_ids}",
        f"- Strict `content validate` (no tolerated failure): {review.strict_validation.upper()}",
        "- Golden_Set addition confirmed after the recorded reviewer (approval-flow tests, "
        f"Requirements 23.3, 23.5, 23.9): {review.approval_flow.upper()}",
        "",
    ]
    return "\n".join(out)


def write_phase_report(
    path: Path,
    checks: Sequence[CheckRecord],
    perf: Mapping[str, Any] | None,
    rule_map: Sequence[tuple[str, Sequence[str], Sequence[tuple[str, str]]]],
    groups: Mapping[str, GroupRecord],
    review: ReviewOutcome,
    *,
    required_check_ids: Sequence[str] = (),
    mapping_error: str = "",
    generated_at: datetime | None = None,
) -> Path:
    """Write ``path`` iff the gate passes; otherwise raise and leave the filesystem untouched."""
    blockers = report_blockers(
        checks,
        perf,
        groups,
        review,
        required_check_ids=required_check_ids,
        mapping_error=mapping_error,
    )
    if blockers or perf is None:
        raise PhaseReportRefused(blockers)
    text = render_phase_report(checks, perf, rule_map, groups, review, generated_at=generated_at)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path
