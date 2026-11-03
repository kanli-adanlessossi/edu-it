"""Worker tests: refill budgets and retry bound; failure is silent (task 15.5).

Complements ``test_ai_worker_refill.py`` (which covers a budget stop). No model or network is used.

**Validates: Requirements 13.5, 13.10**
"""

from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path

import pytest
from edu_it_domain import config as cfg
from edu_it_domain.ports.llm_provider import (
    AttemptUsage,
    BudgetReachedError,
    LLMInput,
    MalformedOutputError,
    OutputSchema,
    PromptRef,
    ProviderUnavailableError,
    StructuredOutput,
)
from edu_it_infrastructure.fakes import Console_Mailer
from edu_it_worker.ai.composition import build_ai_worker
from edu_it_worker.ai.jobs import REFILL_PARENT_JOB

sys.path.insert(0, str(Path(__file__).parent))
from test_ai_worker_refill import (  # type: ignore[import-not-found]
    _draft,
    _parent,
    _Parents,
    _World,
)


class _RecordingLLM:
    """Scripted model that records the retry bound passed on every call."""

    def __init__(self, script: list[str | Exception]) -> None:
        self._script = list(script)
        self.max_retries: list[int] = []

    def complete(
        self, prompt_version: PromptRef, input: LLMInput, schema: OutputSchema, max_retries: int
    ) -> StructuredOutput:
        self.max_retries.append(max_retries)
        step = self._script.pop(0)
        if isinstance(step, Exception):
            raise step
        usage = AttemptUsage("scripted", 1, 1, 0, "ok")
        return StructuredOutput(schema.validate(step), prompt_version, "scripted", (usage,))


def _run(world: _World, llm: _RecordingLLM) -> None:
    world.llm = llm
    world.worker.consumers[REFILL_PARENT_JOB]({"parent_stable_id": "q1"})


def test_every_model_call_of_a_refill_job_carries_the_configured_retry_bound() -> None:
    """Validates: Requirement 13.5 (the retry bound applies to every draft)."""
    world = _World(_Parents(_parent("q1")))
    llm = _RecordingLLM([_draft(1), _draft(2), _draft(3)])

    _run(world, llm)

    assert llm.max_retries == [cfg.LLM_MAX_RETRIES] * 3


def test_each_refill_job_opens_its_own_budget_scope() -> None:
    """Validates: Requirement 13.5 (budgets are applied per job, not once per process)."""
    world = _World(_Parents(_parent("q1")))
    _run(world, _RecordingLLM([_draft(1), _draft(2), _draft(3)]))
    _run(world, _RecordingLLM([BudgetReachedError("daily")]))

    job_scopes = world.scopes[1:]  # the first scope is the composition preflight
    assert len(job_scopes) == 2
    assert job_scopes[0].job != job_scopes[1].job


@pytest.mark.parametrize(
    "failure",
    [
        BudgetReachedError("daily"),
        MalformedOutputError("bad"),
        ProviderUnavailableError("down"),
    ],
)
def test_failed_refill_sends_no_mail_and_enqueues_nothing(
    failure: Exception, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Validates: Requirement 13.10 (no alert, no email, no escalation, no new job)."""
    sent: list[object] = []
    monkeypatch.setattr(Console_Mailer, "send", lambda self, mail: sent.append(mail))
    world = _World(_Parents(_parent("q1")))
    llm = _RecordingLLM([failure])

    _run(world, llm)  # does not raise

    assert llm.max_retries == [cfg.LLM_MAX_RETRIES]  # stopped at the first failed draft
    assert sent == []
    assert world.queue.pending == ()
    assert world.tasks.page(["track-1"], None, 50).items == ()
    assert world.variants.count_approved("q1") == 0


def test_failure_after_some_drafts_keeps_them_pending_and_stops_without_follow_up() -> None:
    """Validates: Requirements 13.5, 13.10 (a partial run ends quietly)."""
    world = _World(_Parents(_parent("q1")))
    llm = _RecordingLLM([_draft(1), BudgetReachedError("daily")])

    _run(world, llm)

    assert len(world.tasks.page(["track-1"], None, 50).items) == 1
    assert world.queue.pending == ()
    assert len(llm.max_retries) == 2  # no draft is attempted after the stop


def test_the_ai_worker_has_no_mail_dependency() -> None:
    """Validates: Requirement 13.10 (the worker cannot send mail, so a failure cannot)."""
    parameters = inspect.signature(build_ai_worker).parameters

    assert not [name for name in parameters if "mail" in name.lower()]
    assert json.dumps(sorted(parameters))  # signature is introspectable
