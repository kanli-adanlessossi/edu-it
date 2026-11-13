---
inclusion: fileMatch
fileMatchPattern: "tests/**|**/tests/**|**/*{test,spec}.*|**/conftest.py|**/playwright.config.*|**/vitest.config.*"
---
# Testing Strategy and Performance Targets

This steering file is the single source of truth for EDU-IT testing rules and measured
performance targets. The rules are transcribed from the learning-core, hands-on-labs,
kubernetes-deployment and api-route-wiring specs; numbers and normative wording are preserved.
The detail of how to run the suites is in `docs/TESTING.md`.

## Rule-First Domain Tests

- The Domain_Engine tests are written from the rules in #[[file:.kiro/steering/domain-rules.md]]
  before the implementation, with at least one test per rule.
- Domain statement coverage is at least 90%.
- Time-dependent behavior is driven only by the fake Clock
  (#[[file:.kiro/steering/structure.md]]).
- Every domain rule maps to at least one EARS requirement and at least one test.

## Test Tiers

- Unit and in-process API tests run against fakes. One in-process test drives sign-up,
  verification, sign-in, enrollment, quiz attempt, project submission and sign-out over HTTP
  against the served app built with fakes.
- Web end-to-end (`npm run test:e2e`) runs against the deterministic mock API only.
- Real-stack tests use no mock API: they run the real web, API and worker against the Docker
  Sandbox provider, and cover the full learner journey and both seed labs in `/fr` and `/en`.
- Chart render tests (`tests/chart`) need no cluster. Kind tests (`tests/k8s`) cover smoke,
  isolation, failure, upgrade and rollback behavior. The lab load test is
  `tools/perf/lab_load_k8s.py`.
- Playwright with axe-core runs on the dashboard, track, module, lesson reader, quiz player,
  review session and project submission pages, and on the Lab_View states.
- GitHub-OAuth tests use a mock provider and make no external call.

## Performance Targets

- Learning endpoints (enrollment, progress, lesson, quiz-attempt, hint, review, project
  submission): p95 latency under 300 ms on the seed content, locally.
- Sandbox ready within 20 s at p95 on the local setup (at least 30 samples); terminal keystroke
  round trip under 150 ms at p95 (at least 200 keystrokes); shell Check results within 15 s.
- Kind load test: Sandbox_Ready_Time p95 of at most 30 s over at least 20 samples; at least 10
  concurrent Lab_Sessions held for at least 60 s, each ready within 120 s; every grading result
  within 15 s.
- Percentiles use the nearest-rank method. The first 3 warm-up samples are discarded and the
  machine is otherwise idle. One full re-measurement is allowed; every attempt is logged and the
  final one counts.
- A missed target fails the run with a non-zero status naming each missed target, its measured
  value and its threshold. The load test deletes every Lab_Pod it created, even on failure or
  abort, leaving none within 60 s.
