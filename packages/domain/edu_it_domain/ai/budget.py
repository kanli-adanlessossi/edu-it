"""Budget gate for generation attempts (Requirements 10.1 to 10.5).

Pure function over ledger sums. The gate is evaluated before every attempt,
including retries, and has no switch to disable it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from edu_it_domain.config import AI_BUDGET_DAILY_CENTS, AI_BUDGET_PER_TRACK_CENTS


class BudgetKind(StrEnum):
    """The Budget that was reached."""

    DAILY = "daily"
    TRACK = "track"
    JOB = "job"


@dataclass(frozen=True, slots=True)
class BudgetDecision:
    """Outcome of one gate evaluation; ``reached`` names the first Budget hit."""

    reached: BudgetKind | None

    @property
    def allowed(self) -> bool:
        return self.reached is None


def check_attempt(
    *,
    daily_spent_cents: int,
    track_spent_cents: int,
    job_spent_cents: int = 0,
    cost_cap_cents: int | None = None,
    daily_limit_cents: int = AI_BUDGET_DAILY_CENTS,
    track_limit_cents: int = AI_BUDGET_PER_TRACK_CENTS,
) -> BudgetDecision:
    """Decide whether one more model attempt may start.

    A Budget is reached when its spend is at or above its limit. The daily
    Budget is checked first, then the per-track Budget, then the job Cost_Cap
    (only when one was supplied).
    """
    if daily_spent_cents >= daily_limit_cents:
        return BudgetDecision(BudgetKind.DAILY)
    if track_spent_cents >= track_limit_cents:
        return BudgetDecision(BudgetKind.TRACK)
    if cost_cap_cents is not None and job_spent_cents >= cost_cap_cents:
        return BudgetDecision(BudgetKind.JOB)
    return BudgetDecision(None)


__all__ = ["BudgetDecision", "BudgetKind", "check_attempt"]
