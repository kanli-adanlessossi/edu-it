"""Ledger-backed AttemptGate and UsageSink (ai-content Req 1.4, 10.1 to 10.5, 12.1).

Both are bound to one job by construction: the gate reads the spend sums of the
:class:`GenerationLedger` and applies the pure domain ``check_attempt``; the sink
appends every attempt to the same ledger. Time comes only from the injected clock.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time

from edu_it_domain.ai.budget import check_attempt
from edu_it_domain.config import AI_BUDGET_DAILY_CENTS, AI_BUDGET_PER_TRACK_CENTS
from edu_it_domain.ports.clock import Clock
from edu_it_domain.ports.generation_ledger import GenerationLedger, JobRef
from edu_it_domain.ports.llm_provider import AttemptUsage, BudgetReachedError

__all__ = ["JobScope", "LedgerAttemptGate", "LedgerUsageSink"]


@dataclass(frozen=True, slots=True)
class JobScope:
    """The job a provider is bound to: ledger, job, clock and budget scope."""

    ledger: GenerationLedger
    job: JobRef
    clock: Clock
    track_stable_id: str | None = None
    cost_cap_cents: int | None = None
    daily_limit_cents: int = AI_BUDGET_DAILY_CENTS
    track_limit_cents: int = AI_BUDGET_PER_TRACK_CENTS


class LedgerAttemptGate:
    """Raises ``BudgetReachedError`` before an attempt when a Budget is reached."""

    def __init__(self, scope: JobScope) -> None:
        self._scope = scope

    def before_attempt(self, attempt: int) -> None:
        scope = self._scope
        now = scope.clock.now()
        day_start = datetime.combine(now.date(), time.min, tzinfo=now.tzinfo)
        track_spent = (
            scope.ledger.spent_for_track(scope.track_stable_id)
            if scope.track_stable_id is not None
            else 0
        )
        decision = check_attempt(
            daily_spent_cents=scope.ledger.spent_today(day_start),
            track_spent_cents=track_spent,
            job_spent_cents=scope.ledger.spent_for_job(scope.job),
            cost_cap_cents=scope.cost_cap_cents,
            daily_limit_cents=scope.daily_limit_cents,
            track_limit_cents=scope.track_limit_cents,
        )
        if decision.reached is not None:
            raise BudgetReachedError(decision.reached.value)


class LedgerUsageSink:
    """Appends every attempt to the job's ledger."""

    def __init__(self, scope: JobScope) -> None:
        self._scope = scope

    def record(self, usage: AttemptUsage) -> None:
        self._scope.ledger.record_attempt(self._scope.job, usage)
