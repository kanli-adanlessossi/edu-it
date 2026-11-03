# Implementation Plan: AI Content Generation

## Overview

Implementation language is Python (the design uses concrete Python modules, so no language question applies). Work is ordered by milestone and follows the design section "Delivery order and dependencies". Each milestone ends with a required invariant task that proves the core invariant, "learners only ever see content an expert approved", with automated tests, followed by a checkpoint that also runs the incremental requirement-to-test traceability check (Requirement 23.5).

- **Milestone A (tasks 1 to 11):** network guard first, then pure domain rules, persistence, the LLMProvider engine, Replay and Live transports, the gated vendor adapter, versioned prompts, Verifier, `edu-it-generate`, `edu-it-eval`.
- **Milestone B (tasks 12 to 18):** variant lifecycle, variant and review repositories, the `item_flag` read side, approved-only serving, refill worker, reviewer queue.
- **Milestone C (tasks 19 to 25):** quality write side (observations, reports, flagging), path advisor, retention and the end-to-end GDPR proof.
- **Closing (tasks 26 to 29):** verification suite and report guard, then the Live_Run (task 28) and the Phase_4_Report (task 29) as the last two tasks.

**Dependency on decision D1.** The Live_Provider needs the ADR 0007 vendor addendum (decision D1), which names the provider and records the DPA and EU residency. Only three places touch it: task 4.4 (vendor adapter and its contract tests), the optional smoke call 4.12, and task 28 (Live_Run). Task 4.4 stops and asks the owner if the addendum is missing. It never chooses a vendor. No other task depends on 4.4, 4.12 or on the addendum, and everything else runs on the Replay_Provider and the fakes.

**Registration of personal data.** Revision `0005_ai_content` is built up by the tasks that introduce each table. Each task that introduces a personal-data table (`item_observation` in 19.2, `problem_report` in 20.1, `advisor_audit_log` and the `path_recommendation` changes in 22.2) registers it in `PERSONAL_DATA_TABLES`, `docs/DATA-MODEL.md` section 13, export, deletion and the retention policy in that same task (Requirement 21.6). Task 23 is the end-to-end proof, not the first registration.

## Tasks

### Milestone A: Generation pipeline

- [x] 1. Foundations: network guard, traceability tool, AI Config, replaced LLMProvider port and pure domain rules
  - [x] 1.1 Add the model network guard and the domain boundary check
    - Root pytest plugin `tools/pytest_no_model_network.py` (enabled through `addopts`) fails any test that connects to a non-loopback address, with a self-test proving it trips
    - `tools/check_domain_boundary.py` asserts the domain imports no provider SDK, and is run by the lint step
    - This is the first task so that every later test runs under the guard
    - _Requirements: 1.6, 2.5, 22.4_

  - [x] 1.2 Add the incremental traceability check
    - `tools/check_traceability.py --milestone {A,B,C,all}` reads the requirement ids of the milestone and fails, naming each requirement that no automated test claims through a `Validates: Requirements ...` line in its docstring
    - Milestone sets: A = Requirements 1 to 12 plus 22.1 and 22.4; B = 13 to 16 plus 22.2, 22.5 to 22.7; C = 17 to 21 plus 22.3; `all` adds 23 to 25
    - A short exceptions file lists requirements verified without a test, each with a reason (for example 1.11 and 24.13, which are owner actions), and is reviewed by the owner
    - Add `tools/tests` for the check, including a case where one requirement has no test
    - _Requirements: 23.2, 23.5_

  - [x] 1.3 Add the AI Config block and `validate_ai_config`
    - Add the values from the design Config table to `edu_it_domain/config.py`, with `LLM_MAX_ATTEMPTS == 1 + LLM_MAX_RETRIES` (3 and 2) and `LIVE_RUN_COST_CAP_CENTS` of 2000
    - `validate_ai_config` checks ordered bounds, a positive cap not above the daily budget and positive retentions, and names every offending key
    - Create the `edu_it_domain/ai/` package so later domain tasks add modules without sharing files
    - Add tests for valid and invalid Config
    - _Requirements: 1.3, 10.1, 10.2, 24.1_

  - [x] 1.4 Replace the `LLMProvider` port with the D6 signature
    - In `edu_it_domain/ports/llm_provider.py` define `complete(prompt_version, input, schema, max_retries)`, `PromptRef`, `LLMInput`, `OutputSchema`, `StructuredOutput`, `AttemptUsage`
    - Define `MalformedOutputError`, `ProviderUnavailableError`, `ProviderBindingError`, `BudgetReachedError(kind)`, `MissingFixtureError(key)`, `PersonalDataInRequestError`
    - Reject `max_retries` outside 0..2 with `ValueError`
    - Remove `LLMCompletion` from the domain and migrate `Fake_LLMProvider` and `test_fake_llm_provider.py`, `test_ports.py`, `test_fake_substitutability.py` in the same change
    - _Requirements: 1.1, 1.6, 12.1_

  - [x] 1.5 Implement domain rules `inputs.py`, `privacy.py`, `pseudonym.py`, `fixtures.py`
    - Typed `InputKind` allowlists (the `VERIFY_ANSWER` kind has no key field), value scan for personal data, HMAC pseudonymous handle, canonical `fixture_key` and `encode`/`decode`
    - _Requirements: 2.2, 2.6, 3.1, 3.2, 3.3, 7.2_

  - [x] 1.6 Implement domain rules `budget.py`, `generation.py`, `checks.py`
    - `check_attempt` (daily, per-track, job), `STAGES`, `stage_allowed` (merged output or markers on a named Base_Ref), `plan_units`, `remaining_units`, `commit_set`
    - `normalise`, `is_duplicate` (exact only), `coverage`, `readability_en` (Flesch), `readability_fr` (Kandel-Moles), `similarity` (5-word shingle containment)
    - _Requirements: 5.1, 5.3, 5.14, 5.15, 7.7, 7.8, 7.9, 7.10, 7.12, 10.1 to 10.5, 12.6_

  - [x] 1.7 Write property test for the privacy filter
    - **Property 6: LLM inputs carry only allowed data**
    - **Validates: Requirements 3.1, 3.2, 3.4, 18.6**

  - [x] 1.8 Write property test for pseudonymous handles
    - **Property 7: Pseudonymous handles do not expose or link identities**
    - **Validates: Requirements 3.3, 17.1**

  - [x] 1.9 Write property test for the budget gate
    - **Property 21: The budget gate holds before every attempt**
    - **Validates: Requirements 10.1, 10.2, 10.3, 10.4**

  - [x] 1.10 Write property test for duplicate detection
    - **Property 15: Duplicate detection is exact on normalised text**
    - **Validates: Requirements 7.7, 7.12**

  - [x] 1.11 Write property test for coverage findings
    - **Property 16: Coverage findings name every uncovered concept**
    - **Validates: Requirements 7.8**

  - [x] 1.12 Write property test for readability and similarity
    - **Property 17: Readability and similarity findings follow the limits**
    - **Validates: Requirements 7.9, 7.10**

- [x] 2. Content schemas and validator
  - [x] 2.1 Add `QuestionVariant` and the stage draft schemas to `edu_it_content/schemas.py`
    - `BriefDraft`, `OutlineDraft`, `LessonDraft` (commands only, no output field), `QuizDraft`, `LabDraft` (with `reference_solution`), `ProjectDraft`, `QuestionVariant`
    - Bilingual FR and EN learner-facing fields and Provenance fields
    - _Requirements: 5.8, 5.10, 7.5_

  - [x] 2.2 Tighten the reviewer rule and add `include_review_rule`
    - `_check_reviewer` rejects an `ai` item when `reviewed_by` or `reviewed_at` is missing, naming the item
    - `validate_content(content, *, include_review_rule=True)`; only the Generator passes `False`, the CLI and CI never do
    - Do not extend `--allow-pending`
    - _Requirements: 6.1, 6.2, 6.3_

  - [x] 2.3 Write property test for the validator
    - **Property 11: The validator rejects exactly the unreviewed AI items**
    - **Validates: Requirements 6.1, 6.2**

  - [x] 2.4 Write unit tests for strict validation of existing content
    - Strict `content validate` still passes every Phase 2 and Phase 3 item after the tightened rule
    - `LessonDraft` has no output field
    - _Requirements: 6.3, 7.5_

- [x] 3. Persistence for generation and budgets
  - [x] 3.1 Create revision `0005_ai_content` for generation and budgets and update `metadata.py`
    - Additive, inspect-then-add style of `0002_labs_runtime`: new columns on `generation_job` and `budget_ledger`
    - Later tasks extend the same revision for the tables they introduce (12.2, 12.3, 19.2, 20.1, 22.2); `downgrade` removes only what `upgrade` added; export table-name groups
    - Update `docs/DATA-MODEL.md` sections 2, 3 and 11 for these columns
    - _Requirements: 23.4_

  - [x] 3.2 Implement the `GenerationLedger` port with Postgres and in-memory repositories
    - `open_job`, `record_attempt` (ledger row and job totals in one transaction), `close_job`, `spent_today`, `spent_for_track`, `spent_for_job`
    - One shared contract suite for both implementations
    - _Requirements: 1.4, 10.1, 12.1, 12.3_

  - [x] 3.3 Write migration test over a populated database
    - Upgrade and downgrade keep rows from earlier revisions intact, following the `test_migration_*` pattern, for the revision as it stands after task 3.1 (task 23.4 extends it to the complete revision)
    - _Requirements: 23.4_

- [x] 4. LLM engine, transports and provider selection
  - [x] 4.1 Implement `llm/transport.py`, `llm/pydantic_schema.py` and `llm/engine.py`
    - `ModelTransport` and `RawCompletion` (infrastructure-internal), the Pydantic `OutputSchema` adapter, and the engine: privacy filter, `AttemptGate` before every attempt, `ProviderBinding` check, schema validation, retry within 3 attempts, `UsageSink` for every attempt
    - Output is returned exactly as validated; unavailability, binding failures and budget stops are not retried
    - _Requirements: 1.2, 1.3, 1.4, 1.5, 1.8, 1.9, 10.4, 10.5_

  - [x] 4.2 Implement `llm/fixture_store.py` and `llm/replay.py`
    - Canonical JSON Fixtures at `evals/fixtures/<prompt_id>/<key>.json`, ordered attempts per key, privacy scan before write
    - Replay transport raises `MissingFixtureError` naming the key and owns no socket
    - _Requirements: 2.2, 2.3, 2.4, 3.2_

  - [x] 4.3 Implement `llm/live.py` and the transport registry
    - Standard-library HTTPS transport behind `ModelTransport`, EU endpoint host binding, record mode through `fixture_store`
    - `llm/registry.py` lets a vendor module under `llm/vendors/` register itself by name, so adding the vendor adapter (task 4.4) edits no existing file
    - Contract suite against a loopback stub server
    - _Requirements: 1.1, 1.7, 2.2_

  - [x] 4.4 Implement the vendor `ModelTransport` adapter and its contract tests (gated on decision D1)
    - Gate first: check `docs/adr` for the ADR 0007 addendum naming the provider, DPA and EU residency. If it is missing, stop this task and ask the owner. Do not choose a vendor and do not guess its wire format. Record the task as blocked on the owner. No other task depends on it, so the rest of the plan continues
    - Implement the adapter in `llm/vendors/<vendor>.py` behind `ModelTransport`, registered through the registry of task 4.3
    - Contract tests on recorded vendor-shaped responses, run in CI without network: success, malformed body, rate limit, 5xx, timeout, redirect to another host, response under another key, token usage mapping
    - _Requirements: 1.7, 1.8, 1.10, 1.11_

  - [x] 4.5 Add `build_llm_provider` and settings
    - `composition.py` selection table: `test` allows fake and replay and refuses live, `production` refuses fake and replay; the ledger is bound to each provider
    - Add `LLM_PROVIDER_BACKEND`, `LLM_PROVIDER_ENDPOINT`, `LLM_PROVIDER_ALLOWED_HOSTS`, `LLM_MODEL_GENERATION`, `LLM_MODEL_VERIFIER`, `LLM_PRICES`, `LLM_FIXTURE_DIR`, `LLM_RECORD`, `ADVISOR_HANDLE_KEY` to `CompositionSettings` and `.env.example`
    - Does not reference the vendor adapter; the live backend resolves the vendor through the registry
    - _Requirements: 1.7, 2.1_

  - [x] 4.6 Write property test for structured output
    - **Property 1: Structured output is validated, unmodified and bounded**
    - **Validates: Requirements 1.2, 1.3, 1.5**

  - [x] 4.7 Write property test for attempt recording
    - **Property 2: Every attempt is recorded**
    - **Validates: Requirements 1.4, 12.1, 12.3**

  - [x] 4.8 Write property test for the provider binding
    - **Property 3: Responses outside the configured binding are rejected**
    - **Validates: Requirements 1.8, 1.9**

  - [x] 4.9 Write property test for fixture keys and round trip
    - **Property 4: Fixture keys are deterministic and Fixtures round-trip**
    - **Validates: Requirements 2.2, 2.6**

  - [x] 4.10 Write property test for replay
    - **Property 5: Replay returns what was recorded and fails clearly on a miss**
    - **Validates: Requirements 2.3, 2.4**

  - [x] 4.11 Write unit tests for provider selection and conformance
    - Selection table for `test`, `development`, `production` against `fake`, `replay`, `live`
    - Live, Replay and Fake satisfy the new `LLMProvider` protocol
    - _Requirements: 1.1, 2.1_

  - [x] 4.12 Add the capped vendor smoke-call script
    - `tools/llm_smoke.py` makes one tiny call through the Live_Provider with a Cost_Cap of a few cents given on the command line, applies the budget gate and the provider binding, and prints the cost
    - Same gate as task 4.4: stops and asks the owner if the ADR 0007 addendum is missing. The owner runs it by hand; it is never part of the test suite or CI
    - _Requirements: 1.7, 1.11, 10.2_

  - [x] 4.13 Add a refreshable credential source for the Live_Provider (gated on decision D1)
    - Same gate as task 4.4: the ADR 0007 addendum must be Accepted. Stop and ask the owner if it is not
    - Add a `CredentialSource` in `llm/` with two implementations: the existing environment variable `LLM_PROVIDER_API_KEY` (local development, long-term key) and AWS Secrets Manager read through the new setting `LLM_PROVIDER_API_KEY_SECRET_ID` (production). The two settings are mutually exclusive, and `provider selection` names the problem when both or neither are set
    - `LiveTransport` takes the source instead of a fixed string. It reads the key on first use and re-reads it once after an HTTP 401 or 403, then retries that attempt a single time. A second 401 or 403 raises `ProviderUnavailableError`
    - The credential fingerprint follows the key currently in use. The binding check compares against the fingerprint of the key that was sent, so a rotation never causes a false binding failure. A response under a different key still fails as before
    - Only the fingerprint is logged or recorded, never the key. The Secrets Manager client is imported only in the infrastructure layer, with a pinned version, and `tools/check_domain_boundary.py` stays green
    - Tests without network: key rotated between two calls, 401 then success after re-read, repeated 401, secret missing or unreadable, both or neither setting present, response under another key still rejected
    - _Requirements: 1.7, 1.8, 1.10, 1.11_

  - [x] 4.14 Add the key rotation Lambda for the production credential (gated on decision D1)
    - Same gate as task 4.4. Location `deploy/aws/key-rotation/`, outside the application packages
    - Handler that generates a new short-term Bedrock API key with AWS's token generator, using the Lambda's own IAM role, and writes it as a new version of the Secrets Manager secret named by `LLM_PROVIDER_API_KEY_SECRET_ID`. The handler never logs the key, only its fingerprint, and a failed write leaves the previous version in place
    - A template for the EventBridge schedule (every 6 hours, inside the 12-hour key lifetime), the Lambda role (least privilege: generate the key, write that one secret) and a CloudWatch alarm on Lambda errors
    - Unit tests with fake clients: new version written, previous version kept on failure, key never appears in logs or exceptions
    - Deploying the template is an owner action and is not part of the test suite or CI. Record in the task result that it was deployed once the owner confirms
    - _Requirements: 1.10, 1.11_

  - [x] 4.15 Pass the rendered prompt text and output schema to the vendor adapter (gated on decision D1)
    - Same gate as task 4.4. Follows from the `bedrock_claude` adapter of task 4.4, which today falls back to a one-line system prompt
    - `TransportRequest` carries only a `PromptRef` and the input fields. Render the prompt through `PromptStore` in `composition.py` or `live.py` and hand the text and the output schema to the adapter, replacing the fallback. The adapter still sends no `outputConfig`
    - Tests without network: the adapter receives the rendered prompt text and not the fallback, the request body contains the prompt as the `system` text, and no `outputConfig` is sent
    - _Requirements: 1.7, 1.10_

  - [x] 4.16 Keep the status class visible through `LiveTransport` (gated on decision D1)
    - Same gate as task 4.4. Do this after 4.13, which adds the 401/403 re-read to `live.py`, so the two changes do not conflict
    - Today every non-2xx reply becomes `ProviderUnavailableError`, so a 400 or 404 looks like an outage. Let the vendor adapter classify a non-2xx reply: 400 and 404 are configuration faults reported with the existing vendor error type and are not retried, while 429, 408, 424, 500, 503, 401 and 403 stay `ProviderUnavailableError`. The 401/403 re-read of task 4.13 still applies
    - Tests without network: a 400 and a 404 are classified as configuration faults and are not retried, each of the other listed statuses stays `ProviderUnavailableError`, 401/403 still trigger the single re-read
    - _Requirements: 1.7, 1.8, 1.10_

  - [x] 4.17 Reject `global.` model ids at startup (gated on decision D1)
    - Same gate as task 4.4. The adapter of task 4.4 rejects them at request time only
    - Add the check to `validate_ai_config` or to `CompositionSettings` validation, so a bad `LLM_MODEL_GENERATION` or `LLM_MODEL_VERIFIER` fails with the setting named, before any call. Keep the adapter's request-time check as a second guard
    - Tests without network: a `global.` model id is refused at startup and the message names the offending setting, an `eu.` profile id and a plain single-region model id are accepted
    - _Requirements: 1.10, 1.11_

- [x] 5. Checkpoint - Ensure all tests pass, ask the user if questions arise.
  - Ensure all tests pass, ask the user if questions arise.

- [x] 6. Versioned prompts and governance files
  - [x] 6.1 Implement `prompt_store.py` and the first prompt files
    - `prompts/<prompt_id>__v<version>.md` with front matter (`output_schema`, `input_kind`, `includes`)
    - Create `glossary__v1.md` and v1 prompts for brief, outline, lesson, quiz, lab, project and `verify_answer`, each drafting prompt including `glossary@1`
    - Prompts for variants (task 15.1) and the advisor (task 22.1) are added in their milestones
    - _Requirements: 4.1, 4.2, 4.4, 4.5, 4.6_

  - [x] 6.2 Add `tools/check_prompt_immutability.py` and the CI step
    - Diff `prompts/` against `--base <ref>`, fail naming every modified, renamed or deleted existing file, pass on new version files
    - Update `ci.yml` to fetch enough history to diff against the base
    - _Requirements: 4.2, 4.3_

  - [x] 6.3 Add `.github/CODEOWNERS` and the Networking seed content
    - A line for each `content/<track>/` path, with placeholder team handles the owner replaces (decision D11)
    - Hand-author `content/networking-basics/brief.json` (title "Networking basics" EN, "Bases du réseau" FR) and `evals/glossary/networking-terms.json`
    - _Requirements: 4.6, 5.7, 24.7_

  - [x] 6.4 Write property test for prompt files
    - **Property 8: Prompt files are named by id and version and never edited**
    - **Validates: Requirements 4.1, 4.2, 4.3**

  - [x] 6.5 Write unit tests for governance data
    - Glossary contains every Linux concept title pair and every Networking seed term; CODEOWNERS has a line per track; each drafting prompt includes the glossary
    - _Requirements: 4.4, 4.6, 5.7_

- [x] 7. Verifier
  - [x] 7.1 Implement `VerifyItem`
    - Independent answer through a blind `VERIFY_ANSWER` input (different model when `LLM_VERIFIER_MODEL` is set), command outputs from a Verification_Sandbox, duplicate, coverage, readability and similarity checks
    - Labs are delegated to an injected `LabVerifier` protocol, implemented by `VerifyLab` (task 7.2) and wired in task 8.3
    - Returns `(findings, outputs)` and never mutates the item or removes a finding
    - _Requirements: 7.1 to 7.12_

  - [x] 7.2 Implement `VerifyLab`
    - Provision Starting_State and Reference_Solution sandboxes as one unit, run every check in both, destroy both in `finally` after both phases finished or failed
    - Bound each run by `min(check.timeout, USABLE_RUN_BUDGET)` and each phase by `LAB_GRADING_RUN_DEADLINE`
    - A provisioning or exec failure is a finding, never a silent pass
    - _Requirements: 8.1 to 8.7_

  - [x] 7.3 Write property test for the blind answerer
    - **Property 12: The independent answerer is blind and the key is never changed**
    - **Validates: Requirements 7.2, 7.4, 12.5**

  - [x] 7.4 Write property test for sandbox command outputs
    - **Property 13: Command outputs and executions come from the sandbox only**
    - **Validates: Requirements 7.5, 7.6**

  - [x] 7.5 Write property test for verifier immutability
    - **Property 14: The Verifier never alters items or findings**
    - **Validates: Requirements 7.11**

  - [x] 7.6 Write property test for the lab matrix
    - **Property 18: Lab findings follow the check matrix**
    - **Validates: Requirements 8.1, 8.2, 8.3, 8.4**

  - [x] 7.7 Write property test for sandbox cleanup and bounds
    - **Property 19: Verification sandboxes are destroyed together and within bounds**
    - **Validates: Requirements 8.5, 8.6, 8.7**

- [x] 8. Generator and `edu-it-generate`
  - [x] 8.1 Implement `git_workspace.py`, `content_writer.py` and `stages.py`
    - The Git port has no merge and no push method. Stage markers are read from the merged base or from a named Base_Ref through `git ls-tree` only, and the Base_Ref is a source of markers and nothing else
    - `content_writer` writes sorted stable JSON and FR and EN MDX together, with Provenance (`origin: ai`, `reviewed_by` absent, SHA-256 `content_version`, `stable_id` from `IdGenerator`)
    - _Requirements: 5.1, 5.4, 5.5, 5.8, 5.9, 5.10, 5.14, 5.16_

  - [x] 8.2 Implement the Generation_Report and PR template writer
    - `docs/generation/<track>/<stage>-<run>.md` with items, provenance, tokens, cost, every finding, discarded units, items not generated and remaining Budget
    - Write `.github/PULL_REQUEST_TEMPLATE/ai-generation-<stage>.md` with the checklist and ensure the CODEOWNERS line for the track
    - _Requirements: 5.6, 5.7, 9.1, 9.2, 9.3, 9.4_

  - [x] 8.3 Implement `generator.py`, `cli.py` and the worker composition
    - `edu-it-generate --track T --stage S [--live | --record] [--cost-cap-usd N] [--limit-units N] [--base-ref REF]`, registered in `apps/worker/pyproject.toml`
    - Order: validate Config, stage gate (no job, branch or model call on refusal; markers from the merged base or the named Base_Ref), budget gate, branch, units, verify (labs through `VerifyLab`), commit completed units and report, exit codes
    - No option bypasses the stage gate or the budget gate, and `--base-ref` only widens where markers are read; resume skips committed `stable_id`; partial units are discarded and listed
    - Runs structural validation with `include_review_rule=False`
    - _Requirements: 5.2, 5.3, 5.5, 5.9, 5.11, 5.12, 5.13, 5.14, 5.15, 5.16, 10.1 to 10.5, 12.1 to 12.8_

  - [x] 8.4 Write property test for the stage gate
    - **Property 9: The stage gate depends only on the preceding Stage**
    - **Validates: Requirements 5.3, 5.4, 5.11, 5.12, 5.14, 5.15, 5.16**

  - [x] 8.5 Write property test for generated items
    - **Property 10: Generated items are unreviewed, bilingual, pinned and not servable**
    - **Validates: Requirements 4.5, 5.8, 5.10, 22.1, 24.8**

  - [x] 8.6 Write property test for the Generation_Report
    - **Property 20: The Generation_Report is complete and consistent**
    - **Validates: Requirements 9.1, 9.2, 9.3, 9.4, 24.6**

  - [x] 8.7 Write property test for interrupted runs
    - **Property 23: Interrupted runs commit only complete units and resume exactly**
    - **Validates: Requirements 12.2, 12.4, 12.6, 12.7, 12.8, 24.5, 24.9, 24.10, 24.11**

  - [x] 8.8 Write unit and static tests for the Generator
    - Each Stage against fake git, fake sandbox and Replay_Provider; a named Base_Ref is read without any merge; the generator package has no merge, push or protected branch name; no CLI option bypasses the gates
    - _Requirements: 5.1, 5.9, 5.13, 5.16, 10.5_

- [x] 9. Evals and `edu-it-eval`
  - [x] 9.1 Implement the Eval_Suite runner and CLI
    - `evals/suites/golden.json` with `schema-conformance`, `answer-key` and `glossary-terms` cases built from the Phase 2 and 3 Golden_Set
    - Deterministic runner (sorted cases, no wall clock), Replay_Provider only under `EDU_IT_ENV=test`, budget gate with `--live`, `edu-it-eval` registered in `apps/worker/pyproject.toml`
    - _Requirements: 11.1, 11.2, 11.3, 11.4_

  - [x] 9.2 Write property test for the Eval_Report
    - **Property 22: The Eval_Report is deterministic and its pass rate is exact**
    - **Validates: Requirements 11.2, 11.5**

  - [x] 9.3 Write unit tests for the eval runner
    - Replay only in the test environment; `--live` applies the Budgets
    - _Requirements: 11.3, 11.4_

- [x] 10. Milestone A invariant proof
  - [x] 10.1 Write the required Milestone A invariant tests
    - A Generation_Run for every Stage on the fakes produces no item with `reviewed_by` set and no item in a servable state, and `content validate` on the Generation_Branch content rejects the AI items
    - The suite contains no test that calls a live model (network guard active)
    - _Requirements: 6.2, 22.1, 22.4_

- [x] 11. Checkpoint - Milestone A: lint, type checks, unit and property tests, `content validate`, traceability
  - Run `tools/check_traceability.py --milestone A` and add the missing test or exception for every requirement it names (Requirement 23.5)
  - Ensure all tests pass, ask the user if questions arise.

### Milestone B: Variants and review

- [x] 12. Variant lifecycle, repositories and the flag read side
  - [x] 12.1 Implement `lifecycle.py` and amend D6
    - `VariantState`, `TRANSITIONS`, `transition`, `SERVABLE_STATES` (Approved and Served), with `Withdrawn -> Approved` reviewer reinstatement
    - Amend `docs/AI-CONTENT-DESIGN.md`, `.kiro/steering/ai-content.md` and the diagram in the same change (decision D3)
    - Pure domain code, so it starts as soon as the Config and package exist
    - _Requirements: 13.3, 13.4, 14.1, 19.4_

  - [x] 12.2 Implement `VariantRepository` and `ReviewTaskRepository`
    - Extend revision `0005_ai_content` and `metadata.py` with the new columns on `variant` and `review_queue_item` and the `variant_revision` table; update `docs/DATA-MODEL.md` sections 2, 3 and 11
    - Ports, Postgres and in-memory repositories, one contract suite; `decide` writes the transition and the variant in one transaction; `add` is idempotent per open target
    - Wire into `composition.py`
    - _Requirements: 13.3, 15.4, 15.5, 15.10, 23.4_

  - [x] 12.3 Implement the `item_flag` table and the `ItemFlagReader` port
    - Extend revision `0005_ai_content` and `metadata.py` with `item_flag` (partial unique index on the active flag); update `docs/DATA-MODEL.md`
    - `ItemFlagReader.active_flags(keys)` in a new port module, with in-memory and Postgres implementations under one contract suite, wired into `composition.py`
    - Test fixtures set flags directly through the repository so Milestone B proves flagged items are never served; the Milestone C `ItemQualityRepository` extends this reader (task 19.2)
    - _Requirements: 14.1, 14.2, 20.3, 23.4_

- [x] 13. Reviewer queue backend
  - [x] 13.1 Implement `ReviewTaskActions` and `ListReviewTasks`
    - approve, reject, request changes and edit (new `variant_revision` and `content_version`); the track scope check runs before any write
    - Cursor-paginated listing over the reviewer's track
    - _Requirements: 15.1, 15.3 to 15.7, 15.10, 15.11, 15.12, 16.1, 16.2_

  - [x] 13.2 Add the review API
    - `errors/ai.py` (403 `reviewer-expertise-required`, 404, 409 `review-task-already-decided`), schemas, `review_tasks.py` router for the paths in the design API table, CSRF gate, `route_table.py` entries, OpenAPI, FR and EN problem details
    - _Requirements: 15.2, 15.8, 15.10_

  - [x] 13.3 Write property test for review authorization
    - **Property 28: Review tasks are scoped and authorized**
    - **Validates: Requirements 15.1, 15.8, 15.11, 15.12**

  - [x] 13.4 Write property test for review pagination
    - **Property 30: Review task pages cover the list exactly once**
    - **Validates: Requirements 15.10**

  - [x] 13.5 Write API tests for review endpoints
    - 403, 409 and 422 problem documents in FR and EN, route table completeness and OpenAPI publication
    - _Requirements: 15.2, 15.8_

- [x] 14. Approved-only serving
  - [x] 14.1 Implement `serving.py` and `ServableCatalog`
    - `servable_variants`, deterministic `pick_variant`, `resolve_question` (variant, then parent, then another question of the concept, then no-servable-question)
    - `ServableCatalog` decorates `CatalogRepository` and excludes flagged items through the `ItemFlagReader` of task 12.3; it depends on no Milestone C task
    - Variants are served in spaced-review sessions only (D4) under the parent `stable_id`, with `variant_ref` pinned between start and submit
    - No `LLMProvider` dependency anywhere in serving
    - _Requirements: 14.1, 14.2, 14.3, 20.1, 20.3, 20.4, 20.5_

  - [x] 14.2 Wire serving into the API and worker
    - Inject `ServableCatalog` in the API composition and make the worker build review and practice sets through the same filter; carry `variant_ref` in the review use cases
    - `no-servable-question` problem document with translated `detail`
    - _Requirements: 14.1, 14.5, 20.4_

  - [x] 14.3 Write property test for approved-only serving
    - **Property 26: Serving returns only Approved items, from the moment of change**
    - **Validates: Requirements 14.1, 14.2, 14.6, 22.2, 22.5, 22.6**

  - [x] 14.4 Write property test for pool fallback
    - **Property 27: Emptied pools fall back to the parent, and flagged parents to another question**
    - **Validates: Requirements 20.1, 20.3, 20.5**

  - [x] 14.5 Write property test for reviewer edits
    - **Property 29: Reviewer edits never interrupt or bypass serving**
    - **Validates: Requirements 15.7, 16.1, 16.2, 16.3, 16.4**

- [x] 15. Variant refill and AI worker
  - [x] 15.1 Add the variant prompt and `RefillVariantPool`
    - `prompts/variant__v1.md` (includes the glossary) and the use case: Approved parent only, shortfall capped by `VARIANTS_PER_REFILL_MAX`, draft, `VerifyItem`, then `PendingReview` plus one Review_Task or `Flagged` plus findings
    - Any stop (malformed, unavailable, Budget) creates nothing servable and does not re-enqueue
    - _Requirements: 13.2 to 13.7_

  - [x] 15.2 Implement `build_ai_worker`, refill jobs and `serve.py` changes
    - `validate_ai_config` at startup, `ai.refill.cycle` and `ai.refill.parent` with bucket dedupe keys, handlers that log and swallow errors, no alert or escalation
    - `build_served_worker` returns both workers and `main` starts and stops both
    - The worker must build review and practice sets through `ServableCatalog` (same approved-only, unflagged filter as the API, carrying `variant_ref`); deferred from task 14.2
    - _Requirements: 13.1, 13.5, 13.8, 13.9, 13.10_

  - [x] 15.3 Write property test for variant states
    - **Property 24: Variant states only move along the allowed transitions**
    - **Validates: Requirements 13.3, 13.4, 15.4, 15.5, 15.6**

  - [x] 15.4 Write property test for refill
    - **Property 25: Refill is triggered by the threshold, once per cycle, and failure serves nothing**
    - **Validates: Requirements 13.1, 13.6, 13.7, 13.8, 13.9**

  - [x] 15.5 Write worker tests for refill
    - Budgets and the retry bound apply in every refill job; a failed refill sends no mail and enqueues nothing more
    - _Requirements: 13.5, 13.10_

- [x] 16. Reviewer queue web UI
  - [x] 16.1 Build the reviewer pages under `/[locale]/reviewer`
    - List and detail with approve, edit, request changes and reject; Variant in FR and EN, Provenance and findings; FR and EN strings
    - Settle exact file paths and component names from the existing learner pages
    - _Requirements: 15.2, 15.3, 15.9_

  - [x] 16.2 Write web tests for the reviewer queue
    - vitest and Playwright for the actions, plus the axe-core checks used for learner pages
    - _Requirements: 15.9_

- [x] 17. Milestone B invariant proof
  - [x] 17.1 Write the required Milestone B invariant tests
    - API tests request every item state through every serving endpoint and assert no non-approved item is returned
    - Worker tests assert review and practice sets contain approved items only
    - Static test: the serving and review modules import no LLM port
    - A Rejected, retired or Flagged Variant (flag set directly through the `item_flag` repository) is never returned after the change (copies already served are not recalled)
    - _Requirements: 14.3, 14.4, 14.5, 22.2, 22.5, 22.6, 22.7_

- [x] 18. Checkpoint - Milestone B: lint, type checks, unit and property tests, `content validate`, traceability
  - Run `tools/check_traceability.py --milestone B` and add the missing test or exception for every requirement it names (Requirement 23.5)
  - Ensure all tests pass, ask the user if questions arise.

### Milestone C: Personalization and quality loop

- [x] 19. Quality domain, write-side repositories and observations
  - [x] 19.1 Implement `quality.py`
    - Incremental `fold`, `is_anomalous` (min samples and bounds), `reports_reach_threshold` counting distinct learners of the current `content_version`
    - Pure domain code, so it needs only the Config
    - _Requirements: 18.4, 19.1, 19.2_

  - [x] 19.2 Implement the quality write side and record observations
    - Extend revision `0005_ai_content`, `metadata.py` and `docs/DATA-MODEL.md` with the new `item_statistic` columns, `ai_cursor` and `item_observation`
    - Register `item_observation` in `PERSONAL_DATA_TABLES`, `docs/DATA-MODEL.md` section 13, export, deletion and retention in this same task, with its `test_gdpr_*_coverage` case
    - `ObservationRecorder` and `ItemQualityRepository` extending the `ItemFlagReader` of task 12.3 with `flag`, `clear_flag`, `observations_after` and `apply` (stats and cursor in one transaction, idempotent flag), Postgres and in-memory, one contract suite, wired into `composition.py`
    - `AttemptQuizUseCase` and `SubmitReviewUseCase` append an `item_observation` (elapsed time optional, clamped to `ITEM_ELAPSED_MS_MAX`)
    - _Requirements: 19.1, 21.4, 21.6, 23.4_

  - [x] 19.3 Write property test for the report threshold
    - **Property 35: The report threshold counts distinct learners of the current version**
    - **Validates: Requirements 18.4**

  - [x] 19.4 Write property test for statistics folding
    - **Property 36: Statistics fold equals the batch result and anomalies follow the bounds**
    - **Validates: Requirements 19.1, 19.2**

- [x] 20. Problem reports
  - [x] 20.1 Add the `problem_report` table and `ProblemReportRepository`
    - Extend revision `0005_ai_content`, `metadata.py` and `docs/DATA-MODEL.md` with `problem_report`
    - Register `problem_report` in `PERSONAL_DATA_TABLES`, `docs/DATA-MODEL.md` section 13, export, deletion and retention in this same task, with its `test_gdpr_*_coverage` case
    - Postgres and in-memory repositories (add, get, `reporters_for(item, version)`, `delete_for_user`), one contract suite, wired into `composition.py`
    - _Requirements: 18.2, 21.1, 21.2, 21.4, 21.6, 23.4_

  - [x] 20.2 Implement `SubmitProblemReport` and the API route
    - `POST /problem-reports` with required `Idempotency-Key`, stored once with `stable_id` and `content_version`, 422 problem document for an oversize comment or invalid category, route table entry
    - The comment has no path to any `LLMInput`
    - _Requirements: 18.2, 18.3, 18.5, 18.6_

  - [x] 20.3 Add the "Report a problem" control to question, lesson and lab views
    - FR and EN strings, category and optional comment
    - _Requirements: 18.1_

  - [x] 20.4 Write property test for problem reports
    - **Property 34: Problem reports are stored once and bounded**
    - **Validates: Requirements 18.2, 18.3, 18.5**

  - [x] 20.5 Write web tests for the report control
    - Control shown on each view in FR and EN, axe-core clean
    - _Requirements: 18.1_

- [x] 21. Flagging and review of flagged items
  - [x] 21.1 Implement `FoldObservations`, `EvaluateFlags` and `FlagItem`
    - `FlagItem` is idempotent: withdraws the item, sets a Variant to `Withdrawn`, creates exactly one open `flagged_item` Review_Task listing the report count or anomaly, enqueues a refill when a pool empties
    - Extend `ReviewTaskActions`: approve returns the item to serving and clears the flag, reject keeps it out, and a flag is cleared only by that decision
    - _Requirements: 18.4, 18.7, 18.8, 18.9, 19.1 to 19.5, 20.2_

  - [x] 21.2 Add quality jobs to the AI worker
    - `ai.quality.cycle` and `ai.quality.evaluate_item` with dedupe keys; `SubmitProblemReport` enqueues evaluation best-effort
    - _Requirements: 18.4, 19.1, 19.2_

  - [x] 21.3 Write property test for flagging
    - **Property 37: Flagging is idempotent and a review decision settles it**
    - **Validates: Requirements 18.4, 18.7, 19.3, 19.4, 19.5**

  - [x] 21.4 Write worker tests for the quality loop
    - The Review_Task is the only notification channel; a static test shows the worker's quality code never imports the LLM port
    - _Requirements: 18.6, 18.8_

- [x] 22. Path advisor
  - [x] 22.1 Implement `ordering.py`, the advisor prompt and the `AdvisorInput` builder
    - `rule_based_order` (total, deterministic, prerequisites first, at most `PATH_RECOMMENDATION_LENGTH`), `check_llm_order`
    - `prompts/advisor__v1.md`, `EligibleItems.for_learner` (exists, servable, unlocked), `AdvisorInput` with a per-run Pseudonymous_Handle
    - _Requirements: 17.1, 17.3, 17.4_

  - [x] 22.2 Implement the recommendation tables, port, repositories and use cases
    - Extend revision `0005_ai_content`, `metadata.py` and `docs/DATA-MODEL.md` with `advisor_audit_log` and the `path_recommendation` changes (`stable_id`, `ordering`, `created_at`, `dismissed_at`, `run_id`)
    - Register `advisor_audit_log` and the `path_recommendation` changes (dismissals included) in `PERSONAL_DATA_TABLES`, `docs/DATA-MODEL.md` section 13, export, deletion and retention in this same task, with their `test_gdpr_*_coverage` cases
    - `PathRecommendationRepository` (Postgres and in-memory, contract suite, wired into `composition.py`), `RunPathAdvisor` (every failure ends in the rule-based order, one audit row per run), `GetRecommendation` (re-filtered at read time), `DismissRecommendation` (idempotent)
    - _Requirements: 17.2, 17.4, 17.5, 17.6, 17.8, 21.1, 21.2, 21.4, 21.6, 23.4_

  - [x] 22.3 Add advisor jobs to the AI worker
    - `ai.advise.cycle` and `ai.advise.user` with bucket dedupe keys; refuse startup without `ADVISOR_HANDLE_KEY` in production
    - _Requirements: 17.2_

  - [x] 22.4 Add the recommendation API and dashboard card
    - `GET /path-recommendation` (204 when none), `POST /path-recommendation/{id}/dismissal`, route table entries, dashboard card with dismiss action in FR and EN
    - _Requirements: 17.7, 17.8_

  - [x] 22.5 Write property test for the rule-based order
    - **Property 32: The rule-based order is total, deterministic and respects prerequisites**
    - **Validates: Requirements 17.5**

  - [x] 22.6 Write property test for persisted recommendations
    - **Property 31: Persisted recommendations contain only Eligible_Items, with a fallback and an audit row**
    - **Validates: Requirements 17.3, 17.4, 17.5, 17.6, 17.9, 22.3**

  - [x] 22.7 Write property test for dismissal
    - **Property 33: Dismissal removes a recommendation from view and is idempotent**
    - **Validates: Requirements 17.8**

  - [x] 22.8 Write web tests for the dashboard card
    - Card shown, dismissal hides it, axe-core clean
    - _Requirements: 17.7, 17.8_

- [x] 23. Retention and end-to-end GDPR proof
  - [x] 23.1 Implement `PurgeAiData` and the `ai.retain` job
    - Apply the retention periods already registered with each table (no new registration here) to `item_observation`, `problem_report`, `advisor_audit_log` and superseded recommendations, idempotently
    - `ai.retain` with a bucket dedupe key in the AI worker
    - _Requirements: 21.4_

  - [x] 23.2 Write the end-to-end export and deletion flow test
    - A learner with reports, recommendations, dismissals, audit entries and observations: the export contains them all, deletion removes them, a second export contains none, and `item_statistic` aggregates are unchanged with no learner link
    - Fails if any personal-data table of this spec is missing from `PERSONAL_DATA_TABLES`, section 13, export, deletion or retention
    - _Requirements: 21.1, 21.2, 21.3, 21.4, 21.5, 21.6_

  - [x] 23.3 Write property test for GDPR coverage
    - **Property 38: Export, deletion and retention cover every new personal-data table**
    - **Validates: Requirements 21.1, 21.2, 21.3, 21.4, 21.5, 21.6**

  - [x] 23.4 Extend the migration test to the complete revision
    - Upgrade and downgrade of the full `0005_ai_content` keep rows from earlier revisions intact and downgrade removes only what upgrade added
    - _Requirements: 23.4_

- [x] 24. Milestone C invariant proof
  - [x] 24.1 Write the required Milestone C invariant tests
    - A Flagged_Item is out of serving and a recommendation never contains a non-approved item, across the API and the worker
    - A concept with no servable question returns the translated problem document
    - _Requirements: 20.4, 20.5, 22.3_

- [x] 25. Checkpoint - Milestone C: lint, type checks, unit and property tests, `content validate`, traceability
  - Run `tools/check_traceability.py --milestone C` and add the missing test or exception for every requirement it names (Requirement 23.5)
  - Ensure all tests pass, ask the user if questions arise.

### Closing: verification, Live_Run and Phase_4_Report

- [x] 26. Verification suite and report guard
  - [x] 26.1 Add the `ai-content` verification suite and the complete traceability test
    - `tools/run_verification.py --suite ai-content` runs lint, type checks, unit and property tests, `content validate`, `edu-it-eval` (replay), the prompt immutability check and `tools/check_traceability.py --milestone all`
    - `tools/tests/test_ai_content_verification_suite.py` maps every requirement to at least one existing test, like `test_labs_verification_suite.py`; this is the complete version of the incremental checks at the milestone checkpoints
    - _Requirements: 23.1, 23.2, 23.5_

  - [x] 26.2 Implement `tools/phase_report.py`
    - Writes `docs/reports/phase-4.md` in the form of `docs/reports/phase-3.md` only when every required acceptance check ran and passed, otherwise names each check that did not run or did not pass and does not write a passing report
    - Accepts the Live_Run cost, Cost_Cap, models, branch name and Eval_Report as inputs and a "not verified" list
    - _Requirements: 25.1 to 25.6_

  - [x] 26.3 Write property test for the report guard
    - **Property 39: The phase report is passing only when every check ran and passed**
    - **Validates: Requirements 25.6**

- [x] 27. Checkpoint - Full suite green before the Live_Run
  - Ensure all tests pass, ask the user if questions arise.

- [x] 28. Live_Run: generate the Networking track outline and first lesson on an unmerged branch (USD 20 cap)
  - [x] 28.1 Confirm the Live_Run prerequisites
    - Confirm decision D1: the ADR 0007 addendum exists in `docs/adr` and the vendor adapter of task 4.4 is implemented with its contract tests green. If either is missing, stop and ask the owner. Do not choose a vendor
    - Set `LLM_PROVIDER_ENDPOINT`, `LLM_PROVIDER_ALLOWED_HOSTS`, `LLM_MODEL_GENERATION`, `LLM_MODEL_VERIFIER`, `LLM_PRICES` and the key outside the repository (production reads the secret that task 4.14 refreshes)
    - Confirm `content/networking-basics/brief.json` exists on the base ref (the owner merges it; the Generator never merges) and the Docker daemon is available for sandbox checks
    - _Requirements: 1.10, 1.11, 24.4, 24.13_

  - [x] 28.2 Generate the outline
    - Run `edu-it-generate --track networking-basics --stage outline --record --cost-cap-usd 20` from a clean tree (`--record` already selects the Live_Provider and writes the Fixtures; it is mutually exclusive with `--live`)
    - The run creates its own Generation_Branch, applies the USD 20 cap with the daily and per-track Budgets, runs all Verification_Checks, and leaves the branch unmerged with no item marked reviewed. Keep the branch name and Generation_Report
    - If the cap is reached, only fully completed units are committed and the discarded unit is listed
    - _Requirements: 24.1, 24.2, 24.3, 24.4, 24.5, 24.6, 24.7, 24.8, 24.9, 24.10, 24.11_

  - [x] 28.3 Generate the first lesson
    - Run `edu-it-generate --track networking-basics --stage lessons --limit-units 1 --record --cost-cap-usd 20 --base-ref <outline Generation_Branch>`, FR and EN
    - The outline is unmerged, so the Stage_Markers of the preceding Stage are read from the named Base_Ref. That only changes where markers are read. It does not merge or bypass the gate. If the gate still refuses, stop and ask the owner. Do not add a bypass option or merge
    - Keep the second branch name, Generation_Report, total cost, tokens and models
    - _Requirements: 5.3, 5.13, 5.14, 5.15, 5.16, 24.1, 24.2, 24.3, 24.4, 24.5, 24.6, 24.8, 24.9, 24.10, 24.11, 24.12_

- [x] 29. Phase 4 report
  - [x] 29.1 Run the Eval_Suite for the current Prompt versions
    - Run `edu-it-eval` on the Replay_Provider and keep the Eval_Report
    - _Requirements: 11.1, 11.2, 25.3_

  - [x] 29.2 Write `docs/reports/phase-4.md`
    - Run the `ai-content` verification suite and `tools/phase_report.py` with the Live_Run cost in USD, the Cost_Cap, the models, the Generation_Branch names, the Eval_Report results, the command and outcome of each acceptance check, and what was not verified
    - If any check did not run or pass, the tool must not write a passing report and the missing check is named in the report
    - _Requirements: 23.2, 25.1, 25.2, 25.3, 25.4, 25.5, 25.6_

## Notes

- Tasks marked with `*` are optional and can be skipped for a faster MVP, except that the milestone invariant tasks (10.1, 17.1, 24.1), the network guard and domain boundary check (1.1) and the end-to-end GDPR flow test (23.2) are required because Requirements 2.5, 21 and 22 say the tests SHALL exist.
- Property tests use Hypothesis with at least 100 examples and carry the tag `Feature: ai-content, Property {n}: {text}`. Each property test lives in its own file, so tasks in one wave never write the same file.
- Every automated test carries a `Validates: Requirements ...` line in its docstring. `tools/check_traceability.py` (task 1.2) reads it, and each milestone checkpoint (tasks 11, 18, 25) runs it for that milestone (Requirement 23.5). Task 26.1 keeps the complete version over all requirements.
- The network guard (1.1) is the first task, so every later test runs under it. Only task 28 calls a live model in the plan, plus the optional hand-run smoke script 4.12. Neither is part of the test suite or CI.
- Task 4.4 is gated on the ADR 0007 addendum (decision D1). If the addendum is missing the task stops and asks the owner, and the executor records it as blocked while later waves continue. Only 4.12 and 28.1 depend on it. The wave list is a scheduling aid, not a claim that other tasks need 4.4.
- Waves interleave milestones where tasks are independent: the lifecycle task (12.1) and the quality domain rules (19.1) start early because they need only the Config. The milestone checkpoints still run once every task of their milestone is complete.
- Revision `0005_ai_content` is extended by the task that introduces each table (3.1, 12.2, 12.3, 19.2, 20.1, 22.2), and personal-data tables are registered for export, deletion and retention in the same task that introduces them (Requirement 21.6). `item_flag` and the `ItemFlagReader` belong to Milestone B (12.3) so serving (14.1) depends on no Milestone C task; Milestone C adds only the write side.
- Base_Ref reading of the stage gate: the stage order requires a merged brief before the outline and a merged outline before lessons, and the Generator never merges. The plan hand-authors the brief (6.3) for the owner to merge (Requirement 24.13) and uses `--base-ref` with the outline Generation_Branch for the lesson run (Requirements 5.14 to 5.16 and 24.12). The Base_Ref is a source of Stage_Markers only and never bypasses the gate. The project owner has not yet confirmed this reading, so confirm it before running task 28.
- Each task references requirements for traceability. Checkpoints end each milestone.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["1.2", "1.3", "2.1", "3.1", "6.2"] },
    { "id": 2, "tasks": ["1.4", "1.6", "2.2", "3.3", "6.3", "12.1"] },
    { "id": 3, "tasks": ["1.5", "1.9", "1.10", "1.11", "1.12", "2.3", "2.4", "3.2", "7.2", "8.1", "12.2"] },
    { "id": 4, "tasks": ["1.7", "1.8", "4.1", "6.1", "7.1", "7.6", "7.7", "12.3", "13.1", "19.1"] },
    { "id": 5, "tasks": ["4.2", "4.6", "4.7", "4.8", "6.4", "6.5", "7.3", "7.4", "7.5", "8.2", "13.2", "13.4", "14.1", "15.1", "19.3", "19.4"] },
    { "id": 6, "tasks": ["4.3", "4.9", "4.10", "13.3", "13.5", "14.2", "14.3", "14.4", "14.5", "15.3", "16.1", "22.1"] },
    { "id": 7, "tasks": ["4.4", "4.5", "16.2", "17.1", "22.5"] },
    { "id": 8, "tasks": ["4.11", "4.12", "8.3", "19.2"] },
    { "id": 9, "tasks": ["8.4", "8.5", "8.6", "8.7", "8.8", "9.1", "10.1", "15.2", "20.1"] },
    { "id": 10, "tasks": ["9.2", "9.3", "15.4", "15.5", "20.2", "21.1", "22.2"] },
    { "id": 11, "tasks": ["20.3", "20.4", "21.2", "21.3", "22.6", "22.7"] },
    { "id": 12, "tasks": ["20.5", "21.4", "22.3", "22.4"] },
    { "id": 13, "tasks": ["22.8", "23.1", "24.1"] },
    { "id": 14, "tasks": ["23.2", "23.3", "23.4"] },
    { "id": 15, "tasks": ["26.1", "26.2"] },
    { "id": 16, "tasks": ["26.3", "28.1"] },
    { "id": 17, "tasks": ["28.2"] },
    { "id": 18, "tasks": ["28.3"] },
    { "id": 19, "tasks": ["29.1"] },
    { "id": 20, "tasks": ["29.2"] }
  ]
}
```
