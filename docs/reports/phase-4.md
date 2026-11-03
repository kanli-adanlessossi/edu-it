# Phase 4 Report: AI content generation

Status: PASSING

Generated 2026-11-03T10:50:49Z by `tools/phase_report.py` after the Live_Run. The report is passing only when every required acceptance check ran and passed (Requirement 25.6).

## 1. Live_Run (Requirement 25.2)

- Live_Run cost (USD): 0.10
- Cost_Cap (USD): 20
- Models used: eu.anthropic.claude-sonnet-4-6
- Generation_Branch: `generate/networking-basics/outline/d26eda31-9feb-4f6b-acba-88519a4bb23d`, `generate/networking-basics/lessons/ba190837-7a74-474b-ba69-47867a0c740b`

## 2. Eval_Report (Requirement 25.3)

- Suite: `golden`
- Passed: 36 of 36 (pass rate 1.0000)

| Prompt | Version |
|--------|---------|
| `glossary` | `1` |
| `quiz` | `1` |
| `verify_answer` | `1` |

| Case | Kind | Outcome | Detail |
|------|------|---------|--------|
| `answer-key.question.cp.1-chmod-symbolic.en` | answer-key | PASSED | selected [0] |
| `answer-key.question.cp.1-chmod-symbolic.fr` | answer-key | PASSED | selected [0] |
| `answer-key.question.cp.2-chmod-octal.en` | answer-key | PASSED | selected [0] |
| `answer-key.question.cp.2-chmod-octal.fr` | answer-key | PASSED | selected [0] |
| `answer-key.question.cp.3-chown-chgrp.en` | answer-key | PASSED | selected [0] |
| `answer-key.question.cp.3-chown-chgrp.fr` | answer-key | PASSED | selected [0] |
| `answer-key.question.cp.4-setgid-umask.en` | answer-key | PASSED | selected [0] |
| `answer-key.question.cp.4-setgid-umask.fr` | answer-key | PASSED | selected [0] |
| `answer-key.question.cp.5-command-output.en` | answer-key | PASSED | selected [0] |
| `answer-key.question.cp.5-command-output.fr` | answer-key | PASSED | selected [0] |
| `answer-key.question.rp.1-ls-l-fields.en` | answer-key | PASSED | selected [0] |
| `answer-key.question.rp.1-ls-l-fields.fr` | answer-key | PASSED | selected [0] |
| `answer-key.question.rp.2-rwx-meaning.en` | answer-key | PASSED | selected [0] |
| `answer-key.question.rp.2-rwx-meaning.fr` | answer-key | PASSED | selected [0] |
| `answer-key.question.rp.3-octal-from-symbolic.en` | answer-key | PASSED | selected [0] |
| `answer-key.question.rp.3-octal-from-symbolic.fr` | answer-key | PASSED | selected [0] |
| `answer-key.question.rp.4-group-read-true-false.en` | answer-key | PASSED | selected [0] |
| `answer-key.question.rp.4-group-read-true-false.fr` | answer-key | PASSED | selected [0] |
| `answer-key.question.rp.5-command-output.en` | answer-key | PASSED | selected [0] |
| `answer-key.question.rp.5-command-output.fr` | answer-key | PASSED | selected [0] |
| `glossary-terms.concept.changing-permissions` | glossary-terms | PASSED | 2 term pair(s) |
| `glossary-terms.concept.processes` | glossary-terms | PASSED | 2 term pair(s) |
| `glossary-terms.concept.reading-permissions` | glossary-terms | PASSED | 3 term pair(s) |
| `glossary-terms.lab.fix-the-shared-folder` | glossary-terms | PASSED | 2 term pair(s) |
| `glossary-terms.lab.world-writable-entries` | glossary-terms | PASSED | 2 term pair(s) |
| `glossary-terms.question.cp.2-chmod-octal` | glossary-terms | PASSED | 2 term pair(s) |
| `glossary-terms.question.cp.3-chown-chgrp` | glossary-terms | PASSED | 2 term pair(s) |
| `glossary-terms.question.cp.4-setgid-umask` | glossary-terms | PASSED | 3 term pair(s) |
| `glossary-terms.question.cp.5-command-output` | glossary-terms | PASSED | 1 term pair(s) |
| `glossary-terms.question.rp.1-ls-l-fields` | glossary-terms | PASSED | 1 term pair(s) |
| `glossary-terms.question.rp.2-rwx-meaning` | glossary-terms | PASSED | 3 term pair(s) |
| `glossary-terms.question.rp.3-octal-from-symbolic` | glossary-terms | PASSED | 1 term pair(s) |
| `glossary-terms.question.rp.4-group-read-true-false` | glossary-terms | PASSED | 2 term pair(s) |
| `glossary-terms.question.rp.5-command-output` | glossary-terms | PASSED | 4 term pair(s) |
| `schema-conformance.quiz.changing-permissions` | schema-conformance | PASSED | output validates |
| `schema-conformance.quiz.reading-permissions` | schema-conformance | PASSED | output validates |

## 3. Acceptance checks: commands and outcomes (Requirement 25.4)

| # | Check | Requirement | Description | Command | Outcome |
|---|-------|-------------|-------------|---------|---------|
| 1 | `ai-lint` | 23.1 | Ruff lint over the Python components | `python -m ruff check .` | PASSED: All checks passed! |
| 2 | `ai-format` | 23.1 | Ruff format check over the Python components | `python -m ruff format --check .` | PASSED: 675 files already formatted |
| 3 | `ai-domain-boundary` | 23.3 | packages/domain imports no web or API framework | `python tools/check_domain_boundary.py` | PASSED: domain-boundary: OK - no web/API framework imports in C:\PUBLIC\PROJECTS\EDU-IT\dev\edu-it\packages\domain |
| 4 | `ai-typecheck` | 23.1 | mypy over the Python components | `python -m mypy` | PASSED: Success: no issues found in 626 source files |
| 5 | `ai-web-lint` | 23.1 | Web lint | `(cd apps/web && npm run lint)` | PASSED: > eslint . |
| 6 | `ai-web-typecheck` | 23.1 | Web type check | `(cd apps/web && npm run typecheck)` | PASSED: > tsc --noEmit |
| 7 | `ai-unit` | 23.1 | Unit tests (Docker-marked tests excluded; no model network access) | `python -m pytest -q --no-header -p no:cacheprovider -m not docker -k not propert` | PASSED: 2534 passed, 131 skipped, 671 deselected, 227 warnings in 188.80s (0:03:08) |
| 8 | `ai-property` | 23.1 | Property-based tests (Docker-marked tests excluded) | `python -m pytest -q --no-header -p no:cacheprovider -m not docker -k propert` | PASSED: 456 passed, 2 skipped, 2878 deselected in 570.11s (0:09:30) |
| 9 | `ai-web-test` | 23.1 | Web unit tests (vitest) | `(cd apps/web && npm test)` | PASSED: Tests  345 passed (345) |
| 10 | `ai-content-validate` | 6, 23.1 | content validate, --allow-pending (the CI mode) | `python -m edu_it_content.cli validate --allow-pending --content-root content` | PASSED: content valid: 80 item(s) passed all checks in content |
| 11 | `ai-eval-replay` | 11 | edu-it-eval on the golden suite with the Replay_Provider (EDU_IT_ENV=test) | `python -c import sys; from edu_it_worker.ai.cli import eval_entry; sys.exit(eval_entry(['--suite', 'golden']))` | PASSED: report: C:\PUBLIC\PROJECTS\EDU-IT\dev\edu-it\evals\reports\golden.replay.json |
| 12 | `ai-prompt-immutability` | 4.2, 4.3 | No committed Prompt changed since origin/main | `python tools/check_prompt_immutability.py --base origin/main` | PASSED: prompt-immutability: OK - no existing prompt file changed since origin/main |
| 13 | `ai-traceability` | 23.2, 23.5 | Every requirement of milestones A, B, C and 23 to 25 is claimed by a test | `python tools/check_traceability.py --milestone all` | PASSED: Traceability OK for milestone all. |
| 14 | `ai-traceability-suite` | 23.2 | Every requirement maps to an existing ai-content test | `python -m pytest -q --no-header -p no:cacheprovider tools/tests/test_ai_content_verification_suite.py` | PASSED: 107 passed in 1.45s |

## 4. Not verified (Requirement 25.5)

- Live_Run cost is the sum of the two Generation_Reports (outline USD 0.05 + lessons USD 0.05); each report states its cost at USD 0.01 granularity, and no vendor invoice was compared.
- Only the outline and one lesson (lesson.automatic-address-assignment-with-dhcp) were generated; 12 other lessons were not attempted (--limit-units 1). Per-locale (FR and EN) coverage of the generated content is not stated in the Generation_Reports and was not independently checked here.
- Both Generation_Branches remain unmerged and no generated item is reviewed; human review of the generated Networking content was not performed.
- Eval_Report ran on the Replay_Provider only; it does not measure live model quality.
- Docker-marked tests and Playwright/axe-core suites are outside the ai-content suite and were not run for this report.
- The ai-content suite ran on branch feature/ai_content with prompt immutability checked against origin/main (3a6c135); it does not cover CI results on a remote.
