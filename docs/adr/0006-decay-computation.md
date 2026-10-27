# ADR 0006: Decay Computation — Deterministic Clock-Based Strategy

- **ID:** A6
- **Status:** Accepted
- **Date:** 2025-01-01
- **Decision topic:** Decay computation (deterministic clock-based decay strategy)

## Context

Mastery is tracked as `mastery` (0–100 per user × concept). A concept enters the spaced review
schedule on first reaching Practiced, and mastery decays while a review is overdue. The domain
layer imports no framework and receives time through an injected `Clock`, so decay must never be
read from the wall clock directly; it must be reproducible given the same inputs and the same
clock reading. The platform needs decay to behave identically in production, in tests, and in
the worked mastery example in `docs/RULES-REVIEW.md` (D7), which requires the computation to be
deterministic rather than dependent on when or how often it runs.

This decision is grounded in the domain rules captured in steering — see
#[[file:.kiro/steering/domain-rules.md]] — and in the layering rules
(#[[file:.kiro/steering/structure.md]], injected `Clock`). The high-level mastery state machine
and the decay note appear in `docs/ARCHITECTURE.md` (D1), and the review schedule tables appear
in `docs/DATA-MODEL.md` (D3).

## Decision

Decay is computed deterministically from the injected `Clock`: `Decay = -1/day overdue`,
deterministic from the clock. Decay is applied by the `apps/worker` spaced-review and decay
computation job, which reads the current instant from the injected `Clock` rather than the wall
clock, so the same schedule state and the same clock reading always yield the same mastery
result. Decay transitions a concept `Mastered` → `Proficient` and `Proficient` → `Practiced`,
but decay never drops mastery below 30 once Practiced, and decay never relocks content; it only
re-queues for review.

## Consequences

Because decay depends only on the clock reading and the review-schedule state, the computation is
idempotent with respect to how often the worker runs: running it multiple times for the same
instant produces the same result, and a skipped run is recovered on the next run without
double-counting. Tests inject a fake `Clock` to reproduce any decay scenario exactly, and the D7
worked example can trace mastery points, a hint reveal, and an overdue review with reproducible
numbers. The invariants (never below 30 once Practiced; never relocks content, only re-queues for
review) are preserved by construction. The trade-off is that decay is strictly linear
(`-1/day overdue`) rather than curve-based; any future change to the decay curve is itself a new
decision that must flow back into #[[file:.kiro/steering/domain-rules.md]] per
#[[file:.kiro/steering/working-agreements.md]].
