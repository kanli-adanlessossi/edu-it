# ADR 0007: LLM Provider and Data Residency

- **ID:** A7
- **Status:** Accepted
- **Date:** 2025-01-01
- **Decision topic:** LLM provider and data residency (provider behind `LLMProvider`, DPA, EU residency)

## Context

Content is drafted by AI and approved by experts. The LLM provider is used for ahead-of-time
content generation only, never on a learner's request path, which keeps the learner experience
independent of LLM latency and availability. Because generation jobs may process content and
must record provenance (`origin`, `model`, `prompt_id`, `prompt_version`, `generation_date`,
`reviewed_by`, `reviewed_at`) and cost, the platform needs a single provider boundary it can
test against and swap, plus a clear data-handling and residency posture so personal data is not
sent to the provider beyond what is allowed.

This decision is grounded in the AI content generation and review rules and the privacy rules
captured in steering — see #[[file:.kiro/steering/ai-content.md]] and
#[[file:.kiro/steering/auth-privacy.md]] — and is detailed in `docs/AI-CONTENT-DESIGN.md` (D6),
which documents the `LLMProvider` signatures, review gates, and what data is allowed in LLM
requests.

## Decision

The LLM provider sits behind the `LLMProvider` port (provider behind `LLMProvider`, DPA and EU
residency): all generation goes through `LLMProvider`, which is used for ahead-of-time content
generation only and never on a learner's request path. A Data Processing Agreement (DPA) governs
the provider relationship and EU data residency applies. The `LLMProvider.complete` call
validates structured output against content schemas, applies bounded retries, and records model,
tokens, and cost. Only the data allowed per `docs/AI-CONTENT-DESIGN.md` (D6) is included in LLM
requests; personal data is not sent to the provider beyond what is allowed. On timeout or invalid
output, retries are bounded by the maximum stated in `docs/AI-CONTENT-DESIGN.md`, invalid output
is never auto-corrected, the item is flagged for review, the path advisor falls back to
rule-based ordering, and job cost and tokens are still recorded.

## Consequences

Routing all LLM use through the `LLMProvider` port keeps the provider swappable behind a stable
interface, lets tests run against a fake provider with no network calls, and confines generation
to an ahead-of-time path so no learner request ever blocks on the LLM. The DPA and EU-residency
commitment constrain provider selection to vendors that can meet those terms, which narrows the
available options but satisfies the privacy posture in #[[file:.kiro/steering/auth-privacy.md]].
Recording model, tokens, and cost on every `generation_job` (and in `budget_ledger`) makes
spend auditable, and the documented fallback to rule-based ordering keeps the path advisor
available when the LLM is not. This decision flows back into
#[[file:.kiro/steering/ai-content.md]] and #[[file:.kiro/steering/auth-privacy.md]] per
#[[file:.kiro/steering/working-agreements.md]].
