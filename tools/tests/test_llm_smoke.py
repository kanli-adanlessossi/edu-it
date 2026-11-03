"""Unit tests for the argument and refusal logic of ``tools/llm_smoke.py`` (task 4.12).

The script itself makes a real paid call and is run by hand only. These tests use a stub
provider through the script's seams, so they make no network call.
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest
from edu_it_domain.ports.generation_ledger import JobStatus
from edu_it_domain.ports.llm_provider import (
    AttemptUsage,
    BudgetReachedError,
    LLMInput,
    OutputSchema,
    PromptRef,
    StructuredOutput,
)
from edu_it_infrastructure.fakes.generation_ledger import In_Memory_GenerationLedger

_PATH = Path(__file__).resolve().parents[1] / "llm_smoke.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("llm_smoke_under_test", _PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


smoke = _load()

LIVE_ENV = {"EDU_IT_ENV": "development", "LLM_PROVIDER_BACKEND": "live"}


class _Provider:
    def __init__(self, scope: object, cost: int, *, reached: str | None = None) -> None:
        self._scope = scope
        self._cost = cost
        self._reached = reached

    def complete(
        self, prompt_version: PromptRef, input: LLMInput, schema: OutputSchema, max_retries: int
    ) -> StructuredOutput:
        assert max_retries == 0  # one attempt only
        if self._reached is not None:
            raise BudgetReachedError(self._reached)
        usage = AttemptUsage("m", 10, 5, self._cost, "ok")
        self._scope.ledger.record_attempt(self._scope.job, usage)  # type: ignore[attr-defined]
        return StructuredOutput(schema.validate('{"ok": true}'), prompt_version, "m", (usage,))


def _run(
    argv: list[str], env: dict[str, str], cost: int = 1, reached: str | None = None
) -> tuple[int, In_Memory_GenerationLedger]:
    ledgers: list[In_Memory_GenerationLedger] = []

    def ledger_factory(_s: object, clock: object) -> In_Memory_GenerationLedger:
        ledgers.append(In_Memory_GenerationLedger(clock))  # type: ignore[arg-type]
        return ledgers[0]

    code = smoke.main(
        argv,
        env=env,
        ledger_factory=ledger_factory,
        provider_factory=lambda _s, scope: _Provider(scope, cost, reached=reached),
    )
    return code, ledgers[0] if ledgers else None  # type: ignore[return-value]


@pytest.mark.parametrize("bad", ["0", "11", "-1", "abc", "1.5"])
def test_cost_cap_outside_a_few_cents_is_rejected(bad: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        smoke.parse_cost_cap(bad)


def test_cost_cap_inside_range_is_accepted() -> None:
    assert smoke.parse_cost_cap("3") == 3


def test_refuses_without_live_backend(capsys: pytest.CaptureFixture[str]) -> None:
    code, ledger = _run(
        ["--cost-cap-cents", "3"], {"EDU_IT_ENV": "development", "LLM_PROVIDER_BACKEND": "fake"}
    )
    assert code == smoke.EXIT_REFUSED
    assert ledger is None  # nothing opened, no call
    assert "LLM_PROVIDER_BACKEND=live" in capsys.readouterr().err


def test_refuses_the_placeholder_key(capsys: pytest.CaptureFixture[str]) -> None:
    env = {**LIVE_ENV, "LLM_PROVIDER_API_KEY": "change-me"}
    code, ledger = _run(["--cost-cap-cents", "3"], env)
    assert code == smoke.EXIT_REFUSED
    assert ledger is None
    assert "placeholder" in capsys.readouterr().err


def test_success_prints_the_cost_and_closes_the_job(capsys: pytest.CaptureFixture[str]) -> None:
    env = {**LIVE_ENV, "LLM_PROVIDER_API_KEY": "real-looking-key"}
    code, ledger = _run(["--cost-cap-cents", "3"], env, cost=2)
    out = capsys.readouterr()
    assert code == smoke.EXIT_OK
    assert "cost: 2 cent(s) of a 3 cent Cost_Cap" in out.out
    assert "real-looking-key" not in out.out + out.err
    assert ledger is not None


def test_budget_stop_is_a_failure_with_no_cost(capsys: pytest.CaptureFixture[str]) -> None:
    env = {**LIVE_ENV, "LLM_PROVIDER_API_KEY": "real-looking-key"}
    code, _ = _run(["--cost-cap-cents", "3"], env, reached="daily")
    out = capsys.readouterr()
    assert code == smoke.EXIT_FAILED
    assert "daily Budget" in out.err
    assert "cost: 0 cent(s)" in out.out
    assert JobStatus.BUDGET_REACHED.value == "budget_reached"
