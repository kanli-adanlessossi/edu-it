"""Unit tests for the eval runner and ``edu-it-eval`` (ai-content task 9.3).

No model is called and no network is used: the provider is a counting stub and the ledger is
the in-memory fake.

**Validates: Requirements 11.3, 11.4**
"""

from __future__ import annotations

import json
from pathlib import Path

import edu_it_worker.ai.eval_runner as runner_module
import pytest
from edu_it_domain.config import AI_BUDGET_DAILY_CENTS
from edu_it_domain.ports.generation_ledger import JobKind, JobRef, NewJob
from edu_it_domain.ports.llm_provider import (
    AttemptUsage,
    BudgetReachedError,
    LLMInput,
    OutputSchema,
    PromptRef,
    StructuredOutput,
)
from edu_it_infrastructure.composition import CompositionSettings, LLMBackend
from edu_it_infrastructure.fakes.clock import Test_Clock
from edu_it_infrastructure.fakes.generation_ledger import In_Memory_GenerationLedger
from edu_it_worker.ai.cli import eval_entry
from edu_it_worker.ai.eval_runner import EvalRequest, run_eval
from edu_it_worker.ai.generator import ExitCode

SUITE = {
    "cases": [
        {
            "id": "answer-key.q1",
            "kind": "answer-key",
            "locale": "en",
            "question": "Pick the first.",
            "options": ["a", "b"],
            "expected": [0],
        }
    ]
}


class _Stub:
    """A provider that counts calls and either answers ``[0]`` or raises ``error``."""

    def __init__(self, error: Exception | None = None) -> None:
        self.calls = 0
        self._error = error

    def complete(
        self, prompt_version: PromptRef, input: LLMInput, schema: OutputSchema, max_retries: int
    ) -> StructuredOutput:
        self.calls += 1
        if self._error is not None:
            raise self._error
        usage = AttemptUsage("stub", 1, 1, 0, "ok")
        return StructuredOutput(
            schema.validate('{"selected": [0]}'), prompt_version, "stub", (usage,)
        )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    suites = tmp_path / "evals" / "suites"
    suites.mkdir(parents=True)
    (suites / "mini.json").write_text(json.dumps(SUITE), encoding="utf-8")
    return tmp_path


def _settings(env: str) -> CompositionSettings:
    return CompositionSettings.from_env({"EDU_IT_ENV": env})


def _full_ledger(clock: Test_Clock) -> In_Memory_GenerationLedger:
    """A ledger whose spend today already equals the daily Budget."""
    ledger = In_Memory_GenerationLedger(clock)
    job = ledger.open_job(NewJob("earlier", JobKind.STAGE))
    ledger.record_attempt(job, AttemptUsage("m", 1, 1, AI_BUDGET_DAILY_CENTS, "ok"))
    return ledger


@pytest.fixture
def providers(monkeypatch: pytest.MonkeyPatch) -> list[tuple[LLMBackend, _Stub]]:
    """Replace provider construction; records the backend each provider was built for."""
    built: list[tuple[LLMBackend, _Stub]] = []

    def fake_build(settings: CompositionSettings, scope: object, **_: object) -> _Stub:
        stub = _Stub()
        built.append((settings.llm_provider_backend, stub))
        return stub

    monkeypatch.setattr(runner_module, "build_llm_provider", fake_build)
    return built


def test_replay_run_in_test_environment_uses_replay_backend(
    repo: Path, providers: list[tuple[LLMBackend, _Stub]]
) -> None:
    """Under EDU_IT_ENV=test a run without --live selects the Replay_Provider.

    **Validates: Requirements 11.3**
    """
    outcome = run_eval(
        _settings("test"), EvalRequest(suite="mini"), repo=repo, report_dir=repo / "out"
    )
    assert outcome.exit_code is ExitCode.OK
    assert {backend for backend, _ in providers} == {LLMBackend.REPLAY}
    assert (repo / "out" / "mini.replay.json").exists()


def test_live_refused_in_test_environment(
    repo: Path, providers: list[tuple[LLMBackend, _Stub]]
) -> None:
    """--live with EDU_IT_ENV=test is refused: usage exit, no provider, no job, no report.

    **Validates: Requirements 11.3**
    """
    ledger = In_Memory_GenerationLedger(Test_Clock())
    outcome = run_eval(
        _settings("test"),
        EvalRequest(suite="mini", live=True),
        repo=repo,
        report_dir=repo / "out",
        ledger=ledger,
    )
    assert outcome.exit_code is ExitCode.USAGE
    assert "EDU_IT_ENV=test" in outcome.message
    assert providers == []
    assert not (repo / "out").exists()


def test_eval_entry_live_in_test_environment_exits_2(
    repo: Path,
    providers: list[tuple[LLMBackend, _Stub]],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``edu-it-eval --live`` with EDU_IT_ENV=test exits 2 and makes no model call.

    **Validates: Requirements 11.3**
    """
    monkeypatch.setenv("EDU_IT_ENV", "test")
    monkeypatch.setenv("EDU_IT_REPO_ROOT", str(repo))
    assert eval_entry(["--suite", "mini", "--live"]) == 2
    assert "refusing to start" in capsys.readouterr().err
    assert providers == []


def test_live_daily_budget_reached_exits_4_without_job_or_model_call(
    repo: Path, providers: list[tuple[LLMBackend, _Stub]]
) -> None:
    """--live with the daily Budget spent exits 4 before any job or model call.

    **Validates: Requirements 11.4**
    """
    clock = Test_Clock()
    ledger = _full_ledger(clock)
    outcome = run_eval(
        _settings("development"),
        EvalRequest(suite="mini", live=True),
        repo=repo,
        report_dir=repo / "out",
        clock=clock,
        ledger=ledger,
    )
    assert outcome.exit_code is ExitCode.BUDGET
    assert "daily" in outcome.message
    assert outcome.report is None
    # Only the preflight probe was built; it was never called, and no job was opened.
    assert all(stub.calls == 0 for _, stub in providers)
    assert {b for b, _ in providers} == {LLMBackend.LIVE}
    assert ledger.spent_today(clock.now().replace(hour=0, minute=0)) == AI_BUDGET_DAILY_CENTS
    assert not (repo / "out").exists()
    with pytest.raises(ValueError, match="unknown job"):
        ledger.job_totals(JobRef("none"))


def test_eval_entry_live_daily_budget_reached_exits_4(
    repo: Path,
    providers: list[tuple[LLMBackend, _Stub]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``edu-it-eval --live`` with the daily Budget spent exits 4 with no model call.

    **Validates: Requirements 11.4**
    """
    monkeypatch.setenv("EDU_IT_ENV", "development")
    monkeypatch.setenv("EDU_IT_REPO_ROOT", str(repo))
    monkeypatch.setattr(
        runner_module, "build_generation_ledger", lambda _s, clock: _full_ledger(clock)
    )
    assert eval_entry(["--suite", "mini", "--live"]) == 4
    assert all(stub.calls == 0 for _, stub in providers)


def test_live_cost_cap_reached_exits_4_and_stops_the_cases(repo: Path) -> None:
    """A Cost_Cap reached during a live run exits 4; the case is reported as not run.

    **Validates: Requirements 11.4**
    """
    stub = _Stub(error=BudgetReachedError("job"))
    outcome = run_eval(
        _settings("development"),
        EvalRequest(suite="mini", live=True, cost_cap_cents=1),
        repo=repo,
        report_dir=repo / "out",
        clock=Test_Clock(),
        ledger=In_Memory_GenerationLedger(Test_Clock()),
        provider_factory=lambda _scope: stub,
    )
    assert outcome.exit_code is ExitCode.BUDGET
    assert "job" in outcome.message
    assert outcome.report is not None
    assert outcome.report.passed == 0
    assert "not run" in outcome.report.outcomes[0].detail


def test_non_positive_cost_cap_is_a_usage_error(repo: Path) -> None:
    """A Cost_Cap below one cent is refused before any provider or job exists.

    **Validates: Requirements 11.4**
    """
    outcome = run_eval(
        _settings("development"),
        EvalRequest(suite="mini", live=True, cost_cap_cents=0),
        repo=repo,
        report_dir=repo / "out",
    )
    assert outcome.exit_code is ExitCode.USAGE
