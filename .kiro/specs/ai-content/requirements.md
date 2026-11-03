# Requirements Document

## Introduction

This spec builds **AI content generation** for EDU-IT: an ahead-of-time pipeline that drafts content
with an LLM, verifies it automatically, and puts it in front of an expert before any learner sees it.
It follows the hands-on-labs phase (`docs/reports/phase-3.md`), which delivered the sandbox runtime,
the Golden_Set seed content and the strict `content validate` gate. It inherits every authoritative
rule from steering: AI generation and review (#[[file:.kiro/steering/ai-content.md]]), content
pipeline and localization (#[[file:.kiro/steering/content.md]]), domain rules
(#[[file:.kiro/steering/domain-rules.md]]), labs and sandboxes (#[[file:.kiro/steering/labs.md]]),
authentication and privacy (#[[file:.kiro/steering/auth-privacy.md]]), API conventions
(#[[file:.kiro/steering/api-conventions.md]]) and process
(#[[file:.kiro/steering/working-agreements.md]]). The `LLMProvider` signatures, stages, variant
lifecycle, path advisor and failure scenario 4.4 are in `docs/AI-CONTENT-DESIGN.md` (D6). The AI/review
tables (`review_queue_item`, `variant`, `generation_job`, `item_statistic`, `path_recommendation`,
`budget_ledger`) are in `docs/DATA-MODEL.md` (D3). ADR 0007 (LLM provider and data residency) applies.

**Core invariant.** Learners only ever see content an expert approved. Every milestone proves this
invariant with automated tests (Requirement 22).

**Two review workflows, by design.** Authoring-time content (tracks, modules, lessons, quizzes, labs,
projects) is reviewed through pull requests. Runtime quiz variants are reviewed through the web
reviewer queue. Both workflows are kept, and neither replaces the other.

Steering is the single source of truth. This document references steering and does not restate rule
numbers as new rules. D6 fixes the retry bound at a maximum of 3 attempts per generation job (one
initial attempt plus at most 2 retries); that value is used unchanged. Business numbers that steering
does not fix (budget amounts, variant-pool threshold, flagging thresholds, readability and similarity
limits) live in `domain/config.py` as "initial Config value" proposals for the user to confirm at
review.

The work spans three milestones:

- **Milestone A: Generation pipeline.** `LLMProvider` implementation, record-and-replay fixtures,
  versioned prompts, `edu-it generate`, pre-review verification, and `edu-it eval`.
- **Milestone B: Variants and review.** Variant-pool refill by the worker, approved-only serving, and
  the web reviewer queue.
- **Milestone C: Personalization and quality loop.** Path advisor, learner problem reports, item
  statistics with auto-flagging, and GDPR coverage.

**Pending owner confirmation.** The Live_Run needs the lessons Stage to run while the outline is
unmerged. Requirement 5 (criteria 14 to 16) and Requirement 24 (criteria 12 and 13) therefore allow
the Generator to read Stage_Markers from a named Base_Ref, and assume that the project owner
hand-authors and merges the Networking_Track brief. The project owner has not yet confirmed this
approach.

**Out of scope:** live generation on a learner's request, and any AI tutor or chat. No learner action
triggers a synchronous LLM call.

## Glossary

- **LLMProvider**: The domain port `complete(prompt_version, input, schema, max_retries)` returning a
  StructuredOutput, defined in D6 and implemented in the infrastructure layer.
- **Live_Provider**: The LLMProvider implementation that calls a real model over the network.
- **Replay_Provider**: The LLMProvider implementation that answers from recorded Fixtures and makes
  no network call. Used by every automated test.
- **Fixture**: A recorded LLM request and response pair stored for replay.
- **StructuredOutput**: LLM output parsed and validated against a content schema.
- **Prompt**: A versioned prompt definition under `prompts/`, identified by `prompt_id` and
  `prompt_version`. The Glossary_Prompt is the prompt holding the FR/EN term glossary.
- **Generator**: The component behind `edu-it generate` that runs one Stage per invocation.
- **Stage**: One step of the sequence brief, outline, lessons, quizzes, labs, project.
- **Generation_Run**: One invocation of a Stage, recorded as a `generation_job`.
- **Stage_Marker**: The recorded evidence in a git ref that a Stage has produced its output.
- **Base_Ref**: A git ref explicitly named in an `edu-it generate` invocation, from which the
  Generator reads Stage_Markers of the preceding Stage.
- **Generation_Branch**: The git branch created by a Generation_Run for authoring-time content.
- **Generation_Report**: The document committed on the Generation_Branch listing outputs, costs and
  every Verification_Finding.
- **PR_Template**: The pull-request template holding the review checklist.
- **CODEOWNERS_File**: The file mapping content paths to reviewers by track.
- **Verifier**: The component that runs Verification_Checks on a generated item before review.
- **Verification_Check**: One of: schema validation, independent answering, sandbox command-output
  production, lab-check validation, duplicate detection, coverage, readability, similarity.
- **Verification_Finding**: The recorded result of a failed or disagreeing Verification_Check.
- **Independent_Answerer**: An LLM call, to a different model or provider than the generating call
  where one is available, that answers a generated question without seeing the generated key.
- **Verification_Sandbox**: A Sandbox created through the `SandboxProvider` port in which generated
  scripts, commands and lab checks run.
- **Starting_State**: The initial filesystem and environment of a generated lab.
- **Reference_Solution**: The generated lab solution that must satisfy every generated lab check.
- **Budget**: The cost ceiling recorded in `budget_ledger`, either daily (global) or per track.
- **Cost_Cap**: A ceiling in USD supplied for a single Generation_Run or live run.
- **Variant**: A generated alternative of an approved parent question, with a lifecycle state defined
  in D6 (Generated, Verified, Flagged, PendingReview, Approved, Rejected, Served, Withdrawn).
- **Variant_Pool**: The set of Approved Variants available to serve for one parent question.
- **Pool_Threshold**: The minimum number of Approved Variants a Variant_Pool should hold (initial
  Config value: 3).
- **Parent_Question**: The approved authored question from which Variants are derived.
- **Serving_Code**: The `apps/api` code path that selects questions, lessons and labs for a learner.
- **Reviewer_Queue**: The web UI and API over `review_queue_item` for runtime Variants.
- **Review_Task**: One `review_queue_item`.
- **Reviewer**: A user whose `role_assignment` is reviewer, with per-track expertise.
- **Provenance**: The recorded fields `origin`, `model`, `prompt_id`, `prompt_version`,
  `generation_date`, `reviewed_by`, `reviewed_at`, `content_version` and `stable_id`.
- **Path_Advisor**: The ahead-of-time component that produces a `path_recommendation`.
- **Advisor_Input**: The pseudonymous payload sent to the Path_Advisor LLM call.
- **Pseudonymous_Handle**: A non-identifying handle for a learner that is not derivable from an
  account identifier, name or email address and is not stored in any LLM request log with an identity.
- **Rule_Based_Order**: The deterministic, prerequisite- and mastery-aware ordering that is the
  fallback of the Path_Advisor.
- **Eligible_Item**: An item that exists, is Approved, and is unlocked for the learner.
- **Advisor_Audit_Log**: The record of each Path_Advisor call, its inputs' handle, outcome and source.
- **Problem_Report**: A learner report on a question, lesson or lab.
- **Item_Statistic**: The `item_statistic` row holding `correct_rate` and `avg_time_ms` per item.
- **Report_Threshold**: The number of distinct learners whose Problem_Reports on one item cause the
  item to be flagged (initial Config value: 3).
- **Refill_Cycle**: One scheduled run of the worker that checks each Variant_Pool against the
  Pool_Threshold.
- **Generation_Unit**: The smallest committable output of a Generation_Run: an outline, or one lesson.
  A lesson is complete only when both its FR and EN locales are produced.
- **Flagged_Item**: An item removed from serving because of a Problem_Report or statistics anomaly and
  sent back to review.
- **Eval_Suite**: A set of cases under `evals/`, including the Golden_Set from Phases 2 and 3.
- **Golden_Set**: The seed items approved by the user and marked Golden_Set in Phases 2 and 3.
- **Eval_Report**: The output of `edu-it eval` for the current Prompt versions.
- **Recorded_Payload**: The request payload stored in a Fixture or log.
- **Networking_Track**: The new bilingual track "Networking basics / Bases du réseau".
- **Live_Run**: The final real-model Generation_Run of the Networking_Track outline and first lesson.
- **Phase_4_Report**: The file `docs/reports/phase-4.md`.

## Requirements

## Milestone A: Generation pipeline

### Requirement 1: LLMProvider implementation

**User Story:** As a maintainer, I want all model calls to go through one validated, bounded and
costed port, so that generation is reliable and auditable.

#### Acceptance Criteria

1. THE Live_Provider SHALL implement the LLMProvider port with the signature
   `complete(prompt_version, input, schema, max_retries)`.
2. WHEN the LLMProvider receives a model response, THE LLMProvider SHALL validate the response
   against the supplied content schema before returning a StructuredOutput.
3. WHEN a model response fails schema validation, THE LLMProvider SHALL retry the call until the
   number of attempts reaches 3 (one initial attempt plus at most 2 retries).
4. THE LLMProvider SHALL record the model, input tokens, output tokens and cost in cents of every
   attempt, including failed attempts, in the `generation_job` and the `budget_ledger`.
5. THE LLMProvider SHALL return output only in the form validated against the schema, without
   rewriting, patching or coercing any field.
6. THE domain layer SHALL depend on the LLMProvider port only and SHALL NOT import any provider SDK.
7. WHERE the configured provider is a Live_Provider, THE Live_Provider SHALL use the provider and
   EU data residency recorded in ADR 0007.
8. IF a Live_Provider response was not obtained through the provider and key configured per ADR 0007,
   THEN THE LLMProvider SHALL reject the response, even when the response passes schema validation.
9. IF a Live_Provider response was not obtained through the provider and key configured per ADR 0007,
   THEN THE LLMProvider SHALL fail the call.
10. THE project team SHALL verify the Live_Run vendor adapter with contract tests on recorded
    vendor-shaped responses before the Live_Run.
11. THE project team SHALL record the choice of vendor in an ADR 0007 addendum before any
    Live_Provider call.

### Requirement 2: Record-and-replay fixtures

**User Story:** As a developer, I want recorded fixtures, so that tests never call a live model.

#### Acceptance Criteria

1. WHILE the test environment is active, THE composition root SHALL refuse to construct the
   Live_Provider and THE Replay_Provider SHALL be the only LLMProvider implementation that returns
   recorded model responses.
2. WHEN a Generation_Run is executed in record mode, THE Live_Provider SHALL store each request and
   response as a Fixture keyed by a hash of `prompt_id`, `prompt_version` and `input`.
3. WHEN the Replay_Provider receives a request whose key matches a Fixture, THE Replay_Provider SHALL
   return the recorded response.
4. IF the Replay_Provider receives a request with no matching Fixture, THEN THE Replay_Provider SHALL
   raise an error naming the missing key and SHALL make no network call.
5. THE test suite SHALL include a test that fails when any test run opens a network connection to a
   model endpoint.
6. FOR ALL Fixtures, writing a Fixture and then reading it back SHALL produce an equivalent Fixture
   (round-trip property).

### Requirement 3: Recorded payloads contain no personal data

**User Story:** As a privacy officer, I want recorded and sent payloads to be free of personal data,
so that no learner information reaches the provider or the repository.

#### Acceptance Criteria

1. THE LLMProvider SHALL accept only inputs built from the data allowed in LLM requests by D6
   Section 6.
2. WHEN a Fixture is written, THE Replay_Provider SHALL reject a Recorded_Payload that contains a
   user identifier, name, email address, OAuth identity, session data, raw terminal input or output,
   or learner free text.
3. WHEN the Path_Advisor builds an Advisor_Input, THE Path_Advisor SHALL replace every learner
   identity with a Pseudonymous_Handle.
4. THE test suite SHALL scan every stored Fixture and every Advisor_Input generated by tests and
   SHALL fail when the scan finds a value matching any seeded personal-data sample.

### Requirement 4: Versioned prompts

**User Story:** As a content lead, I want immutable versioned prompts, so that every generated item
can be reproduced and attributed.

#### Acceptance Criteria

1. THE Prompt store SHALL keep each Prompt under `prompts/` in a file whose name contains
   `prompt_id` and `prompt_version`.
2. WHEN a Prompt text needs to change, THE Prompt store SHALL require a new `prompt_version` file and
   SHALL leave the previous version file unchanged.
3. IF a committed Prompt file differs from its content at the previous commit, THEN THE CI check
   SHALL fail with the name of the modified file.
4. THE Glossary_Prompt SHALL be versioned by the same rules as every other Prompt.
5. WHEN the LLMProvider is called, THE LLMProvider SHALL pin `prompt_id` and `prompt_version` in the
   Provenance of every item generated by that call.
6. THE Glossary_Prompt SHALL list the FR and EN term pair for each term used in the Networking_Track
   and in the existing Linux fundamentals content.

### Requirement 5: Staged generation with `edu-it generate`

**User Story:** As an author, I want to generate one stage at a time, so that I can review each stage
before the next one builds on it.

#### Acceptance Criteria

1. THE Generator SHALL support the Stages brief, outline, lessons, quizzes, labs and project, in that
   order.
2. WHEN `edu-it generate` is invoked with a Stage, THE Generator SHALL run exactly that Stage and
   exit.
3. IF `edu-it generate` is invoked with a Stage whose preceding Stage has no merged output, THEN THE
   Generator SHALL exit with an error naming the missing preceding Stage and SHALL call no model.
4. WHEN a Generation_Run starts, THE Generator SHALL create a Generation_Branch whose name contains
   the track and Stage.
5. WHEN a Generation_Run finishes, THE Generator SHALL commit the generated files and the
   Generation_Report on the Generation_Branch and SHALL leave the branch unmerged.
6. THE Generator SHALL write PR_Template with the review checklist for the Stage, covering
   correctness, FR/EN parity, glossary use, sources, and Verification_Finding review.
7. THE repository SHALL contain a CODEOWNERS_File that assigns each track's content path to the
   Reviewers with expertise in that track.
8. THE Generator SHALL record Provenance on every generated item with `origin` set to `ai` and
   `reviewed_by` empty.
9. THE Generator SHALL never merge a Generation_Branch and SHALL never push to a protected branch.
10. THE Generator SHALL write each generated lesson, quiz and lab in both FR and EN.
11. IF `edu-it generate` is invoked with a Stage whose preceding Stage has no merged output, THEN THE
    Generator SHALL create no Generation_Run.
12. IF `edu-it generate` is invoked with a Stage whose preceding Stage has no merged output, THEN THE
    Generator SHALL create no Generation_Branch.
13. THE Generator SHALL provide no flag or option that overrides the behaviour of criterion 3.
14. WHERE a Base_Ref is explicitly named in the `edu-it generate` invocation, THE Generator SHALL
    read the Stage_Markers of the preceding Stage from that Base_Ref, such as the Generation_Branch
    of the preceding Stage, and SHALL merge nothing.
15. IF the Stage_Markers of the preceding Stage are found neither in merged output nor in the named
    Base_Ref, THEN THE Generator SHALL apply the behaviour of criteria 3, 11 and 12.
16. THE Generator SHALL treat a named Base_Ref as a source of Stage_Markers only and SHALL provide no
    flag or option that bypasses the gate of criterion 3.

### Requirement 6: Content validation of AI items

**User Story:** As a maintainer, I want the validator to reject unreviewed AI items, so that none can
be merged into served content.

#### Acceptance Criteria

1. IF an item has `origin` set to `ai` and no `reviewed_by` and `reviewed_at`, THEN `content validate`
   SHALL reject the item with an error naming the item.
2. WHEN `content validate` is run on a Generation_Branch, THE validator SHALL apply the same
   rejection rule as on the default branch.
3. THE `content validate` command SHALL continue to pass every Phase 2 and Phase 3 item.

### Requirement 7: Verification before review

**User Story:** As a reviewer, I want automated checks run first, so that my time goes to judgement
and not to catching mechanical errors.

#### Acceptance Criteria

1. WHEN an item is generated, THE Verifier SHALL run the Verification_Checks that apply to the item
   type before the item is offered for review.
2. WHEN a question is generated, THE Independent_Answerer SHALL answer the question without access to
   the generated key.
3. WHERE a model or provider different from the generating one is configured, THE
   Independent_Answerer SHALL use that different model or provider.
4. IF the Independent_Answerer's answer differs from the generated key, THEN THE Verifier SHALL record
   a disagreement Verification_Finding, SHALL flag the item and SHALL leave the key unchanged.
5. WHEN a generated lesson contains command outputs, THE Verifier SHALL produce those outputs by
   running the commands in a Verification_Sandbox.
6. THE Verifier SHALL run every generated script, command and lab check only inside a
   Verification_Sandbox created through the `SandboxProvider` port.
7. THE Verifier SHALL detect a generated item whose normalised text exactly matches the normalised
   text of an existing item and SHALL record a duplicate Verification_Finding.
8. THE Verifier SHALL compute the share of the track's concepts covered by the generated items and
   SHALL record a finding when a concept has no item.
9. THE Verifier SHALL compute a readability score for each generated lesson in each locale and SHALL
   record a finding when the score is outside the limit in `domain/config.py`.
10. THE Verifier SHALL compare each generated text with the known-source corpus and SHALL record a
    finding when the similarity exceeds the limit in `domain/config.py`.
11. THE Verifier SHALL never alter, delete or hide a generated item or a Verification_Finding.
12. THE duplicate detection of criterion 7 SHALL use exact matching of normalised text only, and
    near-similarity SHALL be covered solely by the similarity check of criterion 10.

### Requirement 8: Lab check verification

**User Story:** As a reviewer, I want generated lab checks proven against both states, so that a lab
cannot be passed by doing nothing or failed by doing the right thing.

#### Acceptance Criteria

1. WHEN a lab is generated, THE Verifier SHALL provision a Verification_Sandbox from the Starting_State
   and SHALL run every generated lab check.
2. IF a generated lab check passes on the Starting_State, THEN THE Verifier SHALL record a
   Verification_Finding and SHALL flag the lab.
3. WHEN a lab is generated, THE Verifier SHALL apply the Reference_Solution in a Verification_Sandbox
   and SHALL run every generated lab check.
4. IF a generated lab check fails on the Reference_Solution, THEN THE Verifier SHALL record a
   Verification_Finding and SHALL flag the lab.
5. WHEN both the Starting_State checks and the Reference_Solution checks of a lab have finished or
   failed, THE Verifier SHALL destroy the Verification_Sandboxes of that lab.
6. THE Verifier SHALL apply the grading bounds of #[[file:.kiro/steering/labs.md]] to each check run.
7. THE Verifier SHALL treat the Starting_State and Reference_Solution Verification_Sandboxes of one lab
   as one unit and SHALL destroy none of them before both sets of checks have finished or failed.

### Requirement 9: Generation report

**User Story:** As a reviewer, I want every verification outcome in the report, so that nothing is
hidden from my review.

#### Acceptance Criteria

1. THE Generation_Report SHALL list each generated item with its Provenance, tokens and cost.
2. THE Generation_Report SHALL list every Verification_Finding, including every failure and every
   disagreement.
3. FOR ALL Generation_Runs, the number of Verification_Findings in the Generation_Report SHALL equal
   the number of Verification_Findings recorded by the Verifier.
4. THE Generation_Report SHALL state the Budget remaining at the end of the Generation_Run.

### Requirement 10: Budgets

**User Story:** As a project owner, I want daily and per-track budgets, so that generation spend is
bounded.

#### Acceptance Criteria

1. THE Generator SHALL enforce a daily Budget and a per-track Budget, both read from the
   `budget_ledger` and `domain/config.py`.
2. WHERE a Cost_Cap is supplied to a Generation_Run, THE Generator SHALL enforce the Cost_Cap in
   addition to the Budgets.
3. WHEN a Generation_Run starts and a Budget is already reached, THE Generator SHALL exit before any
   model call and SHALL report the Budget that was reached.
4. THE Generator SHALL evaluate the Budgets before every model call, including every retry attempt.
5. THE Generator SHALL evaluate the Budgets on every attempt without exception, as an unconditional
   invariant that no flag, option or mode disables.

### Requirement 11: Evals

**User Story:** As a content lead, I want to measure quality per prompt version, so that prompt changes
are judged on evidence.

#### Acceptance Criteria

1. WHEN `edu-it eval` is invoked, THE Eval_Suite runner SHALL run the Golden_Set cases from Phases 2
   and 3 against the current Prompt versions.
2. WHEN `edu-it eval` finishes, THE runner SHALL write an Eval_Report naming each Prompt version,
   each case outcome and the aggregate pass rate.
3. WHILE the test environment is active, THE `edu-it eval` command SHALL use the Replay_Provider.
4. WHERE the command is invoked with the live option, THE runner SHALL apply the Budgets of
   Requirement 10.
5. FOR ALL Eval_Suite runs with the same Fixtures and Prompt versions, THE runner SHALL produce the
   same Eval_Report (determinism).

### Requirement 12: Failure scenarios of generation

**User Story:** As an author, I want predictable behaviour when generation fails, so that I can trust
what is left on the branch.

#### Acceptance Criteria

1. IF the LLM returns malformed output after all 3 attempts, THEN THE Generator SHALL flag the item,
   SHALL not correct the output, SHALL record the item and its failure in the Generation_Report and
   SHALL record the tokens and cost of every attempt.
2. IF the LLM provider is unavailable during a Stage, THEN THE Generator SHALL stop the Stage, SHALL
   commit the items completed so far and the Generation_Report listing the items not generated, and
   SHALL exit with a non-zero status.
3. IF the LLM provider is unavailable during a Stage, THEN THE Generator SHALL record the cost of
   completed and failed attempts.
4. IF a Budget is reached during a Generation_Run, THEN THE Generator SHALL make no further model
   call, SHALL commit the fully completed Generation_Units with the Generation_Report listing the
   items not generated, and SHALL exit with a message naming the Budget.
5. IF the Independent_Answerer disagrees with the generated key, THEN THE Generator SHALL keep the
   generated key, SHALL flag the item and SHALL list the disagreement in the Generation_Report.
6. WHEN a Stage is run again after a stop, THE Generator SHALL resume without regenerating items
   already committed, keyed by `stable_id`.
7. IF a Budget is reached while a Generation_Unit is partially produced, THEN THE Generator SHALL
   discard the partial Generation_Unit and SHALL commit only fully completed Generation_Units.
8. IF a Generation_Unit is discarded because a Budget was reached, THEN THE Generator SHALL list the
   discarded Generation_Unit in the Generation_Report.

## Milestone B: Variants and review

### Requirement 13: Variant-pool refill

**User Story:** As an operator, I want pools refilled ahead of time, so that learners never wait for
a model.

#### Acceptance Criteria

1. WHEN the number of Approved Variants of a Parent_Question falls below the Pool_Threshold, THE
   worker SHALL enqueue a refill job for that Parent_Question.
2. WHEN a refill job generates a Variant, THE worker SHALL run the Verification_Checks of
   Requirement 7 on the Variant.
3. WHEN a Variant passes all Verification_Checks, THE worker SHALL set its lifecycle state to
   PendingReview and SHALL create a Review_Task.
4. WHEN a Variant fails a Verification_Check or the Independent_Answerer disagrees, THE worker SHALL
   set its lifecycle state to Flagged and SHALL store the Verification_Finding.
5. THE worker SHALL enforce the Budgets and the retry bound of Requirement 1 in every refill job.
6. THE worker SHALL generate Variants only from a Parent_Question whose state is Approved.
7. IF a refill job meets malformed output, an unavailable provider or a reached Budget, THEN THE
   worker SHALL apply the behaviour of Requirement 12 and SHALL create no Variant in a servable state.
8. IF a refill job stops on malformed output, an unavailable provider or a reached Budget, THEN THE
   Variant_Pool SHALL remain below the Pool_Threshold until the next Refill_Cycle.
9. IF a refill job stops on malformed output, an unavailable provider or a reached Budget, THEN THE
   worker SHALL start no new refill job for that Parent_Question before the next Refill_Cycle.
10. IF a refill job stops on malformed output, an unavailable provider or a reached Budget, THEN THE
    worker SHALL perform no escalation.

### Requirement 14: Approved-only serving

**User Story:** As a learner, I want only expert-approved content, so that I can trust what I study.

#### Acceptance Criteria

1. THE Serving_Code SHALL return only items whose lifecycle state is Approved or Served.
2. THE Serving_Code SHALL NOT return an item that is Unapproved, PendingReview, Generated, Verified,
   Flagged, Rejected, Withdrawn or retired.
3. THE Serving_Code SHALL NOT call the LLMProvider.
4. THE API tests SHALL request every item state through every serving endpoint and SHALL assert that
   no non-approved item is returned.
5. THE worker tests SHALL assert that the worker selects only approved items when it builds review
   and practice sets.
6. FOR ALL sets of items with arbitrary lifecycle states, the items returned by the Serving_Code SHALL
   be a subset of the Approved items (invariant).

### Requirement 15: Reviewer queue

**User Story:** As a reviewer, I want a queue of variants in my tracks with all evidence, so that I
can decide quickly and safely.

#### Acceptance Criteria

1. THE Reviewer_Queue SHALL list only Review_Tasks for tracks in which the signed-in Reviewer has
   expertise.
2. WHEN a Reviewer opens a Review_Task, THE Reviewer_Queue SHALL show the Variant in FR and EN, its
   Provenance and every Verification_Finding.
3. THE Reviewer_Queue SHALL offer the actions approve, edit, request changes and reject.
4. WHEN a Reviewer approves a Review_Task, THE Reviewer_Queue SHALL set the Variant to Approved and
   SHALL record `reviewed_by` and `reviewed_at`.
5. WHEN a Reviewer rejects a Review_Task, THE Reviewer_Queue SHALL set the Variant to Rejected and
   SHALL record `reviewed_by` and `reviewed_at`.
6. WHEN a Reviewer requests changes, THE Reviewer_Queue SHALL return the Variant to PendingReview
   with the Reviewer's comment attached.
7. WHEN a Reviewer edits a Variant, THE Reviewer_Queue SHALL save a new `content_version`, SHALL
   record the Reviewer in Provenance and SHALL require an approval action before the edited Variant
   is Approved.
8. IF a user without reviewer expertise in the track of a Review_Task tries to act on it, THEN THE
   API SHALL reject the action with an RFC 9457 problem document with status 403.
9. THE Reviewer_Queue SHALL be available in FR and EN and SHALL pass the axe-core accessibility checks
   used for the learner pages.
10. THE API SHALL list Review_Tasks with cursor pagination as defined in
    #[[file:.kiro/steering/api-conventions.md]].
11. IF an action on a Review_Task is denied for lack of expertise, THEN THE Reviewer_Queue SHALL leave
    the Variant status and the Review_Task entirely unchanged.
12. IF an action on a Review_Task is denied for lack of expertise, THEN THE Reviewer_Queue SHALL record
    no state transition.

### Requirement 16: Reviewer edit of a served item

**User Story:** As a reviewer, I want edits to a live item to be safe, so that no learner sees
unreviewed text and no progress is lost.

#### Acceptance Criteria

1. IF a Reviewer edits an item that is currently being served, THEN THE Reviewer_Queue SHALL keep
   serving the previously Approved `content_version` until the edited version is Approved.
2. IF a Reviewer edits an item that is currently being served, THEN THE Reviewer_Queue SHALL create a
   Review_Task for the edited version.
3. WHEN an edited version is Approved, THE Serving_Code SHALL serve the new version to new attempts
   and SHALL keep existing completions and progress.
4. IF a Reviewer rejects the edited version, THEN THE Serving_Code SHALL continue to serve the
   previously Approved version.

## Milestone C: Personalization and quality loop

### Requirement 17: Path advisor

**User Story:** As a learner, I want a suggested next step, so that I spend time on what helps me
most.

#### Acceptance Criteria

1. WHEN the Path_Advisor runs, THE Path_Advisor SHALL build an Advisor_Input from per-concept
   mastery, completion state, review-schedule state and catalog prerequisites, referenced by a
   Pseudonymous_Handle.
2. THE Path_Advisor SHALL run in the worker ahead of time and SHALL persist a `path_recommendation`
   with its `source`.
3. THE Path_Advisor SHALL restrict its output to Eligible_Items.
4. IF the Path_Advisor LLM returns an unknown, unapproved or locked ID, THEN THE Path_Advisor SHALL
   discard the LLM output, SHALL persist the Rule_Based_Order with `source` set to rule-based and
   SHALL record the reason in the Advisor_Audit_Log.
5. IF the Path_Advisor LLM call fails, times out or returns malformed output after all attempts, THEN
   THE Path_Advisor SHALL persist the Rule_Based_Order with `source` set to rule-based.
6. THE Path_Advisor SHALL write an Advisor_Audit_Log entry for every run, with the Pseudonymous_Handle,
   source, outcome and the identifiers returned.
7. THE dashboard SHALL show the latest `path_recommendation` and a dismiss action.
8. WHEN a learner dismisses a recommendation, THE API SHALL record the dismissal and THE dashboard
   SHALL stop showing that recommendation.
9. FOR ALL Advisor outputs, every item in the persisted ordering SHALL be an Eligible_Item
   (invariant).

### Requirement 18: Report a problem

**User Story:** As a learner, I want to report a problem, so that wrong content gets fixed.

#### Acceptance Criteria

1. THE dashboard SHALL offer "Report a problem" on each question, lesson and lab view, in FR and EN.
2. WHEN a learner submits a Problem_Report, THE API SHALL store the report with the item's `stable_id`
   and `content_version`, a category and an optional comment.
3. WHEN a learner re-submits the same Problem_Report with the same `Idempotency-Key`, THE API SHALL
   store it once and return the original result.
4. WHEN the number of distinct learners who reported the item's current `content_version` reaches
   the Report_Threshold, THE worker SHALL treat the item as a Flagged_Item and SHALL withdraw the item from serving.
5. THE API SHALL reject a Problem_Report comment longer than the limit in `domain/config.py` with an
   RFC 9457 problem document with status 422.
6. THE worker SHALL NOT send a Problem_Report comment to the LLMProvider.
7. WHEN an item becomes a Flagged_Item by reaching the Report_Threshold, THE worker SHALL notify the
   content authors and Reviewers by creating a Review_Task visible in the Reviewer_Queue.
8. THE Review_Task SHALL be the only notification channel for a Flagged_Item.
9. THE system SHALL clear the flag of a Flagged_Item only by a Reviewer decision on the Review_Task of
   that Flagged_Item.

### Requirement 19: Item statistics and flagging

**User Story:** As a content lead, I want statistics to catch bad items, so that they leave circulation
quickly.

#### Acceptance Criteria

1. WHEN an answer is recorded, THE worker SHALL update the Item_Statistic `correct_rate` and
   `avg_time_ms` of the answered item.
2. WHEN an Item_Statistic has at least the minimum sample count in `domain/config.py` and its
   `correct_rate` or `avg_time_ms` is outside the bounds in `domain/config.py`, THE worker SHALL treat
   the item as a Flagged_Item.
3. WHEN an item becomes a Flagged_Item, THE worker SHALL withdraw it from its Variant_Pool, SHALL set a
   Variant to Withdrawn, and SHALL create a Review_Task listing the report count or anomaly.
4. WHEN a Reviewer approves a Review_Task for a Flagged_Item, THE Reviewer_Queue SHALL return the item
   to serving.
5. WHEN a Reviewer rejects a Review_Task for a Flagged_Item, THE Reviewer_Queue SHALL keep the item out
   of serving.

### Requirement 20: Emptied variant pool

**User Story:** As a learner, I want practice to continue when variants are withdrawn, so that I am
never blocked.

#### Acceptance Criteria

1. IF flagging empties a Variant_Pool, THEN THE Serving_Code SHALL serve the Approved Parent_Question.
2. IF flagging empties a Variant_Pool, THEN THE worker SHALL enqueue a refill job for the
   Parent_Question.
3. IF the Parent_Question is itself a Flagged_Item, THEN THE Serving_Code SHALL serve neither the
   Parent_Question nor its Variants and SHALL select another Approved question for the concept.
4. IF a concept has no servable question, THEN THE API SHALL return an RFC 9457 problem document with
   a translated, actionable `detail`.
5. IF the Parent_Question is a Flagged_Item, its Variant_Pool is empty and no other Approved question
   exists for the concept, THEN THE API SHALL return the RFC 9457 problem document of criterion 4.

### Requirement 21: GDPR coverage

**User Story:** As a learner, I want my reports and recommendations covered by export and deletion, so
that my data rights are complete.

#### Acceptance Criteria

1. WHEN a learner requests an export, THE API SHALL include the learner's Problem_Reports,
   `path_recommendation` rows and dismissals.
2. WHEN a learner requests deletion, THE API SHALL delete the learner's Problem_Reports,
   `path_recommendation` rows, dismissals and Advisor_Audit_Log entries that are linked to the learner.
3. WHEN a learner is deleted, THE system SHALL retain the aggregate Item_Statistic values with no
   learner link.
4. THE retention policy SHALL cover each new table that holds personal data.
5. FOR ALL learners, the export, after deletion, SHALL contain no Problem_Report or
   `path_recommendation` of that learner.
6. THE change that introduces each new table holding personal data SHALL register that table for
   export, deletion and retention in the same change.

### Requirement 22: Proof of the core invariant

**User Story:** As a project owner, I want every milestone to prove the core invariant, so that I can
trust the platform.

#### Acceptance Criteria

1. THE Milestone A tests SHALL assert that a Generation_Run produces no item with `reviewed_by` set
   and no item in a servable state.
2. THE Milestone B tests SHALL assert the approved-only serving of Requirement 14 at the API and at the
   worker.
3. THE Milestone C tests SHALL assert that a Flagged_Item and a recommendation never contain a
   non-approved item.
4. THE test suite SHALL include no test that calls a live model.
5. WHEN a Variant is Rejected, retired or Flagged, THE system SHALL never return it from any serving
   endpoint.
6. THE guarantee of criterion 5 SHALL apply to serving requests received after the state change.
7. WHEN a Variant becomes Rejected, retired or Flagged, THE system SHALL leave copies already served
   or cached unchanged.

### Requirement 23: Quality gates and traceability

**User Story:** As a maintainer, I want the usual project gates applied to this phase, so that quality
stays level with earlier phases.

#### Acceptance Criteria

1. THE repository SHALL pass lint, type checks, unit tests, property tests and `content validate` after
   each milestone.
2. THE Phase_4_Report SHALL map each requirement of this document to at least one automated test.
3. THE new LLM, generation, review and advisor code SHALL follow the layering rules of
   #[[file:.kiro/steering/structure.md]].
4. THE new tables SHALL be added by additive migrations that keep existing rows intact.
5. WHEN a milestone ends, THE project team SHALL check the traceability from the requirements of
   that milestone to automated tests.

### Requirement 24: Live run of the Networking track

**User Story:** As the project owner, I want a real run on a small new track, so that I can judge the
output with my own eyes before anything is merged.

#### Acceptance Criteria

1. WHEN the Live_Run starts, THE Generator SHALL apply a Cost_Cap of USD 20.
2. THE Live_Run SHALL generate the outline and the first lesson of the Networking_Track in FR and EN.
3. THE Generator SHALL write the Live_Run output on a Generation_Branch that remains unmerged and
   SHALL not merge it.
4. THE Live_Run SHALL use the Live_Provider and SHALL run all Verification_Checks of Requirement 7.
5. IF the cumulative cost of the Live_Run reaches USD 20 while a Generation_Unit is partially
   produced, THEN THE Generator SHALL discard the partial Generation_Unit.
6. THE Generation_Report of the Live_Run SHALL list the total cost in USD, the tokens, the models, and
   every Verification_Finding.
7. THE Networking_Track title SHALL be "Networking basics" in EN and "Bases du réseau" in FR.
8. WHEN the Live_Run finishes, THE Generator SHALL leave the branch open for the project owner's
   review and SHALL not mark any generated item as reviewed.
9. IF the cumulative cost of the Live_Run reaches USD 20, THEN THE Generator SHALL stop and apply the
   behaviour of Requirement 12.4.
10. IF the cumulative cost of the Live_Run reaches USD 20, THEN THE Generator SHALL commit only fully
    completed Generation_Units, being the outline and lessons complete in both locales.
11. IF a Generation_Unit is discarded because the cost reached USD 20, THEN THE Generator SHALL list
    the discarded Generation_Unit in the Generation_Report.
12. WHEN the Live_Run runs the lessons Stage while the outline is unmerged, THE Generator SHALL read
    the Stage_Markers of the preceding Stage from the named Base_Ref of Requirement 5.14.
13. THE project owner SHALL hand-author and merge the Networking_Track brief before the Live_Run.

### Requirement 25: Phase 4 report

**User Story:** As the project owner, I want a phase report, so that I can see what was proven and
what it cost.

#### Acceptance Criteria

1. THE Phase_4_Report SHALL be written to `docs/reports/phase-4.md` after the Live_Run.
2. THE Phase_4_Report SHALL state the Live_Run cost in USD, the Cost_Cap, the models used and the
   Generation_Branch name.
3. THE Phase_4_Report SHALL include the Eval_Report results for the current Prompt versions.
4. THE Phase_4_Report SHALL list the command and outcome of each acceptance check, in the form of
   `docs/reports/phase-3.md`.
5. THE Phase_4_Report SHALL list what was not verified.
6. IF an acceptance check did not run or did not pass, THEN THE report generator SHALL not write the
   Phase_4_Report as passing and SHALL name the check.
