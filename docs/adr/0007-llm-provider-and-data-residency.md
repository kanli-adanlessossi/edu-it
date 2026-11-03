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

## Addendum 1: Vendor selection (decision D1)

- **Status:** Accepted. Provider chosen and the contractual and deployment facts confirmed by the owner, including the EU routing check.
- **Gate for:** ai-content tasks 4.4, 4.12 and 28.1. They were blocked until the EU routing check was recorded and the status set to Accepted. Both are now done.

### Decision

The live provider is **Anthropic Claude on Amazon Bedrock in an EU region**, reached through the
existing `LiveTransport` with a vendor adapter registered as `bedrock_claude` in
`edu_it_infrastructure/llm/vendors/`. The adapter uses the Bedrock Converse API and authenticates
with a single Bedrock API key sent as a bearer token. Nothing else in the code changes: the host
binding, redirect policy, timeouts, price-based cost, record mode and the engine's validation,
retry and recording stay in the shared layers (ai-content Req 1.7, 1.8, 1.10).

The DPA is the AWS Data Processing Addendum that already applies to the owner's AWS account. No new
vendor contract is needed.

### Confirmed facts to record (owner to fill in)

| Item | Value |
| --- | --- |
| Bedrock runtime endpoint host | `bedrock-runtime.eu-central-1.amazonaws.com` (confirmed by owner) |
| Region the requests are sent to | `eu-central-1` (confirmed by owner) |
| Model and inference profile ID | `eu.anthropic.claude-sonnet-4-6` (geographic EU inference profile, confirmed by owner). EU routing verified by owner on 2026-11-02. Destination regions from get-inference-profile: eu-north-1, eu-west-3, eu-south-1, eu-south-2, eu-west-1, eu-central-1 (all EU). One test call was processed in eu-west-1 (CloudTrail dditionalEventData.inferenceRegion). Re-check if AWS changes the profile or the model version. |
| Model version | v1, dated 17 Feb 2026 18:00 GMT as shown in the console (confirmed by owner) |
| Anthropic model access in the account | Access confirmed by owner for `eu.anthropic.claude-sonnet-4-6` in `eu-central-1` |
| DPA | AWS DPA checked in AWS Artifact by owner. Date recorded by owner: 24 Feb 2025. |
| Data handling | Read by owner on 2026-11-02 : <https://docs.aws.amazon.com/bedrock/latest/userguide/data-protection.html>. Model providers have no access to Bedrock logs or customer prompts and completions (Model Deployment Accounts). |
| API key type and lifetime | Production: short-term keys (confirmed by owner). Development and record mode on a local machine: a long-term key in the `LLM_PROVIDER_API_KEY` environment variable, expiry 90 days at most. Rotation: a scheduled EventBridge rule runs a Lambda every 6 hours that generates a new short-term key and writes it to AWS Secrets Manager (see "Credential"). |
| Price per model, cents per million tokens (input / output) | 300 / 1500 for `eu.anthropic.claude-sonnet-4-6` in `eu-central-1` (owner-reported: $3.00 input and $15.00 output per million tokens) |

### Credential

The single-credential rule stays: the adapter sends only `Authorization: Bearer <key>`. No AWS
access keys, SigV4 signing or IAM credentials are handled by this code. The key is read from
configuration or a secret store and never from the repository. Only its fingerprint is logged or
recorded.

AWS documents two key types, and the choice affects operations:

- **Short-term key.** Lasts up to 12 hours and inherits the permissions of the IAM principal that
  generated it. AWS recommends it for production. The adapter does not generate keys. A deployment
  job or secret-store rotation outside this code refreshes the stored key before it expires. An
  expired key is an HTTP 401/403 reply, mapped to `ProviderUnavailableError`, so generation jobs
  stop cleanly rather than retry.
- **Long-term key.** Lasts until a configured expiry and creates an IAM user. AWS recommends it only
  for exploration. Use it for local development and record mode only, with the expiry limited (90
  days at most), and an IAM policy that restricts it to `bedrock:InvokeModel` on the one model.

Production uses short-term keys, refreshed outside this code. An EventBridge schedule runs a Lambda
every 6 hours, well inside the 12-hour key lifetime. The Lambda has its own IAM role, generates a new
short-term Bedrock key with AWS's token generator, and writes it to AWS Secrets Manager. The worker runs on AWS and reads the secret named by `LLM_PROVIDER_API_KEY_SECRET_ID` on first use, and re-reads it once when a call returns 401/403. Local development sets `LLM_PROVIDER_API_KEY` instead; the two are mutually exclusive. The credential fingerprint follows the key in use, so a rotation is not a binding failure (tasks 4.13 and 4.14). A failed rotation shows up as
those replies, so the schedule needs an alarm on Lambda errors. The Lambda and its schedule are
infrastructure work outside the adapter and are not part of this addendum's code.

### Wire format the adapter will implement

Checked on 2026-11-02 against the AWS Converse API reference (request and response syntax, stop reasons, error codes) and the Bedrock API keys page (bearer token). Not yet proven against a live call from the adapter: the contract tests of task 4.4 must use recorded real responses, and the bearer-token call is confirmed by the first successful adapter call.

- Request: `POST https://<host>/model/<modelId>/converse`, where `<modelId>` is the confirmed model or `eu.` inference profile ID, URL-encoded.
- Authentication: header `Authorization: Bearer <key>`. This is the only credential sent.
- Body: JSON with `system` (a list of text blocks), `messages` (a list of `{role, content: [{text}]}`) and `inferenceConfig` (`maxTokens`, `temperature`). Structured output is requested through the prompt and the output schema, and the engine validates it. Converse also offers `outputConfig.textFormat` (`type` `json_schema`, with the schema passed as a string in `structure.jsonSchema.schema`). This version does not use it. Support for `eu.anthropic.claude-sonnet-4-6` is untested, and the engine's validation stays the safeguard whether or not it is later enabled.
- Response text: `output.message.content[*].text`, concatenated in order.
- Token usage: `usage.inputTokens` is input tokens and `usage.outputTokens` is output tokens.
- Model: Converse does not echo the model, so the adapter reports the configured model ID.
- Errors (status codes as listed in the Converse reference):
  - 429 (`ThrottlingException`, `ModelNotReadyException`), 408 (`ModelTimeoutException`), 424 (`ModelErrorException`), 500 (`InternalServerException`), 503 (`ServiceUnavailableException`) and 401/403 (`AccessDeniedException` or an expired key) map to `ProviderUnavailableError` in `LiveTransport`.
  - 400 (`ValidationException`) and 404 (`ResourceNotFoundException`) are request or configuration faults. They are not retried and are reported with the existing vendor error type chosen in task 4.4.
  - A 2xx body missing the fields above raises `VendorProtocolError`.
- Stop reason: only `end_turn` is a normal finish. The adapter sets no stop sequences, tools or guardrail, so every other `stopReason` is a malformed reply: `max_tokens`, `content_filtered`, `guardrail_intervened`, `malformed_model_output`, `model_context_window_exceeded`, `stop_sequence`, `tool_use`, `malformed_tool_use`, and any value not in this list. An empty text is malformed too. A malformed reply is never auto-corrected, so the item is flagged for review as in the main decision.

### Constraints carried from the main decision

- `allowed_hosts` contains exactly the confirmed regional Bedrock host. A redirect elsewhere is not followed and is rejected as a binding failure.
- EU residency depends on the model ID. A geographic `eu.` inference profile routes only within EU regions, and a `global.` profile can route anywhere, so it is rejected by configuration validation. A model ID with no profile prefix is processed in the region of the endpoint.
- Only data allowed by `docs/AI-CONTENT-DESIGN.md` (D6) is sent. No personal data.
- Prices are configured per model; a missing price fails the call rather than recording zero cost.
- Contract tests run on recorded, vendor-shaped responses without network.

### Consequences

Bedrock keeps the provider behind the existing port, and the owner's existing AWS account and DPA
remove a vendor onboarding step. EU residency is satisfied only by a single-region EU model or an
`eu.` profile, so the model ID is a mandatory item above. Short-term keys add a rotation duty
outside the code, which is the cost of following AWS's production guidance while keeping one
credential. Swapping vendor later means adding one module under `llm/vendors/` and changing
configuration, with no edit to the engine or the transport.