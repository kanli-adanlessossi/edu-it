"""Property 20: the Generation_Report is complete and consistent.

For any run with any items, findings, failures and spend, the report lists every generated item
with its provenance, tokens and cost, lists every finding so that the finding count equals the
number recorded by the Verifier, states the remaining Budget equal to the smallest remaining
amount across the daily, per-track and cap limits, and for a Live_Run includes the total cost,
tokens and models.

The writer is tested on generated reports (pure). The Generator is tested end to end on real
temporary git repositories with the fake providers: the report committed on the Generation_Branch
is read back and compared to the outcome and to the configured limits.

**Validates: Requirements 9.1, 9.2, 9.3, 9.4, 24.6**
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest
from edu_it_application.ai import VerifyItem
from edu_it_domain import config as domain_config
from edu_it_domain.ai.verification import FindingKind, VerificationFinding
from edu_it_infrastructure.fakes.clock import Test_Clock
from edu_it_infrastructure.fakes.generation_ledger import In_Memory_GenerationLedger
from edu_it_infrastructure.fakes.llm_provider import Fake_LLMProvider
from edu_it_infrastructure.fakes.sandbox_provider import Fake_SandboxProvider
from edu_it_infrastructure.git_workspace import SubprocessGitWorkspace
from edu_it_infrastructure.llm.ledger_binding import JobScope, LedgerAttemptGate, LedgerUsageSink
from edu_it_worker.ai.generator import (
    ExitCode,
    GenerationRequest,
    Generator,
    GeneratorDeps,
    RunProviders,
)
from edu_it_worker.ai.prompt_store import PromptStore
from edu_it_worker.ai.report import (
    BudgetRemaining,
    DiscardedUnit,
    GeneratedItem,
    GenerationReport,
    NotGenerated,
    ReportedFinding,
    render_report,
    report_path,
)
from hypothesis import given, settings
from hypothesis import strategies as st

TRACK = "networking-basics"

# -- pure writer property ------------------------------------------------------------------------

_ID = st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789.-_", min_size=1, max_size=12)
_FREE = st.text(max_size=30)
# Backticks delimit the ids in the Markdown, so the parser below needs them out of the ids.
_NONEMPTY = st.text(st.characters(exclude_characters="`"), min_size=1, max_size=20).filter(
    lambda s: bool(s.strip())
)
_MODEL = st.sampled_from(["m-1", "m-2", "model-x", "z"])
_CENTS = st.integers(min_value=0, max_value=10_000_000)


def _collapse(text: str) -> str:
    return " ".join(text.split())


def _usd(cents: int) -> str:
    return f"USD {cents // 100}.{cents % 100:02d}"


@st.composite
def _items(draw: st.DrawFn) -> list[GeneratedItem]:
    ids = draw(st.lists(_ID, unique=True, max_size=6))
    return [
        GeneratedItem(
            stable_id=sid,
            kind=draw(st.sampled_from(["lesson", "quiz", "lab"])),
            model=draw(_MODEL),
            prompt_id=draw(st.sampled_from(["lesson", "quiz", "lab"])),
            prompt_version=str(draw(st.integers(1, 9))),
            content_version="sha256:" + draw(st.text("0123456789abcdef", min_size=4, max_size=8)),
            input_tokens=draw(st.integers(0, 100_000)),
            output_tokens=draw(st.integers(0, 100_000)),
            cost_cents=draw(_CENTS),
        )
        for sid in ids
    ]


@st.composite
def _reports(draw: st.DrawFn) -> GenerationReport:
    items = draw(_items())
    kinds = list(FindingKind)
    findings = [
        ReportedFinding(
            draw(_NONEMPTY),
            VerificationFinding(draw(st.sampled_from(kinds)), draw(_NONEMPTY), draw(_FREE)),
        )
        for _ in range(draw(st.integers(0, 8)))
    ]
    remaining = BudgetRemaining(
        daily_cents=draw(st.integers(-1000, 5000)),
        track_cents=draw(st.integers(-1000, 5000)),
        cap_cents=draw(st.none() | st.integers(-1000, 5000)),
    )
    return GenerationReport(
        track=TRACK,
        stage="lessons",
        run="r1",
        branch="generate/networking-basics/lessons/r1",
        live=draw(st.booleans()),
        items=items,
        findings=findings,
        remaining=remaining,
        discarded=[
            DiscardedUnit(draw(_ID), draw(_NONEMPTY)) for _ in range(draw(st.integers(0, 3)))
        ],
        not_generated=[
            NotGenerated(draw(_ID), draw(_NONEMPTY)) for _ in range(draw(st.integers(0, 3)))
        ],
        extra_input_tokens=draw(st.integers(0, 5000)),
        extra_output_tokens=draw(st.integers(0, 5000)),
        extra_cost_cents=draw(st.integers(0, 5000)),
        extra_models=draw(st.lists(_MODEL, max_size=3)),
    )


def _section(text: str, title: str) -> str:
    match = re.search(rf"^## {re.escape(title)}\n(.*?)(?=^## |\Z)", text, re.S | re.M)
    assert match is not None, title
    return match.group(1)


def _check_report_text(report: GenerationReport, text: str) -> None:
    # Req 9.1: every item, with provenance, tokens and cost, in a stable order.
    items_part = _section(text, "Items")
    headings = re.findall(r"^### (.*)$", items_part, re.M)
    assert headings == sorted(i.stable_id for i in report.items)
    blocks = re.split(r"^### ", items_part, flags=re.M)[1:]
    for item, block in zip(sorted(report.items, key=lambda i: i.stable_id), blocks, strict=True):
        assert f"- Model: {item.model}" in block
        assert f"- Prompt: {item.prompt_id} v{item.prompt_version}" in block
        assert f"- Content version: {item.content_version}" in block
        assert f"- Origin: {item.origin}" in block
        assert f"- Tokens: {item.input_tokens} in, {item.output_tokens} out" in block
        assert f"- Cost: {_usd(item.cost_cents)}" in block

    # Req 9.3: the count equals the number recorded, and every finding is listed in order.
    findings_part = _section(text, "Verification findings")
    assert f"Findings recorded: {len(report.findings)}" in findings_part
    numbered = re.findall(
        r"^(\d+)\. \[([a-z_]+)\] item `(.*?)` target `(.*?)`", findings_part, re.M
    )
    assert len(numbered) == len(report.findings)
    for position, (number, kind, item, target), entry in zip(
        range(1, len(numbered) + 1), numbered, report.findings, strict=True
    ):
        assert int(number) == position
        assert kind == entry.finding.kind.value
        assert item == _collapse(entry.item)
        assert target == _collapse(entry.finding.target)

    # Req 9.2: discarded units and items not generated are all listed.
    for unit in report.discarded:
        assert f"- `{unit.stable_id}`: {_collapse(unit.reason)}" in _section(
            text, "Discarded units"
        )
    for gap in report.not_generated:
        assert f"- `{gap.stable_id}`: {_collapse(gap.reason)}" in _section(
            text, "Items not generated"
        )

    # Req 9.4: the smallest remaining amount across daily, track and cap.
    limits = {
        "daily": report.remaining.daily_cents,
        "track": report.remaining.track_cents,
    }
    if report.remaining.cap_cents is not None:
        limits["job"] = report.remaining.cap_cents
    smallest = min(max(0, v) for v in limits.values())
    budget = _section(text, "Budget remaining")
    match = re.search(r"- Smallest remaining: (USD \d+\.\d\d) \((\w+)\)", budget)
    assert match is not None
    assert match.group(1) == _usd(smallest)
    assert max(0, limits[match.group(2)]) == smallest
    assert f"- Daily: {_usd(max(0, report.remaining.daily_cents))}" in budget
    assert f"- Track: {_usd(max(0, report.remaining.track_cents))}" in budget

    # Req 24.6: total cost, tokens and models cover every attempt.
    totals = _section(text, "Totals")
    cost = sum(i.cost_cents for i in report.items) + report.extra_cost_cents
    tokens_in = sum(i.input_tokens for i in report.items) + report.extra_input_tokens
    tokens_out = sum(i.output_tokens for i in report.items) + report.extra_output_tokens
    models = sorted({i.model for i in report.items} | set(report.extra_models))
    assert f"- Items generated: {len(report.items)}" in totals
    assert f"- Total cost: {_usd(cost)}" in totals
    assert f"- Input tokens: {tokens_in}" in totals
    assert f"- Output tokens: {tokens_out}" in totals
    assert f"- Models: {', '.join(models) if models else 'none'}" in totals
    assert f"- Mode: {'live' if report.live else 'replay'}" in text


@settings(max_examples=200, deadline=None)
@given(report=_reports())
def test_property_20_report_is_complete_and_consistent(report: GenerationReport) -> None:
    text = render_report(report)
    _check_report_text(report, text)
    assert render_report(report) == text


# -- generator end to end -----------------------------------------------------------------------


def _loc(text: str) -> dict[str, str]:
    return {"en": text, "fr": f"{text} (fr)"}


_BRIEF = {
    "title": _loc("Networking"),
    "description": _loc("How computers talk"),
    "learning_outcomes": [_loc("Explain an address")],
}
_OUTLINE = {
    "modules": [
        {
            "title": _loc("Addresses"),
            "lessons": [
                {
                    "title": _loc("What is an IP address"),
                    "summary": _loc("Addresses name hosts"),
                    "concept_titles": [_loc("IP address")],
                },
                {
                    "title": _loc("What is a subnet"),
                    "summary": _loc("Subnets group hosts"),
                    "concept_titles": [_loc("Subnet")],
                },
            ],
        }
    ]
}
_LESSON = {
    "title": _loc("A lesson"),
    "sections": [{"heading": _loc("Intro"), "body": _loc("Some prose."), "commands": []}],
}
_QUIZ = {
    "title": _loc("A quiz"),
    "questions": [
        {
            "prompt_text": _loc("What is 1?"),
            "options": [
                {"text": _loc("One"), "is_correct": True},
                {"text": _loc("Two"), "is_correct": False},
            ],
            "explanation": _loc("Because."),
            "concept_title": _loc("IP address"),
        }
    ],
}


class _Ids:
    def __init__(self) -> None:
        self._n = 0

    def new_id(self) -> str:
        self._n += 1
        return f"run{self._n}"


class _NoLabs:
    def execute(self, lab: object) -> tuple[()]:
        return ()


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "-c",
            "core.autocrlf=false",
            *args,
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout


def _make_repo(root: Path) -> None:
    _git(root, "init", "-q", "-b", "trunk")
    for rel, text in (
        ("lab-images/linux-base/Dockerfile", "FROM scratch\n"),
        (".github/CODEOWNERS", ""),
    ):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "seed")


def _generator(root: Path, selected: int) -> Generator:
    clock = Test_Clock()
    ledger = In_Memory_GenerationLedger(clock)

    def wire(scope: JobScope) -> RunProviders:
        gate, sink = LedgerAttemptGate(scope), LedgerUsageSink(scope)
        responses = {
            "brief": json.dumps(_BRIEF),
            "outline": json.dumps(_OUTLINE),
            "lesson": json.dumps(_LESSON),
            "quiz": json.dumps(_QUIZ),
            "verify_answer": json.dumps({"selected": [selected]}),
        }
        provider = Fake_LLMProvider(responses, gate=gate, sink=sink)
        verifier = VerifyItem(
            independent_llm=provider, sandbox=Fake_SandboxProvider(clock), lab_verifier=_NoLabs()
        )
        return RunProviders(provider, verifier)

    return Generator(
        GeneratorDeps(
            git=SubprocessGitWorkspace(root),
            ledger=ledger,
            clock=clock,
            ids=_Ids(),
            prompts=PromptStore(),
            wire=wire,
            base_branch="trunk",
        )
    )


@pytest.mark.parametrize(
    ("live", "limit", "cap", "selected"),
    [
        (True, None, 2000, 1),  # wrong independent answer: one disagreement per quiz
        (False, 1, None, 0),  # no finding, one unit left over, no cap
        (True, 1, 100, 1),  # small cap is the smallest remaining limit
    ],
)
def test_property_20_generator_report_matches_outcome_and_limits(
    tmp_path: Path, live: bool, limit: int | None, cap: int | None, selected: int
) -> None:
    _make_repo(tmp_path)
    generator = _generator(tmp_path, selected)
    for stage in ("brief", "outline", "lessons"):
        done = generator.run(GenerationRequest(TRACK, stage))
        assert done.exit_code is ExitCode.OK, done.message
        assert done.branch is not None
        _git(tmp_path, "merge", "--ff-only", "-q", done.branch)

    outcome = generator.run(
        GenerationRequest(TRACK, "quizzes", live=live, cost_cap_cents=cap, limit_units=limit)
    )
    assert outcome.branch is not None and outcome.run_id is not None
    text = _git(
        tmp_path, "show", f"{outcome.branch}:{report_path(TRACK, 'quizzes', outcome.run_id)}"
    )

    # Every committed unit is listed as an item (Req 9.1) and nothing else is.
    items_part = _section(text, "Items")
    assert tuple(re.findall(r"^### (.*)$", items_part, re.M)) == tuple(sorted(outcome.committed))
    assert outcome.committed
    assert f"- Items generated: {len(outcome.committed)}" in text

    # The report's finding count is the number the Verifier recorded (Req 9.3).
    assert f"Findings recorded: {outcome.findings}" in text
    listed = re.findall(r"^\d+\. \[", _section(text, "Verification findings"), re.M)
    assert len(listed) == outcome.findings
    if selected == 1:
        assert outcome.findings >= len(outcome.committed)  # at least one disagreement per quiz

    # Discarded units and items not generated match the outcome (Req 9.2).
    for unit in outcome.discarded:
        assert f"`{unit.stable_id}`" in _section(text, "Discarded units")
    for gap in outcome.not_generated:
        assert f"`{gap.stable_id}`" in _section(text, "Items not generated")
    assert (limit is not None) == bool(outcome.not_generated)

    # Remaining Budget is the smallest of daily, track and cap; the fakes spend nothing (Req 9.4).
    expected = [
        domain_config.AI_BUDGET_DAILY_CENTS,
        domain_config.AI_BUDGET_PER_TRACK_CENTS,
        *([cap] if cap is not None else []),
    ]
    assert f"- Smallest remaining: {_usd(min(expected))}" in text
    assert f"- Mode: {'live' if live else 'replay'}" in text
    assert "- Total cost: USD 0.00" in text
    assert re.search(r"^- Models: \S+", text, re.M)
