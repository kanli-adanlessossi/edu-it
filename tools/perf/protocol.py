"""Pure protocol logic for the lab performance measurements (Req 31).

Stdlib only, no I/O. Handles nearest-rank percentile, warm-up discarding,
sample-count minimums, target comparison and the at-most-one re-measurement
rule. The final attempt is the one that counts.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

WARMUP_SAMPLES = 3
MAX_ATTEMPTS = 2  # first measurement + at most one full re-measurement

# name -> (min counted samples, p95 target in seconds)
START_TO_READY = "start_to_ready"
KEYSTROKE = "keystroke_round_trip"
SHELL_CHECK = "shell_check"

MIN_COUNTED = {START_TO_READY: 30, KEYSTROKE: 200, SHELL_CHECK: 1}
TARGETS_SECONDS = {START_TO_READY: 20.0, KEYSTROKE: 0.150, SHELL_CHECK: 15.0}


def raw_required(target: str) -> int:
    """Raw samples to collect: minimum counted plus the discarded warm-ups."""
    return MIN_COUNTED[target] + WARMUP_SAMPLES


def percentile_nearest_rank(values: Sequence[float], p: float) -> float:
    """Nearest-rank percentile: the value at 1-based rank ceil(p/100 * n)."""
    if not values:
        raise ValueError("percentile of an empty sample set")
    if not 0 < p <= 100:
        raise ValueError("p must be in (0, 100]")
    ordered = sorted(values)
    rank = max(1, math.ceil(p / 100 * len(ordered)))
    return ordered[rank - 1]


def discard_warmup(raw: Sequence[float]) -> list[float]:
    return list(raw[WARMUP_SAMPLES:])


@dataclass(frozen=True)
class TargetResult:
    target: str
    raw_count: int
    counted_count: int
    raw_values: list[float]
    counted_values: list[float]
    p95: float | None
    max_value: float | None
    limit: float
    method: str
    enough_samples: bool
    met: bool


def evaluate_target(target: str, raw: Sequence[float], method: str = "") -> TargetResult:
    counted = discard_warmup(raw)
    enough = len(raw) >= raw_required(target) and len(counted) >= MIN_COUNTED[target]
    limit = TARGETS_SECONDS[target]
    p95 = percentile_nearest_rank(counted, 95) if counted else None
    mx = max(counted) if counted else None
    if target == SHELL_CHECK:
        # every shell Check run must be under the limit
        within = mx is not None and mx < limit
    else:
        within = p95 is not None and p95 < limit
    return TargetResult(
        target=target,
        raw_count=len(raw),
        counted_count=len(counted),
        raw_values=list(raw),
        counted_values=counted,
        p95=p95,
        max_value=mx,
        limit=limit,
        method=method,
        enough_samples=enough,
        met=bool(enough and within),
    )


@dataclass(frozen=True)
class Attempt:
    number: int
    results: Mapping[str, TargetResult]

    @property
    def passed(self) -> bool:
        return all(r.met for r in self.results.values())


@dataclass
class ProtocolOutcome:
    attempts: list[Attempt] = field(default_factory=list)

    @property
    def final(self) -> Attempt:
        return self.attempts[-1]

    @property
    def passed(self) -> bool:
        return self.final.passed


def run_protocol(
    collect: Callable[[str, int], Sequence[float]],
    methods: Mapping[str, str] | None = None,
    targets: Sequence[str] = (START_TO_READY, KEYSTROKE, SHELL_CHECK),
) -> ProtocolOutcome:
    """Run attempt 1; if any target missed, run one full re-measurement.

    ``collect(target, raw_count)`` returns that many raw samples (seconds).
    All attempts are retained; the final one counts.
    """
    methods = methods or {}
    outcome = ProtocolOutcome()
    for number in range(1, MAX_ATTEMPTS + 1):
        results = {
            t: evaluate_target(t, collect(t, raw_required(t)), methods.get(t, ""))
            for t in targets
        }
        outcome.attempts.append(Attempt(number, results))
        if outcome.attempts[-1].passed:
            break
    return outcome


def outcome_to_dict(outcome: ProtocolOutcome, machine: Mapping[str, object]) -> dict[str, Any]:
    def res(r: TargetResult) -> dict[str, Any]:
        return {
            "sample_count_raw": r.raw_count,
            "sample_count_counted": r.counted_count,
            "warmup_discarded": WARMUP_SAMPLES,
            "method": r.method,
            "percentile_method": "nearest-rank",
            "p95_seconds": r.p95,
            "max_seconds": r.max_value,
            "limit_seconds": r.limit,
            "enough_samples": r.enough_samples,
            "met": r.met,
            "values_seconds": r.raw_values,
        }

    return {
        "machine": dict(machine),
        "attempts": [
            {"attempt": a.number, "passed": a.passed,
             "targets": {k: res(v) for k, v in a.results.items()}}
            for a in outcome.attempts
        ],
        "final_attempt": outcome.final.number,
        "passed": outcome.passed,
    }
