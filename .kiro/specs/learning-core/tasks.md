# Implementation Plan: Learning Core

## Overview

This plan implements the learning core of EDU-IT across three milestones, honoring the layering
`api → application → domain ← infrastructure`, the injected `Clock`, and the rule that every
business number lives in `packages/domain/edu_it_domain/config.py` (never hardcoded in services).

Milestone A follows Requirement 11 strictly: for every domain service the rules-based tests and the
property-based Hypothesis tests are authored **before** the implementation. In the task ordering
below, each domain service's test sub-task (`*`) is listed immediately before its implementation
sub-task. For the domain engine (Milestone A, Requirement 11), each service's rules-based and
property test sub-task is placed in an **earlier** wave than its implementation sub-task in the Task
Dependency Graph, so the rules-first ordering is explicit and enforced. For Milestones B and C, where
rules-first is not mandated, a test task may share a wave with or follow the code it validates.

Property-based tests use Hypothesis (already a workspace dependency), run a minimum of 100 iterations,
read numbers from `domain/config.py`, are driven by the fake `Clock` (`Test_Clock`), and are tagged
with a comment of the form `Feature: learning-core, Property {n}: {property text}` per the design's
Testing Strategy. Each property task references its Property number and the requirements it validates.

Languages: Python for `packages/domain`, `packages/application`, `packages/infrastructure`, and the
`edu-it` CLI; Python + FastAPI + Pydantic v2 for `apps/api`; TypeScript + Next.js App Router for
`apps/web`. No new stack is introduced.

## Tasks

- [x] 1. Extend domain config and add learning ports + fakes (Milestone A foundation)
  - [x] 1.1 Extend `domain/config.py` with all learning business numbers
    - Add cooldown interval, mastery thresholds (30/60/85) and bands, decay rate (-1/day) and floor (30), initial/passed/failed review intervals, hint multiplier, rubric pass threshold, minimum eligible-question count (3), 24-hour anti-farming window
    - All as named constants read by the domain services; no value hardcoded in services
    - _Requirements: 1.3, 3.1, 4.3, 5.1, 7.3, 8.1, 9.4, 9.5, 10.2, 28.3_
  - [x] 1.2 Define framework-free domain types in `learning/models.py`
    - `MasteryState` enum, `Mastery`, `MasteryEvent`, `ReviewSchedule`, `QuestionOption`, `Answer`, `QuizAttempt`, `Rubric`/`RubricScore`, `ProjectSubmissionRef` dataclasses mirroring D3
    - _Requirements: 1.1, 5.1, 5.7, 9.1_
  - [x] 1.3 Add new domain ports `ProjectReviewer` and `LabCheck` plus learning repository ports
    - `ports/project_reviewer.py`, `ports/lab_check.py`, and repository ports for catalog/enrollment/progress/mastery/review/attempts following the existing `@runtime_checkable` Protocol pattern; reuse the existing `Clock` port unchanged
    - _Requirements: 1.1, 1.2, 1.4, 10.4_
  - [x] 1.4 Implement infrastructure fakes for the new ports
    - `fakes/project_reviewer.py` (`Fake_ProjectReviewer`, configurable score + pending), `fakes/lab_check.py` (`Fake_LabCheck`, configurable coverage + pass), in-memory learning repositories; reuse existing `Test_Clock`
    - _Requirements: 1.4, 10.4, 10.5, 11.4_

- [x] 2. Unlocking, completion, and mastery services (rules-first)
  - [x] 2.1 Write rules-based + property tests for unlocking/completion BEFORE implementation
    - Rules tests: lock while prerequisite unmet, unlock when all met, completion recording, advance only by mastery, decay never relocks
    - **Property 1: Module lock reflects prerequisite mastery** — _Validates: Requirements 2.1, 2.2_
    - **Property 2: Decay never relocks and only re-queues review** — _Validates: Requirements 2.5, 8.3_
    - Driven by `Test_Clock`, ≥100 iterations, numbers from `config.py`
    - _Requirements: 11.1, 11.2, 11.4, 2.1, 2.2, 2.3, 2.4, 2.5_
  - [x] 2.2 Implement `learning/unlocking.py`
    - Compute lock/unlock from prerequisite mastery states; record completion; decay never relocks
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5_
  - [x] 2.3 Write rules-based + property tests for mastery BEFORE implementation
    - Rules tests: state bands, Mastered gate (incl. lab-check path), cap/floor, one event per change
    - **Property 7: Mastery stays within 0–100** — _Validates: Requirements 5.1, 5.8_
    - **Property 8: Mastery state matches its threshold band and gate** — _Validates: Requirements 5.2, 5.3, 5.5, 5.6_
    - **Property 9: Every mastery change appends exactly one event** — _Validates: Requirements 5.7_
    - **Property 11: Decay below a threshold once Practiced never falls below Practiced or 30** — _Validates: Requirements 5.9, 5.10, 5.11, 8.2, 8.5_
    - _Requirements: 11.1, 11.2, 11.4, 5.1, 5.2, 5.3, 5.5, 5.6, 5.7, 5.8, 5.9, 5.10, 5.11_
  - [x] 2.4 Implement `learning/mastery.py`
    - Apply deltas, derive states/thresholds from config, cap 0–100, append `mastery_event`, floor-30-once-Practiced, band transitions on decay, lab-check path for Mastered gate
    - _Requirements: 5.1, 5.2, 5.3, 5.5, 5.6, 5.7, 5.8, 5.9, 5.10, 5.11, 1.4_

- [x] 3. Checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 4. Scoring, cooldown, anti-farming, and hints services (rules-first)
  - [x] 4.1 Write rules-based + property tests for scoring/shuffle/matching BEFORE implementation
    - Rules tests: percent-correct scoring, unanswered = incorrect, foreign-option rejection, mixed types incl. command-output, best-attempt selection
    - **Property 3: Quiz score equals the percentage of correct answers** — _Validates: Requirements 3.1, 3.5_
    - **Property 4: Option shuffle is a deterministic permutation preserving correctness** — _Validates: Requirements 3.2, 3.3_
    - **Property 5: Best attempt is the highest score, ties broken by recency** — _Validates: Requirements 3.6, 6.3_
    - _Requirements: 11.1, 11.2, 11.4, 3.1, 3.2, 3.3, 3.4, 3.5, 3.6_
  - [x] 4.2 Implement `learning/scoring.py`
    - Score 0–100 (unanswered = incorrect), deterministic seeded shuffle, map shuffled position back to original option identity, reject foreign-option answers, mixed/command-output types, best-attempt (tie → most recent)
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6_
  - [x] 4.3 Write rules-based + property tests for cooldown BEFORE implementation
    - **Property 6: Cooldown holds exactly until the interval elapses** — _Validates: Requirements 4.1, 4.2_
    - _Requirements: 11.1, 11.2, 11.4, 4.1, 4.2, 4.3_
  - [x] 4.4 Implement `learning/cooldown.py`
    - Report cooldown from last-attempt time + Clock + Config interval
    - _Requirements: 4.1, 4.2, 4.3_
  - [x] 4.5 Write rules-based + property tests for the 24-hour anti-farming rule BEFORE implementation
    - **Property 12: Re-answers inside the 24-hour window earn no additional mastery** — _Validates: Requirements 6.1, 6.2, 6.4, 6.5_
    - Include the exact 24-hour boundary edge case; driven by `Test_Clock`
    - _Requirements: 11.1, 11.2, 11.4, 6.1, 6.2, 6.3, 6.4, 6.5_
  - [x] 4.6 Implement `learning/antifarming.py`
    - Decide `counted_for_mastery` from the 24h window (`first_answered_at + 24h`, Clock); zero gain inside the window; standard rules at/after the boundary
    - _Requirements: 6.1, 6.2, 6.4, 6.5_
  - [x] 4.7 Write rules-based + property tests for hints BEFORE implementation; add hint-reveal example test
    - **Property 13: Hint multiplier applies only to pre-submission reveals** — _Validates: Requirements 7.2, 7.4_
    - Example test: a hint reveal records one `hint_reveal` and one `mastery_event` (Req 7.1)
    - _Requirements: 11.1, 11.2, 11.4, 7.1, 7.2, 7.3, 7.4_
  - [x] 4.8 Implement `learning/hints.py`
    - Apply Config hint multiplier only when a hint was revealed before submission; never retroactively
    - _Requirements: 7.1, 7.2, 7.3, 7.4_

- [x] 5. Checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 6. Decay, review scheduling/selection, and rubric services (rules-first)
  - [x] 6.1 Write rules-based + property tests for decay BEFORE implementation
    - **Property 14: Decay is a deterministic, idempotent function of the clock and schedule** — _Validates: Requirements 8.1, 8.4_
    - Include overdue-day boundary edge cases; driven by `Test_Clock`
    - _Requirements: 11.1, 11.2, 11.4, 8.1, 8.2, 8.3, 8.4, 8.5_
  - [x] 6.2 Implement `learning/decay.py`
    - Deterministic `-1/day overdue` from Clock + schedule; idempotent per instant; re-queue not relock (ADR A6)
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5_
  - [x] 6.3 Write rules-based + property tests for review scheduling/selection BEFORE implementation
    - **Property 10: One review entry on first Practiced, resumed on re-practice** — _Validates: Requirements 5.4, 9.1, 9.2_
    - **Property 15: Review selection draws only from the eligible pool** — _Validates: Requirements 9.3_
    - **Property 16: Passing lengthens and failing shortens the review interval** — _Validates: Requirements 9.4, 9.5_
    - **Property 17: Due and overdue derive purely from the clock** — _Validates: Requirements 9.6_
    - **Property 19: Reviews below the minimum eligible pool are declined without scheduling** — _Validates: Requirements 28.1, 28.2, 28.3_
    - Include empty-pool and <3-minimum edge cases
    - _Requirements: 11.1, 11.2, 11.4, 5.4, 9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 28.1, 28.2, 28.3_
  - [x] 6.4 Implement `learning/review.py`
    - One schedule entry on first Practiced; resume-on-re-practice; due/overdue computation; interval change on pass/fail; eligible-question selection incl. empty pool and <3 minimum decline
    - _Requirements: 5.4, 9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 28.1, 28.2, 28.3_
  - [x] 6.5 Write rules-based + property tests for rubric BEFORE implementation
    - **Property 18: Rubric outcome follows the weighted score against the threshold** — _Validates: Requirements 10.1, 10.2, 10.3, 10.5_
    - Include the pending-reviewer case (no outcome while pending)
    - _Requirements: 11.1, 11.2, 11.4, 10.1, 10.2, 10.3, 10.4, 10.5_
  - [x] 6.6 Implement `learning/rubric.py`
    - Evaluate weighted criteria vs `max_points`; pass/fail at Config threshold; outcome only after `ProjectReviewer` completes
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5_

- [x] 7. Encode the worked mastery example and enforce the domain coverage gate
  - [x] 7.1 Write the worked-example end-to-end domain test from `docs/RULES-REVIEW.md` D7 §9
    - Confirm mastery trajectory, hint temper, and overdue-review decay using `Test_Clock`
    - _Requirements: 11.2, 11.4_
  - [x] 7.2 Configure the ≥90% domain statement-coverage gate in CI
    - Add a coverage threshold on the domain package to `make test`/CI so a drop fails the build
    - _Requirements: 11.3, 30.1_

- [x] 8. Checkpoint — Ensure all tests pass and domain coverage ≥90%
  - Ensure all tests pass, ask the user if questions arise.

- [x] 9. Content schemas with provenance (Milestone B)
  - [x] 9.1 Define Pydantic v2 content schemas and shared types
    - One model per content type (`track`, `module`, `lesson`, `lab`, `lab_task`, `lab_check`, `project`, `rubric`, `rubric_criterion`, `quiz`, `question`, `question_option`, `concept`, `prerequisite`); shared `Provenance` model; reusable `Localized` type requiring both `fr` and `en`
    - _Requirements: 12.1, 12.2, 12.3, 12.4_
  - [x] 9.2 Write schema tests (valid item parses; missing locale or provenance fails)
    - _Requirements: 12.2, 12.3_

- [x] 10. `edu-it content validate` command
  - [x] 10.1 Implement the `edu-it` CLI skeleton and `content validate` ordered checks
    - Schema validation, bilingual completeness, reference resolution, prerequisite acyclicity, MDX allowlist, AI-reviewer-present; each failure names the item `stable_id` and the rule; exit non-zero on any error, zero when all valid
    - _Requirements: 13.1, 13.2, 13.3, 13.4, 13.5, 13.6_
  - [x] 10.2 Write property test for prerequisite acyclicity + per-check failing fixtures
    - **Property 20: The prerequisite graph is acyclic or the cycle is reported** — _Validates: Requirements 13.4_
    - One failing fixture per check and the all-valid exit-zero case
    - _Requirements: 13.1, 13.2, 13.3, 13.5, 13.6_

- [x] 11. Idempotent `edu-it content sync` command
  - [x] 11.1 Implement `content sync` idempotent upsert keyed by `stable_id`
    - Upsert, bump `content_version` on change, never duplicate; no-op on unchanged second run; preserve `lesson_completion`/completions while resetting other learner state tied to edited items; archive (not hard-delete) removed items; transactional per item
    - _Requirements: 14.1, 14.2, 14.3, 14.4, 26.1_
  - [x] 11.2 Write property test for sync idempotence and completion preservation
    - **Property 21: Content sync is an idempotent upsert that preserves completions** — _Validates: Requirements 14.1, 14.2, 14.3, 14.4, 26.1_
    - _Requirements: 14.1, 14.2, 14.3, 14.4, 26.1_
  - [x] 11.3 Implement deferred-archive behavior for removed items during active use
    - Defer archiving a removed item until active sessions referencing its `stable_id` complete; never hard-delete; in-flight attempts stay pinned to their `content_version`
    - _Requirements: 26.2, 26.3_
  - [x] 11.4 Write property test for deferred archiving during active use
    - **Property 25: Removed items are archived only after active sessions complete** — _Validates: Requirements 26.2_
    - _Requirements: 26.2_

- [x] 12. CI content step
  - [x] 12.1 Add a CI step named `content` that runs `edu-it content validate`
    - Fail the pipeline non-zero and name the step on any validation error; exit zero when all content is valid
    - _Requirements: 14.5, 27.1, 27.2_

- [x] 13. Checkpoint — Ensure content schema, validate, sync, and CI tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 14. Author seed content (origin ai) for "Linux Fundamentals"
  - [x] 14.1 Author the track, modules, lessons, quizzes, project, rubric, and prerequisites
    - Track "Linux Fundamentals"; module "Files & Permissions" (lessons "Reading permissions" and "Changing permissions and ownership"); a 5-question mixed-type quiz incl. one command-output question per lesson; project "Harden a shared project directory" with rubric criteria correctness/least privilege/idempotency/explanation; module "Processes" (placeholder lesson) requiring "Files & Permissions"; natural `fr`/`en` for every learner-facing field; `origin` `ai`
    - Note: the "Processes" module's placeholder lesson intentionally has NO quiz — the two 5-question quizzes are only for the two "Files & Permissions" lessons (Req 15.3) — so do not add a quiz to it
    - _Requirements: 15.1, 15.2, 15.3, 15.4, 15.5, 15.6_
  - [x] 14.2 Validate the authored seed content with `edu-it content validate`
    - Confirm the authored content passes all validation checks before review
    - _Requirements: 13.1, 15.1, 15.6_

- [x] 15. Expert-review checkpoint — STOP and ask the user to review seed content
  - STOP here and ask the user to review the authored Seed_Content before any further content step. Do not sync, serve, mark golden, or attribute a reviewer until the user responds. If the user does not approve, mark no golden set, record no reviewer, and sync/serve nothing.
  - _Requirements: 16.1, 16.4_

- [x] 16. Record approval and mark the golden set
  - [x] 16.1 On approval, record the reviewer in provenance and mark the golden set
    - `content sync` records the approving user as `reviewed_by`/`reviewed_at`; mark the approved content as the `Golden_Set` for Phase 4 evals
    - _Requirements: 16.2, 16.3_
  - [x] 16.2 Write tests for approval attribution and golden-set marking (and the rejection path)
    - Approval records reviewer + marks golden; rejection syncs/serves nothing and marks no golden set
    - _Requirements: 16.2, 16.3, 16.4_

- [x] 17. Learning API — application use cases (Milestone C)
  - [x] 17.1 Implement application use cases over the domain services and repository ports
    - enroll / progress / attempt-quiz / reveal-hint / run-review / submit-project; load state via ports, call domain, persist results; enforce idempotency, cooldown, and version pinning before any side effect
    - _Requirements: 17.1, 17.2, 18.1, 18.3, 18.4, 18.5, 20.3, 20.4_
  - [x] 17.2 Implement content-version pinning for in-flight attempts in the attempt-quiz use case
    - Record `content_version` at attempt start; keep active attempt pinned on mid-attempt edits; new attempt uses new version; leave existing progress rows intact
    - _Requirements: 25.1, 25.2, 25.3_
  - [x] 17.3 Write property tests for version pinning
    - **Property 24: An active attempt stays pinned to its content version** — _Validates: Requirements 25.1, 25.2, 25.3, 26.3_
    - _Requirements: 25.1, 25.2, 25.3, 26.3_

- [x] 18. Learning API — FastAPI routers, idempotency, and problem details
  - [x] 18.1 Implement learning routers under `/v1` with Pydantic v2 request/response and stable-id addressing
    - `POST /v1/enrollments`, `GET /v1/progress`, `GET /v1/lessons/{stable_id}`, `POST /v1/quiz-attempts`, `POST /v1/hints`, `GET/POST /v1/reviews`, `POST /v1/project-submissions`; address every resource by `stable_id`, expose no internal numeric keys
    - _Requirements: 17.1, 17.2, 17.3, 17.4, 18.1, 18.3, 18.4, 18.5_
  - [x] 18.2 Implement cursor-based keyset pagination for list endpoints
    - Stable sort key tie-broken by `stable_id`; expired/malformed cursor returns a 400 problem
    - _Requirements: 17.5_
  - [x] 18.3 Write property test for cursor pagination and stable-id addressing
    - **Property 27: Resources are addressed only by stable id** — _Validates: Requirements 17.4_
    - **Property 28: Cursor pagination covers the full set exactly once** — _Validates: Requirements 17.5_
    - _Requirements: 17.4, 17.5_
  - [x] 18.4 Implement RFC 9457 problem-details responses via the existing errors machinery
    - Translated, actionable `detail`; diagnostics kept in logs; map domain errors (foreign option, cooldown, insufficient pool) to problem types
    - _Requirements: 18.6_
  - [x] 18.5 Implement `Idempotency-Key` handling for quiz-attempt submission
    - Same key + same body returns stored result and records one attempt; concurrent same-key processed at most once; reused key + conflicting body returns 409 problem details
    - Enforce the at-most-once guarantee across worker processes at the idempotency store via an atomic insert / unique constraint on the `Idempotency-Key` (or an equivalent row lock), NOT an in-process lock, so concurrency holds beyond a single process
    - _Requirements: 18.1, 18.2, 23.1, 23.2, 23.3_
  - [x] 18.6 Write property test for idempotency-key behavior
    - **Property 22: Idempotency-Key makes a submission take effect at most once** — _Validates: Requirements 18.1, 18.2, 23.1, 23.2, 23.3_
    - _Requirements: 18.1, 18.2, 23.1, 23.2, 23.3_
  - [x] 18.7 Implement cooldown rejection on quiz-attempt submission
    - Reject submissions during cooldown with a problem-details response whose translated `detail` states when attemptable again; record no attempt
    - _Requirements: 24.1, 24.2_
  - [x] 18.8 Write property test for cooldown rejection
    - **Property 23: Submissions during cooldown are rejected without recording** — _Validates: Requirements 24.1, 24.2_
    - _Requirements: 24.1, 24.2_
  - [x] 18.9 Implement project-submission validation and review routing
    - Reject submissions missing self-assessment / not covering every criterion / missing required evidence (problem details, no record); accept complete submissions, record `project_submission`, route through `ProjectReviewer`; expose a distinct pending status
    - _Requirements: 20.1, 20.2, 20.3, 20.4_
  - [x] 18.10 Implement review-endpoint decline for pools below the minimum
    - Decline to start a review when the eligible pool has fewer than the Config minimum (3); return problem details; create/advance no schedule entry
    - _Requirements: 28.1, 28.2_

- [x] 19. Learning API — lesson locale fallback, placement/final-assessment interfaces, GDPR
  - [x] 19.1 Implement English locale fallback for lesson requests
    - Serve English when no MDX translation exists for the requested locale rather than erroring
    - _Requirements: 17.3_
  - [x] 19.2 Write property test for locale fallback
    - **Property 26: Lesson requests fall back to English** — _Validates: Requirements 17.3_
    - _Requirements: 17.3_
  - [x] 19.3 Define placement and final-assessment interfaces and schemas (TODO grading only)
    - Schemas + interface signatures backed by `placement`/`final_assessment` tables; grading marked TODO, no grading implemented in this phase
    - _Requirements: 21.1, 21.2_
  - [x] 19.4 Implement recording `tested_out` on `module_progress` at placement
    - Record `tested_out` on `module_progress` where a learner tests out of a module at placement; this is the one implemented behavior among the otherwise interface-only placement work (depends on the domain mastery/progress work and the application layer)
    - _Requirements: 21.3_
  - [x] 19.5 Extend the GDPR application service to cover learning personal-data tables
    - Export and deletion cover quiz attempts, mastery (+events), reviews (schedule + attempts), hint reveals, project submissions, enrollment/progress/completions under export, deletion, and retention
    - _Requirements: 22.1, 22.2, 22.3_
  - [x] 19.6 Write property test for GDPR coverage
    - **Property 29: Export and deletion cover all learning personal data** — _Validates: Requirements 22.1, 22.2, 22.3_
    - _Requirements: 22.1, 22.2, 22.3_

- [x] 20. Infrastructure — PostgreSQL repositories for learning state
  - [x] 20.1 Implement PostgreSQL adapters for the learning repository ports
    - Map domain types to/from D3 catalog, progress, mastery/review, and assessment rows; keyset-pagination-friendly, `stable_id`-indexed lookups; back the Redis idempotency/session adapters already present
    - _Requirements: 17.2, 17.4, 17.5, 18.1_

- [x] 21. Checkpoint — Ensure API, application, and infrastructure tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 22. apps/web learner pages under /fr and /en
  - [x] 22.1 Implement dashboard, track, and module pages
    - Dashboard shows progress + concepts due/overdue; track and module pages show module lock states; call the API via the generated OpenAPI client; validate forms with Zod
    - _Requirements: 19.1, 19.2, 19.6_
  - [x] 22.2 Implement the lesson reader with the MDX component allowlist
    - Render MDX only through the allowlist (ADR A5)
    - _Requirements: 19.3, 19.6_
  - [x] 22.3 Implement the quiz player, review session, and project submission pages
    - Quiz player with feedback + progressive hints; review session page; project submission with rubric self-assessment + required evidence
    - _Requirements: 19.4, 19.5, 19.6_
  - [x] 22.4 Implement the lab "coming soon" replacement everywhere a lab is referenced
    - Replace lab content entirely with a placeholder exposing no lab details, preview, or link
    - _Requirements: 15.7, 19.7_
  - [x] 22.5 Write vitest logic/component tests for web behavior
    - Lock-state derivation, lab "coming soon" replacement (no details/preview/link), locale handling, Zod form validation, MDX renders only allowlisted components
    - _Requirements: 19.2, 19.3, 19.7_

- [x] 23. Integration tests across application + infrastructure
  - [x] 23.1 Write the end-to-end integration suite
    - enrollment → progress → lesson → quiz attempt → hint → review → project submission against real repositories + Redis-backed idempotency/session adapters; version-pinning and deferred-archive behaviors; Clock-driven review lifecycle (due, passed, failed with expected interval changes); GDPR export/deletion across every personal-data table
    - _Requirements: 25.1, 25.2, 25.3, 26.2, 26.3, 9.4, 9.5, 9.6, 22.1, 22.2, 22.3, 30.6, 30.8_

- [x] 24. Verification suite and accessibility (Requirement 30)
  - [x] 24.1 Assemble the verification suite aggregating the required checks
    - Rule→requirement→test mapping and ≥90% coverage; the five `content validate` failures; sync idempotence + progress preservation; both-language lesson completion and lock lifting; shuffle/matching/cooldown/anti-farming/hints; Clock-driven review lifecycle; duplicate submission counted once; export and deletion coverage; golden-set approval
    - _Requirements: 30.1, 30.2, 30.3, 30.4, 30.5, 30.6, 30.7, 30.8, 30.10_
  - [x] 24.2 Run Playwright + axe-core on every learner page under /fr and /en
    - Dashboard, track, module, lesson reader, quiz player, review session, and project submission pages; assert no accessibility violations
    - _Requirements: 30.9, 19.1, 19.2, 19.3, 19.4, 19.5_
  - [x] 24.3 Implement the p95 latency measurement against the Seed_Content
    - Measure enrollment, progress, lesson, quiz-attempt, hint, review, and project-submission endpoints; assert p95 < 300 ms
    - If the p95 assertion fails, feed the result back into repository/index tuning (e.g. the keyset pagination and `stable_id`-indexed lookups in task 20.1) rather than treating it as a dead-end assertion
    - _Requirements: 29.1_

- [x] 25. Final task — write the phase-2 report
  - Write `docs/reports/phase-2.md` recording, for each verification check in Requirement 30, the command invoked and its recorded pass-or-fail outcome (recording failures rather than omitting them), plus the recorded p95 latency measurement against the Seed_Content.
  - _Requirements: 29.2, 31.1, 31.2, 31.3_

## Notes

- Tasks marked with `*` are optional/non-blocking test sub-tasks and can be skipped for a faster MVP. For the domain engine, however, Requirement 11 requires the rules-based and property tests to be authored before/alongside implementation, so skipping the `*` test sub-tasks in Milestone A forfeits that guarantee.
- Each task references specific requirements for traceability; property tasks also name their Property number from design.md.
- Property-based tests use Hypothesis, ≥100 iterations, are driven by `Test_Clock`, read numbers from `domain/config.py`, and are tagged `Feature: learning-core, Property {n}: {property text}`.
- Checkpoints ensure incremental validation. The expert-review checkpoint (task 15) STOPS and waits for the user before golden-set marking.
- The workflow produces planning + implementation tasks only; begin execution from the tasks list once reviewed.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2", "9.1"] },
    { "id": 1, "tasks": ["1.3", "9.2", "10.1", "14.1"] },
    { "id": 2, "tasks": ["1.4", "2.1", "4.1", "4.3", "4.5", "4.7", "6.1", "6.3", "6.5", "10.2", "11.1", "14.2"] },
    { "id": 3, "tasks": ["2.2", "2.3", "4.2", "4.4", "4.6", "4.8", "6.2", "6.4", "6.6", "11.2", "11.3", "12.1", "16.1"] },
    { "id": 4, "tasks": ["2.4", "7.1", "11.4", "16.2", "17.1"] },
    { "id": 5, "tasks": ["7.2", "17.2", "18.1", "18.4", "18.9", "18.10", "19.1", "19.3", "19.4", "19.5", "20.1"] },
    { "id": 6, "tasks": ["17.3", "18.2", "18.5", "18.7", "19.2", "19.6"] },
    { "id": 7, "tasks": ["18.3", "18.6", "18.8", "22.1", "22.2", "22.3", "22.4"] },
    { "id": 8, "tasks": ["22.5", "23.1"] },
    { "id": 9, "tasks": ["24.1", "24.2", "24.3"] }
  ]
}
```
