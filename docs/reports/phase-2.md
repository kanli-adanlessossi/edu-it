# Phase-2 Report — EDU-IT Learning Core

> Acceptance artifact for the `learning-core` spec (Requirements 29.2, 31.1–31.3). For each check in
> Requirement 30 it records the command invoked and the real pass-or-fail outcome. Failing or
> not-yet-satisfied outcomes are recorded, not omitted. The p95 latency measurement (Requirement 29)
> is in §4.

## 1. Summary

| # | Req | Check | Outcome |
|---|-----|-------|---------|
| 1 | 30.1 | Rule→requirement→test mapping; domain coverage ≥ 90% | PASS |
| 2 | 30.2 | `content validate` fails on the five invalid-content cases | PASS |
| 3 | 30.3 | `content sync` idempotent; content changes never reset progress | PASS |
| 4 | 30.4 | Both lessons complete in fr/en; locks lift as specified | PASS |
| 5 | 30.5 | Shuffling, answer matching, cooldown, anti-farming, hint multipliers | PASS |
| 6 | 30.6 | Clock-driven review lifecycle (due, pass, fail) | PASS |
| 7 | 30.7 | Duplicate quiz submission counted once | PASS |
| 8 | 30.8 | Export and deletion include all learning data | PASS |
| 9 | 30.9 | Playwright + axe-core on all learner pages, /fr and /en | PASS (14 passed) |
| 10 | 30.10 | Seed_Content approved by the user and marked Golden_Set | **FAIL** (approval flow passes; the committed seed has no recorded reviewer) |
| – | 29.1 | p95 < 300 ms on the learning endpoints | PASS (max p95 31.07 ms) |

Environment: Windows, Python 3.14.0, node v22.14.0. Repository revision: `84def85` (working tree
contained uncommitted changes when recorded). Recorded 2026-10-29.

## 2. Open failure

**Check 30.10 (recorded approval) FAILED.** All 22 committed seed items in `content/linux-fundamentals`
have no `reviewed_by` in their provenance, so the Seed_Content has not been approved by the user and
is not marked the Golden_Set. The approval mechanics are verified (30.10-flow passes) but the recorded
approval itself is pending. Resolution: the user reviews and approves the Seed_Content, the reviewer is
recorded, and the check is re-run. This is not treated as passed.

## 3. Verification Checks — Commands and Outcomes

All Python checks were run through the aggregate runner, `python tools/run_verification.py`
(invoked as `.venv\Scripts\python.exe tools/run_verification.py`), which prints each command and its
outcome. Each `python -m pytest` command below is prefixed with `-q --no-header -p no:cacheprovider`.

### 3.1 Requirement 30.1 — rule mapping and coverage: PASS

- Mapping: `python -m pytest tools/tests/test_learning_verification_suite.py::test_every_domain_rule_maps_to_a_requirement_and_a_test tools/tests/test_learning_verification_suite.py::test_every_mapping_entry_matches_a_real_rule tools/tests/test_learning_verification_suite.py::test_domain_coverage_gate_is_at_least_90_percent_and_wired_into_runners` — 3 passed.
- Coverage gate: `python -m pytest --cov=edu_it_domain --cov-fail-under=90 --cov-report=term` — 945 passed, 102 warnings in 161.71s; the ≥ 90% gate was met (exit status 0).

### 3.2 Requirement 30.2 — `content validate` failures: PASS

`python -m pytest tools/tests/test_learning_verification_suite.py packages/content/tests/test_validate_properties.py` — 24 passed.
Covers missing translation, broken reference, prerequisite cycle, unknown MDX component, AI item without reviewer.

### 3.3 Requirement 30.3 — sync idempotence and progress preservation: PASS

`python -m pytest packages/content/tests/test_sync.py packages/content/tests/test_sync_properties.py tools/tests/test_learning_verification_suite.py::test_second_sync_of_seed_makes_no_changes tools/tests/test_learning_verification_suite.py::test_content_edit_updates_item_but_never_resets_completions` — 15 passed.

### 3.4 Requirement 30.4 — lesson completion in both languages and lock lifting: PASS

`python -m pytest tools/tests/test_learning_verification_suite.py::test_both_seed_lessons_complete_in_each_language_and_locks_lift_by_mastery packages/infrastructure/tests/test_unlocking_completion_properties.py apps/api/tests/test_errors_locale_fallback_property.py packages/application/tests/test_get_lesson_fallback.py` — 22 passed.

### 3.5 Requirement 30.5 — shuffling, matching, cooldown, anti-farming, hints: PASS

`python -m pytest packages/infrastructure/tests/test_scoring_properties.py packages/infrastructure/tests/test_cooldown_properties.py packages/infrastructure/tests/test_antifarming_properties.py packages/infrastructure/tests/test_hints_properties.py apps/api/tests/test_quiz_attempt_cooldown.py apps/api/tests/test_quiz_cooldown_property.py` — 60 passed.

### 3.6 Requirement 30.6 — Clock-driven review lifecycle: PASS

`python -m pytest packages/infrastructure/tests/test_learning_integration.py::test_enrollment_to_project_submission_journey packages/infrastructure/tests/test_review_properties.py packages/infrastructure/tests/test_decay_properties.py packages/infrastructure/tests/test_worked_example_e2e.py` — 47 passed.

### 3.7 Requirement 30.7 — duplicate submission counted once: PASS

`python -m pytest packages/infrastructure/tests/test_property_idempotency_key.py packages/infrastructure/tests/test_adapter_idempotency_store.py packages/infrastructure/tests/test_learning_integration.py::test_shared_redis_idempotency_across_two_worker_stores` — 7 passed.

### 3.8 Requirement 30.8 — export and deletion coverage: PASS

`python -m pytest packages/infrastructure/tests/test_gdpr_learning_data_coverage.py packages/infrastructure/tests/test_gdpr_learning_data_coverage_properties.py packages/infrastructure/tests/test_learning_integration.py::test_export_and_deletion_cover_every_personal_data_table` — 5 passed.

### 3.9 Requirement 30.9 — Playwright + axe-core: PASS

The aggregate runner has no command wired for this check (it reports `not-run`), so it was run
directly: `npx playwright test` in `apps/web` — 14 passed (31.8s). Dashboard, track, module, lesson
reader, quiz player, review session, and project submission pages, each under `/fr` and `/en`, had
no axe-core violations. The pages are served by `next dev` against the deterministic mock API in
`apps/web/e2e/mock-api.mjs` (see `playwright.config.ts`), not the real API.

### 3.10 Requirement 30.10 — Seed_Content approval and Golden_Set: FAIL

- Flow: `python -m pytest packages/content/tests/test_sync_approval.py tools/tests/test_learning_verification_suite.py::test_approval_records_reviewer_and_marks_seed_as_golden_set tools/tests/test_learning_verification_suite.py::test_without_approval_nothing_is_synced_attributed_or_marked` — 18 passed (PASS).
- Recorded approval: runner inspection of `content/linux-fundamentals` — **FAILED**: 22 of 22 seed items have no recorded reviewer (approval pending). See §2.

## 4. p95 Latency (Requirement 29)

Command: `LATENCY_REPORT=<file> python -m pytest -q -s apps/api/tests/test_learning_latency.py` — 1 passed.

Method: real routers and use cases over the SQL repositories (schema on in-memory SQLite, not
PostgreSQL), Redis idempotency adapter on fakeredis, catalog loaded from the authored Seed_Content under
`content/`. Each sample is a full in-process ASGI round trip by a distinct learner; nearest-rank p95.
Budget: p95 < 300 ms.

| endpoint | samples | p95 (ms) | max (ms) |
|---|---|---|---|
| enrollment | 50 | 21.43 | 24.84 |
| progress | 50 | 15.04 | 24.88 |
| lesson | 50 | 14.83 | 18.68 |
| quiz-attempt | 50 | 31.07 | 32.25 |
| hint | 50 | 6.92 | 15.98 |
| review (GET + POST) | 100 | 5.95 | 29.16 |
| project-submission | 50 | 4.34 | 4.51 |

Result: every endpoint is under the 300 ms budget. Caveat: this is an in-process measurement over
SQLite, so it excludes network and PostgreSQL latency; it was not run against a Compose stack.
