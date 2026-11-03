"""Generation_Report, PR_Template and CODEOWNERS writers (ai-content Req 5.6, 5.7, 9.1 to 9.4).

Everything here is pure: functions return text or ``{path: bytes}`` and touch
no file, ref or clock. The Generator commits the returned files with the
generated content on the Generation_Branch.

The finding count in the report is ``len(findings)`` of the very list the
Verifier returned, and every finding is rendered, so report and record cannot
disagree (Req 9.3).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final

from edu_it_domain.ai.budget import BudgetKind
from edu_it_domain.ai.generation import STAGES
from edu_it_domain.ai.verification import VerificationFinding

from edu_it_worker.ai.stages import content_dir

__all__ = [
    "CODEOWNERS_PATH",
    "BudgetRemaining",
    "DiscardedUnit",
    "GeneratedItem",
    "GenerationReport",
    "NotGenerated",
    "ReportedFinding",
    "ensure_codeowners_line",
    "governance_files",
    "pr_template_path",
    "render_pr_template",
    "render_report",
    "report_path",
]

CODEOWNERS_PATH: Final = ".github/CODEOWNERS"
_PLACEHOLDER_ORG: Final = "@REPLACE-ME-ORG"
_RUN_RE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


@dataclass(frozen=True, slots=True)
class GeneratedItem:
    """One generated item with its Provenance, tokens and cost (Req 9.1)."""

    stable_id: str
    kind: str
    model: str
    prompt_id: str
    prompt_version: str
    content_version: str
    input_tokens: int
    output_tokens: int
    cost_cents: int
    origin: str = "ai"
    generation_date: str = ""


@dataclass(frozen=True, slots=True)
class ReportedFinding:
    """A Verification_Finding and the item it was raised for."""

    item: str
    finding: VerificationFinding


@dataclass(frozen=True, slots=True)
class DiscardedUnit:
    """A partial Generation_Unit dropped, for example when a Budget was reached."""

    stable_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class NotGenerated:
    """A planned unit that was never produced (provider failure, Budget stop, limit)."""

    stable_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class BudgetRemaining:
    """Remaining amount of each limit at the end of the run, in cents.

    ``cap_cents`` is ``None`` when no Cost_Cap applied to the run.
    """

    daily_cents: int
    track_cents: int
    cap_cents: int | None = None

    def smallest(self) -> tuple[BudgetKind, int]:
        """Return the limit with the least remaining and that amount (Req 9.4)."""
        candidates = [
            (BudgetKind.DAILY, max(0, self.daily_cents)),
            (BudgetKind.TRACK, max(0, self.track_cents)),
        ]
        if self.cap_cents is not None:
            candidates.append((BudgetKind.JOB, max(0, self.cap_cents)))
        return min(candidates, key=lambda pair: pair[1])


@dataclass(frozen=True, slots=True)
class GenerationReport:
    """All facts of one Generation_Run."""

    track: str
    stage: str
    run: str
    branch: str
    live: bool
    items: Sequence[GeneratedItem]
    findings: Sequence[ReportedFinding]
    remaining: BudgetRemaining
    discarded: Sequence[DiscardedUnit] = field(default_factory=tuple)
    not_generated: Sequence[NotGenerated] = field(default_factory=tuple)
    #: Usage of attempts that produced no item (failed or discarded), so the
    #: totals cover every attempt (Req 12.1, 12.3).
    extra_input_tokens: int = 0
    extra_output_tokens: int = 0
    extra_cost_cents: int = 0
    extra_models: Sequence[str] = field(default_factory=tuple)
    stop_reason: str = ""


def _check_run(run: str) -> str:
    if not _RUN_RE.fullmatch(run):
        msg = f"invalid run id: {run!r}"
        raise ValueError(msg)
    return run


def _check_stage(stage: str) -> str:
    if stage not in STAGES:
        msg = f"unknown stage: {stage!r}"
        raise ValueError(msg)
    return stage


def report_path(track: str, stage: str, run: str) -> str:
    """Return ``docs/generation/<track>/<stage>-<run>.md``."""
    content_dir(track)  # rejects unsafe track names
    return f"docs/generation/{track}/{_check_stage(stage)}-{_check_run(run)}.md"


def pr_template_path(stage: str) -> str:
    """Return ``.github/PULL_REQUEST_TEMPLATE/ai-generation-<stage>.md``."""
    return f".github/PULL_REQUEST_TEMPLATE/ai-generation-{_check_stage(stage)}.md"


def _usd(cents: int) -> str:
    return f"USD {cents // 100}.{cents % 100:02d}"


def _line(text: str) -> str:
    """Collapse whitespace so model or sandbox text cannot break the Markdown layout."""
    return " ".join(text.split())


def _cell(text: str) -> str:
    return _line(text).replace("|", "\\|")


def render_report(report: GenerationReport) -> str:
    """Return the Generation_Report as Markdown, deterministic for equal input."""
    items = sorted(report.items, key=lambda i: i.stable_id)
    input_tokens = sum(i.input_tokens for i in items) + report.extra_input_tokens
    output_tokens = sum(i.output_tokens for i in items) + report.extra_output_tokens
    cost = sum(i.cost_cents for i in items) + report.extra_cost_cents
    models = sorted({i.model for i in items} | set(report.extra_models))
    kind, amount = report.remaining.smallest()

    out: list[str] = [
        f"# Generation report: {_line(report.track)} / {report.stage} / {report.run}",
        "",
        f"- Track: {_line(report.track)}",
        f"- Stage: {report.stage}",
        f"- Run: {report.run}",
        f"- Branch: {_line(report.branch)} (unmerged; no item is reviewed)",
        f"- Mode: {'live' if report.live else 'replay'}",
    ]
    if report.stop_reason:
        out.append(f"- Stopped: {_line(report.stop_reason)}")
    out += [
        "",
        "## Totals",
        "",
        f"- Items generated: {len(items)}",
        f"- Input tokens: {input_tokens}",
        f"- Output tokens: {output_tokens}",
        f"- Total cost: {_usd(cost)}",
        f"- Models: {', '.join(models) if models else 'none'}",
        "",
        "## Budget remaining",
        "",
        f"- Daily: {_usd(max(0, report.remaining.daily_cents))}",
        f"- Track: {_usd(max(0, report.remaining.track_cents))}",
        "- Cost cap: "
        + (
            "none"
            if report.remaining.cap_cents is None
            else _usd(max(0, report.remaining.cap_cents))
        ),
        f"- Smallest remaining: {_usd(amount)} ({kind.value})",
        "",
        "## Items",
        "",
    ]
    if not items:
        out += ["None.", ""]
    for item in items:
        out += [
            f"### {_line(item.stable_id)}",
            "",
            f"- Kind: {_line(item.kind)}",
            f"- Origin: {_line(item.origin)}",
            f"- Model: {_line(item.model)}",
            f"- Prompt: {_line(item.prompt_id)} v{_line(item.prompt_version)}",
            f"- Content version: {_line(item.content_version)}",
        ]
        if item.generation_date:
            out.append(f"- Generation date: {_line(item.generation_date)}")
        out += [
            f"- Tokens: {item.input_tokens} in, {item.output_tokens} out",
            f"- Cost: {_usd(item.cost_cents)}",
            "",
        ]

    out += [
        "## Verification findings",
        "",
        f"Findings recorded: {len(report.findings)}",
        "",
    ]
    for number, entry in enumerate(report.findings, start=1):
        f = entry.finding
        detail = f": {_line(f.detail)}" if f.detail else ""
        subject = f"item `{_line(entry.item)}` target `{_line(f.target)}`"
        out.append(f"{number}. [{f.kind.value}] {subject}{detail}")
    if report.findings:
        out.append("")

    out += ["## Discarded units", ""]
    out += [f"- `{_line(u.stable_id)}`: {_line(u.reason)}" for u in report.discarded] or ["None."]
    out += ["", "## Items not generated", ""]
    out += [f"- `{_line(u.stable_id)}`: {_line(u.reason)}" for u in report.not_generated] or [
        "None."
    ]
    out.append("")
    return "\n".join(out)


_CHECKLIST: Final[tuple[tuple[str, str], ...]] = (
    ("Correctness", "Facts, commands, answer keys and lab checks are right."),
    ("FR/EN parity", "The French and English versions say the same thing."),
    ("Glossary use", "Terms follow the track glossary in both locales."),
    ("Sources", "Claims that need a source cite one that exists."),
    (
        "Verification findings",
        "Every finding in the Generation_Report is resolved or accepted, with a note.",
    ),
)


def render_pr_template(stage: str) -> str:
    """Return the PR_Template with the review checklist for ``stage`` (Req 5.6)."""
    _check_stage(stage)
    lines = [
        f"## AI generation: {stage}",
        "",
        "This branch holds AI-drafted content. No item is reviewed yet. Do not merge until an",
        "expert of the track has gone through the checklist.",
        "",
        "Generation report: `docs/generation/<track>/" + stage + "-<run>.md`",
        "",
        "### Review checklist",
        "",
    ]
    lines += [f"- [ ] **{name}**: {text}" for name, text in _CHECKLIST]
    lines += [
        "- [ ] Reviewer fields (`reviewed_by`, `reviewed_at`) are set on every item I approve.",
        "",
    ]
    return "\n".join(lines)


def ensure_codeowners_line(existing: str | None, track: str) -> str:
    """Return CODEOWNERS text with a line for ``content/<track>/`` (Req 5.7).

    An existing line for the track path is left as it is, whoever owns it. A
    missing one is appended with a placeholder team handle that the project
    owner replaces (decision D11).
    """
    base = content_dir(track)
    path = f"/{base}/"
    text = existing or ""
    for raw in text.splitlines():
        parts = raw.split()
        if parts and not parts[0].startswith("#") and parts[0].rstrip("/") == path.rstrip("/"):
            return text if text.endswith("\n") or not text else text + "\n"
    if text and not text.endswith("\n"):
        text += "\n"
    return f"{text}{path} {_PLACEHOLDER_ORG}/{track}-reviewers\n"


def governance_files(
    report: GenerationReport, existing: Mapping[str, str] | None = None
) -> dict[str, bytes]:
    """Return the report, PR_Template and CODEOWNERS files to commit.

    ``existing`` maps repository paths to current text; only
    :data:`CODEOWNERS_PATH` is read.
    """
    present = existing or {}
    return {
        report_path(report.track, report.stage, report.run): render_report(report).encode("utf-8"),
        pr_template_path(report.stage): render_pr_template(report.stage).encode("utf-8"),
        CODEOWNERS_PATH: ensure_codeowners_line(present.get(CODEOWNERS_PATH), report.track).encode(
            "utf-8"
        ),
    }
