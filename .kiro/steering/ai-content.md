---
inclusion: fileMatch
fileMatchPattern: "prompts/**|evals/**|**/prompts/**|**/evals/**|**/{generation,generate,generator}/**|**/*{generation,generate,generator}*|**/*{llm,LLM}*|**/*{LLMProvider,llm_provider}*|**/{review,reviews,advisor}/**|**/*{review,advisor,variant}*|**/*{path_recommendation,path-recommendation,path_advisor,path-advisor}*|**/*{generation_job,generation-job,budget_ledger,budget-ledger,item_statistic,item-statistic}*"
---

# AI Content Generation and Review

This steering file is the single source of truth for the EDU-IT AI content generation and review
rules. It is included conditionally (fileMatch inclusion) when working on prompts, evals,
generation, LLM, and review/advisor code (see the `fileMatchPattern` in the front matter). The
AI content generation and review rules below are transcribed verbatim from the project rules;
all numbers and normative wording are preserved unchanged. Where a rule is not self-explanatory,
a one-line reason is added and marked `Reason:`.

## Generation Pipeline

- Content is drafted by AI and approved by experts.
  Reason: AI generation is ahead-of-time; an expert reviewer approves before content is served.
- The LLM provider is used for ahead-of-time content generation only, never on a learner's
  request path.
  Reason: keeps the learner experience independent of LLM latency and availability.
- `apps/worker`: AI generation jobs, variant-pool refill, spaced-review and decay computation.

## LLM Provider Port

- LLM provider: Behind `LLMProvider`: ahead-of-time content generation only, never on a
  learner's request path.
- LLM provider and data residency: provider behind `LLMProvider`, DPA and EU residency.
- Specify what data is allowed in LLM requests.
  Reason: the AI-content design states which data may be included in LLM requests so personal
  data is not sent to the provider beyond what is allowed.

The `LLMProvider` interface signatures (illustrative pseudocode; the authoritative versions live
in `docs/AI-CONTENT-DESIGN.md` (D6)):

```pascal
INTERFACE LLMProvider                  // see docs/AI-CONTENT-DESIGN.md (D6)
  FUNCTION complete(prompt_version, input, schema, max_retries) RETURNS StructuredOutput
  // structured output validated against content schemas; bounded retries; records model, tokens, cost
END INTERFACE
```

## Review Gates and Variant Lifecycle

- A variant is served only after it is Verified (schema + independent answer + sandbox checks)
  and Approved by a reviewer; only approved variants are served.
- A variant is Withdrawn on abnormal correct-rate/time or when reported.
- Variant lifecycle states and transitions:
  - `Generated` → `Verified`: schema + independent answer + sandbox checks.
  - `Generated` → `Flagged`: disagreement / failed verification.
  - `Verified` → `PendingReview`: added to pool marked pending.
  - `PendingReview` → `Approved`: reviewer approves.
  - `PendingReview` → `Rejected`: reviewer rejects.
  - `Approved` → `Served`: only approved variants are served.
  - `Served` → `Withdrawn`: abnormal correct-rate/time or reported.

## Reviewer Roles and Project Review

- `role_assignment` (learner, reviewer with per-track expertise, author, admin).
- Project submissions are reviewed through the `ProjectReviewer` extension point: mentor, peer
  or AI-assisted review.

## Path Advisor

- AI/review ops entities: `review_queue_item`, `variant`, `generation_job`, `item_statistic`,
  `path_recommendation`, `budget_ledger`.
- The path advisor falls back to rule-based ordering.
  Reason: path recommendation degrades to a deterministic rule-based order when the LLM path
  advisor is unavailable or fails.

## Provenance and Versioning

- Provenance is recorded on every content item: `origin`, `model`, `prompt_id`,
  `prompt_version`, `generation_date`, `reviewed_by`, `reviewed_at`; plus a `content_version`
  hash and a `stable_id`.

## Failure Scenarios

### LLM timeout or invalid output

- IF an LLM request times out or returns invalid output, retries are bounded by the maximum
  stated in `docs/AI-CONTENT-DESIGN.md`, invalid output is never auto-corrected, the item is
  flagged for review, the path advisor falls back to rule-based ordering, and job cost and
  tokens are still recorded.

### Content sync during active use

- IF content is synced while an item is in active use, the upsert is idempotent keyed by stable
  ID, edits never reset progress, existing completions are kept, new attempts use the new
  version, and removed items are archived rather than deleted.
