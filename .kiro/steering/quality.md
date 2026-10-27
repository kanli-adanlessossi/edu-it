---
inclusion: always
---

# Quality Bars and Testing

This steering file is the single source of truth for the EDU-IT quality bars and testing rules.
It is included in every interaction (always-on inclusion). The quality-bar and testing rules
below are transcribed verbatim from the project rules; all numbers and normative wording are
preserved unchanged. Where a rule is not self-explanatory, a one-line reason is added and marked
`Reason:`.

## Testability and Layering Discipline

- Dependencies point inward. The domain imports no framework and receives time through an
  injected `Clock`.
  Reason: a framework-free domain with injected time is deterministic and unit-testable without
  external systems.
- Every external system sits behind an interface with a fake.
- Ports defined in the domain: `SandboxProvider`, `Mailer`, `ObjectStorage`, `Clock`,
  `TaskQueue`, `LLMProvider`, and the `ProjectReviewer` extension point. Each has a fake.
- `Clock` is injected; the domain never reads the wall clock directly.
- Business numbers live in `domain/config.py` (thresholds, points, intervals, timeouts).
  Reason: keeps every tunable business constant in one framework-free location so the domain
  stays pure and testable.

## Grading Bounds

- Grading is bounded: results within a 15 s target, executed at most once per 10 s per session.
- If a grading check does not return within the 15 s target, the timeout yields a translated
  "could not complete" feedback instead of raw output, and the run is recorded with a failed
  status.

## LLM Output Quality

- LLM structured output is validated against content schemas; retries are bounded; the provider
  records model, tokens, and cost.
- Invalid output is never auto-corrected; the item is flagged for review, and the path advisor
  falls back to rule-based ordering; job cost and tokens are still recorded.
- The LLM provider is used for ahead-of-time content generation only, never on a learner's
  request path.
  Reason: keeps the learner experience independent of LLM latency and availability.

## Content Verification Gates

- A variant is served only after it is Verified (schema + independent answer + sandbox checks)
  and Approved by a reviewer; only approved variants are served.
- A variant is Withdrawn on abnormal correct-rate/time or when reported.

## Error-Reporting Quality

- Error format: RFC 9457 problem details (`type`, `title`, `status`, `detail`, `instance`).
- Learner-facing errors are translated and actionable; details stay in logs.

## Data-Integrity Quality Bars

- Content sync upsert is idempotent, keyed by stable ID; edits never reset progress; existing
  completions are kept; new attempts use the new version; removed items are archived rather than
  deleted.
- Duplicate quiz submissions are deduped by an idempotency key; re-answering the same question
  within 24 hours of the first answer yields no additional mastery gain; the best attempt still
  counts for completion.

## Verification Checks (document quality)

Because the design-foundation phase produces documents, "tests" are verification checks run and
recorded in `docs/reports/phase-0.md`:

- Front-matter check: each steering file parses and carries valid front matter (and a
  `fileMatch` where the rules require one).
- Mermaid render check: each diagram block is validated (e.g. with the Mermaid CLI) so it
  renders without error.
- Traceability completeness: every rule line in the spec request appears in the table with its
  target steering file.
- Link integrity: every `#[[file:.kiro/steering/<file>.md]]` reference resolves to an existing
  file.
- ADR completeness: each ADR contains context, decision and consequences sections.
- No-code check: confirm no files were created under `apps/`, `lab-images/`, `content/`,
  `prompts/`, `evals/`, or `deploy/`.
