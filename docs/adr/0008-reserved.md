# ADR 0008: Reserved

- **ID:** A8
- **Status:** Reserved
- **Date:** 2025-01-01
- **Decision topic:** Reserved (reserved topic)

## Context

This ADR is the reserved record in the ADR set (A1–A8). The topic is reserved for future use: it
is produced now so that exactly eight ADR documents exist under `docs/adr/`, each addressing
exactly one of the eight decision topics (versions, translation storage, task queue, session
storage, MDX rendering, decay computation, LLM provider and data residency, and the one reserved
topic), per Requirement 3.2 and `docs/ARCHITECTURE.md` (D1)'s Deliverables Map. No subject matter
has been assigned to this record yet; it holds the eighth slot until a future decision claims it.

## Decision

No decision is recorded yet. When a future architecture decision is assigned to this slot, it
will be recorded here with full context, decision, and consequences prose, and the change will
flow back into the relevant steering file in the same change per
#[[file:.kiro/steering/working-agreements.md]].

## Consequences

Keeping A8 as a reserved record preserves the fixed cardinality of exactly eight ADRs
(A1–A8) so the ADR completeness and cardinality checks in the phase-0 report
(`docs/reports/phase-0.md`, R1) pass without a gap in numbering. Until a topic is assigned, this
record carries no architectural commitment and imposes no constraint on implementation; the only
effect is that the eighth ADR slot is explicitly accounted for rather than missing.
