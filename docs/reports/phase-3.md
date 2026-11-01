# Phase 3 Report: Hands-on labs

Generated 2026-11-01T11:09:51Z by the two-phase gate procedure (`tools/run_verification.py` docstring): phase 1, Real_Stack down, `tools/run_verification.py --suite labs` (14 checks and the Requirement 33 groups, 2026-11-01 10:45-11:03Z); phase 2, `tools/e2e_stack.py up`, `--suite labs --only labs-playwright-real labs-performance` (11:03-11:09Z, run from 11:04Z). `phase_report.write_phase_report` merged the two runs' results; its guards were applied to the merged set. The report is written only when every acceptance check ran and passed and the final performance attempt met every target (Requirements 35.1, 35.3).

## 1. Acceptance checks: commands and outcomes (Requirement 35.1)

| # | Check | Requirement | Description | Command | Outcome |
|---|-------|-------------|-------------|---------|---------|
| 1 | `labs-unit` | 5-30 | Labs unit tests (Docker-marked tests excluded) | `python -m pytest -q --no-header -p no:cacheprovider -m not docker -k not propert` | PASSED: 1483 passed, 55 skipped, 555 deselected, 17 warnings in 72.69s (0:01:12) |
| 2 | `labs-property` | 1-30 | Labs property-based tests (Docker-marked tests excluded) | `python -m pytest -q --no-header -p no:cacheprovider -m not docker -k propert` | PASSED: 340 passed, 2 skipped, 1751 deselected in 362.38s (0:06:02) |
| 3 | `labs-docker` | 3, 10, 14, 28, 29 | Docker-marked tests against the Docker_Provider and the lab images | `python -m pytest -q --no-header -p no:cacheprovider -m docker` | PASSED:   warnings.warn(pytest.PytestUnraisableExceptionWarning(msg)) |
| 4 | `labs-web` | 15, 16, 18 | Web unit and property tests (vitest) | `(cd apps/web && npm test)` | PASSED:              at least ~530ms faster with isolate: false — reuses workers across files instead of one per file |
| 5 | `labs-playwright` | 15, 32 | Playwright seed-lab page tests against the deterministic mock API | `(cd apps/web && npx playwright test e2e/seed-labs.spec.ts)` | PASSED: [WebServer]   Learn more: https://nextjs.org/docs/messages/middleware-to-proxy |
| 6 | `labs-playwright-real` | 32 | Playwright learner journey against the real stack (needs the stack and E2E_MAIL_LOG) | `(cd apps/web && npm run test:e2e:real)` | PASSED:   5 passed (29.9s) |
| 7 | `labs-axe` | 16 | axe-core accessibility on the lab view and every learner page, /fr and /en | `(cd apps/web && npx playwright test e2e/lab-view-a11y.spec.ts e2e/learner-a11y.spec.ts)` | PASSED: [WebServer]   Learn more: https://nextjs.org/docs/messages/middleware-to-proxy |
| 8 | `labs-content-strict` | 20, 23.8 | content validate, strict (no tolerated failure) | `python -m edu_it_content.cli validate --content-root content` | PASSED: content valid: 80 item(s) passed all checks in content |
| 9 | `labs-content-pending` | 20.12, 20.13 | content validate, --allow-pending (the CI mode) | `python -m edu_it_content.cli validate --allow-pending --content-root content` | PASSED: content valid: 80 item(s) passed all checks in content |
| 10 | `labs-review-approval` | 23 | Approval flow: reviewer recorded, Golden_Set only after confirmation, rejection path | `python -m pytest -q --no-header -p no:cacheprovider packages/content/tests/test_lab_approval.py` | PASSED: 8 passed in 1.18s |
| 11 | `labs-adr-pins` | 4.13, 4.14 | ADR 0001 pins match the manifests for the dependencies this spec introduces | `python -m pytest -q --no-header -p no:cacheprovider tools/tests/test_labs_adr_pins.py` | PASSED: 5 passed in 0.23s |
| 12 | `labs-traceability` | 33 | Every labs rule maps to a requirement and an existing test | `python -m pytest -q --no-header -p no:cacheprovider tools/tests/test_labs_verification_suite.py` | PASSED: 13 passed in 0.21s |
| 13 | `labs-performance` | 31 | Start-to-ready, keystroke and shell-Check performance (30/200 samples, 3 warm-ups, at most one re-measurement) | `python tools/perf/lab_perf.py --start-cmd C:/PUBLIC/PROJECTS/EDU-IT/dev/edu-it/.venv/Scripts/python.exe tools/perf/start_driver.py --keystroke-cmd C:/PUBLIC/PROJECTS/EDU-IT/dev/edu-it/.venv/Scripts/python.exe tools/perf/keystroke_driver.py --check-cmd C:/PUBLIC/PROJECTS/EDU-IT/dev/edu-it/.venv/Scripts/python.exe tools/perf/check_driver.py --out tools/perf/lab-perf-raw.json` | PASSED: RESULT: PASS -> tools/perf/lab-perf-raw.json |
| 14 | `labs-image-build` | 4 | Build both Lab_Images with docker buildx bake | `(cd lab-images && docker buildx bake --load)` | PASSED:   linux-base: docker-desktop://dashboard/build/desktop-linux/desktop-linux/nzxoufu1awcav6ikhs50q63k7 |
| 15 | `labs-image-scan-lab-linux-base` | 4 | Trivy scan of edu-it/lab-linux-base:dev: fail on any finding, every severity | `trivy image --scanners vuln --severity UNKNOWN,LOW,MEDIUM,HIGH,CRITICAL --exit-code 1 edu-it/lab-linux-base:dev` | PASSED: 2026-11-01T10:58:14Z	INFO	Number of language-specific files	num=0 |
| 16 | `labs-image-scan-lab-python` | 4 | Trivy scan of edu-it/lab-python:dev: fail on any finding, every severity | `trivy image --scanners vuln --severity UNKNOWN,LOW,MEDIUM,HIGH,CRITICAL --exit-code 1 edu-it/lab-python:dev` | PASSED: 2026-11-01T10:58:31Z	INFO	Number of language-specific files	num=0 |

## 2. Performance measurements (Requirement 31)

Percentile method: nearest-rank (ceil(p/100 * n)-th smallest). The first 3 samples of each target are discarded as warm-ups. Only the final attempt decides the pass (Requirements 31.8, 35.4).

### 2.1 Every attempt (Requirement 31.8)

| Attempt | Target | Counted | Raw | p95 (s) | Max (s) | Limit (s) | Met |
|---------|--------|---------|-----|---------|---------|-----------|-----|
| 1 | start_to_ready | 30 | 33 | 0.570061 | 0.570456 | 20.0 | yes |
| 1 | keystroke_round_trip | 200 | 203 | 0.007769 | 0.010030 | 0.15 | yes |
| 1 | shell_check | 1 | 4 | 0.823854 | 0.823854 | 15.0 | yes |

### 2.2 Final attempt (attempt 1) measurements

#### Start to ready (Req 31.1)

- Sample count: 30 counted (33 raw, 3 warm-ups discarded; minimum 30)
- Method: nearest-rank percentile (p95)
- Measurement: wall-clock of command: C:/PUBLIC/PROJECTS/EDU-IT/dev/edu-it/.venv/Scripts/python.exe tools/perf/start_driver.py
- Result: nearest-rank p95 0.570061 s under 20.0 s: MET
- Values (seconds, counted samples in collection order):

```
0.538431, 0.495812, 0.530374, 0.537676, 0.529112, 0.476797, 0.567212, 0.554503, 0.500325, 0.555954, 0.556801, 0.494754, 0.508628, 0.524596, 0.480328, 0.522526, 0.540630, 0.514166, 0.569631, 0.570061, 0.541104, 0.531470, 0.554454, 0.517603, 0.542028, 0.514204, 0.564530, 0.512317, 0.508458, 0.570456
```

#### Keystroke round trip (Req 31.2)

- Sample count: 200 counted (203 raw, 3 warm-ups discarded; minimum 200)
- Method: nearest-rank percentile (p95)
- Measurement: wall-clock of command: C:/PUBLIC/PROJECTS/EDU-IT/dev/edu-it/.venv/Scripts/python.exe tools/perf/keystroke_driver.py
- Result: nearest-rank p95 0.007769 s under 0.15 s: MET
- Values (seconds, counted samples in collection order):

```
0.001914, 0.005689, 0.007219, 0.002008, 0.006802, 0.003753, 0.005872, 0.006083, 0.007646, 0.005285, 0.004415, 0.005183, 0.005722, 0.005931, 0.005857, 0.005329, 0.006586, 0.004809, 0.005540, 0.005407, 0.007769, 0.005484, 0.003990, 0.005205, 0.004216, 0.005624, 0.004503, 0.003836, 0.006367, 0.007904, 0.004079, 0.007303, 0.006297, 0.007744, 0.003832, 0.006690, 0.005294, 0.006478, 0.004451, 0.007281, 0.005604, 0.008412, 0.005718, 0.005265, 0.007489, 0.005801, 0.002798, 0.003902, 0.005731, 0.006078, 0.005111, 0.004856, 0.002517, 0.005399, 0.004912, 0.008047, 0.005284, 0.002171, 0.005886, 0.007185, 0.007217, 0.005122, 0.002337, 0.004842, 0.005743, 0.004967, 0.008519, 0.005486, 0.007982, 0.005042, 0.005837, 0.006203, 0.003279, 0.002830, 0.005271, 0.007556, 0.005421, 0.005808, 0.004032, 0.004302, 0.007077, 0.007504, 0.004830, 0.007545, 0.007426, 0.005215, 0.004496, 0.005442, 0.007400, 0.003337, 0.003799, 0.004267, 0.004472, 0.010030, 0.007585, 0.002243, 0.005317, 0.007607, 0.004898, 0.007586, 0.006906, 0.006931, 0.005015, 0.005802, 0.007634, 0.005142, 0.006663, 0.005858, 0.005755, 0.002412, 0.003710, 0.006691, 0.006039, 0.007735, 0.007527, 0.005529, 0.006048, 0.002284, 0.002050, 0.002204, 0.001863, 0.001921, 0.001998, 0.001940, 0.003259, 0.002205, 0.002516, 0.001892, 0.002209, 0.001860, 0.003720, 0.002743, 0.003924, 0.001938, 0.002175, 0.001915, 0.002094, 0.002343, 0.004583, 0.004135, 0.005262, 0.003151, 0.002641, 0.002668, 0.002606, 0.002186, 0.002523, 0.002576, 0.005911, 0.006081, 0.003026, 0.002908, 0.007671, 0.007647, 0.005709, 0.007496, 0.002397, 0.006060, 0.006125, 0.006295, 0.005114, 0.005499, 0.007596, 0.005632, 0.002118, 0.005777, 0.008008, 0.004366, 0.005451, 0.005991, 0.004883, 0.008557, 0.005721, 0.008657, 0.006499, 0.007746, 0.007322, 0.007674, 0.007874, 0.006206, 0.007233, 0.005162, 0.007300, 0.002921, 0.002828, 0.005078, 0.005302, 0.003968, 0.003105, 0.005445, 0.005759, 0.006684, 0.004679, 0.002527, 0.002030, 0.005729, 0.005018, 0.005731, 0.006320, 0.003935
```

#### Shell Check run (Req 31.3)

- Sample count: 1 counted (4 raw, 3 warm-ups discarded; minimum 1)
- Method: nearest-rank percentile (p95)
- Measurement: wall-clock of command: C:/PUBLIC/PROJECTS/EDU-IT/dev/edu-it/.venv/Scripts/python.exe tools/perf/check_driver.py
- Result: every counted run under 15.0 s (max 0.823854 s): MET
- Values (seconds, counted samples in collection order):

```
0.823854
```

### 2.3 Machine setup

```json
{
  "cpu": "Intel64 Family 6 Model 186 Stepping 2, GenuineIntel",
  "cpu_count": 20,
  "ram_bytes": 34086969344,
  "os": "Windows 11 10.0.26300",
  "docker_version": "29.9.2",
  "python": "3.14.0",
  "pre_run_snapshot": {
    "timestamp": "2026-11-01T11:04:38.905004+00:00",
    "cpu_percent": 11.0,
    "memory_available_bytes": 14945132544,
    "memory_total_bytes": 34086969344
  },
  "idle_machine_required": true
}
```

## 3. Traceability of labs rules to automated tests (Requirement 33)

Fake_Provider tests verify call logic only and are not security evidence; tamper resistance and the security and grading-integrity rules are evidenced by the Docker_Provider tests (Requirements 10.12, 29.11).

### 3.1 Rule mapping

| Rule | Requirements | Tests (file :: needle) |
|------|--------------|------------------------|
| Learners rebuild skills | 19.1, 19.3, 19.6 | `packages/infrastructure/tests/test_lab_check_gate_property.py` :: `hands-on-labs, Property 13:`<br>`packages/domain/tests/test_property_lab_mastery.py` :: `hands-on-labs, Property 12:` |
| Sandbox: Behind `SandboxProvider` | 1.1, 1.2, 1.4 | `packages/infrastructure/tests/test_sandbox_provider_contract.py` :: `def test_`<br>`packages/infrastructure/tests/test_sandbox_provider_lifecycle_property.py` :: `hands-on-labs, Property 2:` |
| The sandbox sits behind | 1.1, 1.2, 1.3 | `packages/domain/tests/test_lab_ports.py` :: `def test_`<br>`packages/infrastructure/tests/test_fake_lab_adapters.py` :: `def test_`<br>`packages/infrastructure/tests/test_sandbox_provider_contract.py` :: `def test_` |
| `SandboxProvider`: implementations | 1.2, 1.5, 2.1, 2.2 | `packages/infrastructure/tests/test_sandbox_provider_selection.py` :: `def test_`<br>`packages/domain/tests/test_lab_ports.py` :: `def test_` |
| `apps/web`: renders the xterm.js terminal | 13.3, 13.4, 15.2, 15.10 | `apps/web/src/components/terminal-pane.test.tsx` :: `describe(`<br>`apps/web/src/lab-state.property.test.ts` :: `hands-on-labs, Property 18:` |
| `apps/api`: hosts the authenticated terminal WebSocket | 12.1, 12.2, 12.10, 12.11 | `packages/application/tests/test_terminal_property_auth.py` :: `hands-on-labs, Property 14:`<br>`apps/api/tests/test_labs_terminal_route.py` :: `def test_` |
| `apps/worker`: lab provisioning | 6.1, 6.5, 8.1, 9.1 | `apps/worker/tests/test_lab_worker_wiring.py` :: `def test_`<br>`packages/application/tests/test_reap_sessions_property.py` :: `hands-on-labs, Property 5:`<br>`packages/application/tests/test_provision_sandbox_properties.py` :: `hands-on-labs, Property 8:` |
| The api can attach terminal TTY | 12.3, 12.4 | `tests/labs/test_docker_terminal.py` :: `def test_`<br>`packages/application/tests/test_terminal_gateway.py` :: `def test_` |
| labs namespace | 1.5, 3.4, 3.5 | `packages/infrastructure/tests/test_docker_spec_properties.py` :: `hands-on-labs, Property 1:`<br>`tests/labs/test_docker_isolation.py` :: `def test_` |
| Dedicated lab node pool | 1.1, 1.5, 2.1, 2.2 | `packages/infrastructure/tests/test_sandbox_provider_selection.py` :: `def test_`<br>`packages/infrastructure/tests/test_docker_provider.py` :: `def test_` |
| Default-deny NetworkPolicy | 3.2, 3.7 | `tests/labs/test_docker_isolation.py` :: `def test_`<br>`packages/infrastructure/tests/test_docker_spec_properties.py` :: `hands-on-labs, Property 1:` |
| Graceful shutdown drains terminal WebSockets | 12.8, 26.5 | `packages/application/tests/test_terminal_property_drain.py` :: `hands-on-labs, Property 31:`<br>`apps/api/tests/test_terminal_failure_scenarios.py` :: `def test_` |
| The worker provisions, executes and destroys | 6.2, 6.5, 8.2, 10.1 | `packages/application/tests/test_provision_sandbox.py` :: `def test_`<br>`packages/application/tests/test_reap_sessions.py` :: `def test_`<br>`packages/application/tests/test_grade_run.py` :: `def test_` |
| Grading is bounded | 7.4, 11, 28 | `tests/labs/test_grading_acceptance_property.py` :: `hands-on-labs, Property 6:`<br>`packages/application/tests/test_grade_run_scheduling_properties.py` :: `hands-on-labs, Property 11:`<br>`packages/application/tests/test_grade_run_queue_delay.py` :: `def test_`<br>`tests/labs/test_docker_check_abuse.py` :: `def test_` |
| IF sandbox provisioning fails | 25.1, 25.2, 25.3, 25.5 | `tests/labs/test_provisioning_failure_scenarios.py` :: `def test_`<br>`packages/application/tests/test_provision_sandbox_properties.py` :: `hands-on-labs, Property 8:` |
| IF a terminal WebSocket connection drops | 26.1, 26.2, 26.4, 26.5, 26.6 | `apps/api/tests/test_terminal_failure_scenarios.py` :: `def test_`<br>`packages/application/tests/test_terminal_property_no_persistence.py` :: `hands-on-labs, Property 17:` |
| IF a grading check does not return | 11.3, 28.1, 28.2, 28.3 | `tests/labs/test_docker_check_abuse.py` :: `def test_`<br>`packages/application/tests/test_grade_run_scheduling_properties.py` :: `hands-on-labs, Property 11:` |
| Labs are hands-on | 12.3, 14.3, 21.1, 22.1 | `tests/labs/test_docker_terminal.py` :: `def test_`<br>`tests/labs/test_docker_harness.py` :: `def test_` |
| A lab check contributes | 19.1, 19.3 | `packages/infrastructure/tests/test_lab_check_gate_property.py` :: `hands-on-labs, Property 13:`<br>`packages/domain/tests/test_property_lab_mastery.py` :: `hands-on-labs, Property 12:` |
| surfaces a translated error | 25.2, 25.4 | `apps/api/tests/test_errors_lab_problems_properties.py` :: `hands-on-labs, Property 30:`<br>`tests/labs/test_provisioning_failure_scenarios.py` :: `def test_` |
| leaves no partial session running | 25.3, 25.6 | `packages/application/tests/test_provision_sandbox_properties.py` :: `hands-on-labs, Property 8:` |
| lets the reaper clean orphaned | 8.4, 25.5 | `packages/application/tests/test_reap_sessions_property.py` :: `hands-on-labs, Property 5:` |
| applies provisioning rate-limits | 7.5, 7.6, 25.1 | `packages/domain/tests/test_lab_property_provision_rate.py` :: `hands-on-labs, Property 9:`<br>`packages/application/tests/test_provision_sandbox_properties.py` :: `hands-on-labs, Property 8:` |
| Maximum provisioning retries | 25.1 | `packages/application/tests/test_provision_sandbox_properties.py` :: `hands-on-labs, Property 8:` |
| Maximum provisioning rate | 7.5, 7.6 | `packages/domain/tests/test_lab_property_provision_rate.py` :: `hands-on-labs, Property 9:` |
| re-verifies session ownership | 12.11, 26.2 | `packages/application/tests/test_terminal_property_auth.py` :: `hands-on-labs, Property 14:` |
| presents a reconnect indication | 13.4, 26.1 | `apps/web/src/components/terminal-pane.test.tsx` :: `describe(`<br>`apps/web/src/lab-state.property.test.ts` :: `hands-on-labs, Property 18:` |
| drains active sockets | 12.8, 26.5 | `packages/application/tests/test_terminal_property_drain.py` :: `hands-on-labs, Property 31:` |
| persists no terminal I/O | 12.7, 26.6 | `packages/application/tests/test_terminal_property_no_persistence.py` :: `hands-on-labs, Property 17:` |
| grading is bounded (results | 7.4, 11, 28 | `tests/labs/test_grading_acceptance_property.py` :: `hands-on-labs, Property 6:`<br>`packages/application/tests/test_grade_run_scheduling_properties.py` :: `hands-on-labs, Property 11:`<br>`tests/labs/test_docker_check_abuse.py` :: `def test_` |
| the timeout yields a translated | 18.3, 28.2, 28.7 | `packages/domain/tests/test_lab_property_learner_view.py` :: `hands-on-labs, Property 10:`<br>`tests/labs/test_docker_check_abuse.py` :: `def test_` |
| the run is recorded with a failed status | 11.4, 28.3 | `packages/application/tests/test_grade_run_scheduling_properties.py` :: `hands-on-labs, Property 11:`<br>`tests/labs/test_docker_check_abuse.py` :: `def test_` |
| security: Sandbox processes run as a non-root user | 3.1, 3.10 | `tests/labs/test_docker_isolation.py` :: `def test_`<br>`packages/infrastructure/tests/test_docker_spec_properties.py` :: `hands-on-labs, Property 1:` |
| security: Sandbox has no network beyond loopback | 3.2, 3.7, 3.10 | `tests/labs/test_docker_isolation.py` :: `def test_` |
| security: the four resource limits (CPU, memory, PIDs, writable disk) | 3.3, 3.6, 3.8, 3.9, 3.10 | `tests/labs/test_docker_isolation.py` :: `def test_`<br>`packages/infrastructure/tests/test_docker_spec_properties.py` :: `hands-on-labs, Property 1:` |
| security: all capabilities dropped, no-new-privileges, unprivileged | 3.4, 3.5, 3.10 | `tests/labs/test_docker_isolation.py` :: `def test_`<br>`packages/infrastructure/tests/test_docker_spec.py` :: `def test_` |
| security: no learner code in the api or worker process | 5.1, 5.2, 5.3, 5.4, 5.5 | `tests/labs/test_static_analysis_canary.py` :: `def test_` |
| security: grading isolation (read-only root-owned bundle, Grader_User, removed after) | 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 10.10, 10.12 | `tests/labs/test_docker_grading_isolation.py` :: `def test_`<br>`packages/application/tests/test_grade_run_independence_properties.py` :: `hands-on-labs, Property 26:` |
| security: fail-closed freeze of learner processes before any Check | 10.7, 10.8, 10.11, 28.8 | `tests/labs/test_docker_freeze_failure.py` :: `def test_`<br>`packages/application/tests/test_grade_run_freeze_property.py` :: `hands-on-labs, Property 35:` |
| security: tamper resistance (files, PATH, aliases, environment, special files) | 14.12, 29.1, 29.2, 29.3, 29.4, 29.5, 29.9, 29.10, 29.11 | `tests/labs/test_seed_lab_1_tamper.py` :: `def test_`<br>`tests/labs/test_seed_lab_2_tamper.py` :: `def test_`<br>`tests/labs/test_docker_harness.py` :: `def test_`<br>`tests/labs/test_unsafe_learner_files_property.py` :: `hands-on-labs, Property 36:` |
| security: throttles (one active session, provisioning and grading rate limits) | 7.1, 7.4, 7.5, 30.1, 30.3, 30.4 | `tests/labs/test_one_active_session_property.py` :: `hands-on-labs, Property 3:`<br>`tests/labs/test_grading_acceptance_property.py` :: `hands-on-labs, Property 6:`<br>`packages/application/tests/test_request_grading_sequences_properties.py` :: `hands-on-labs, Property 7:`<br>`packages/domain/tests/test_lab_property_provision_rate.py` :: `hands-on-labs, Property 9:`<br>`apps/api/tests/test_labs_second_session_failures.py` :: `def test_` |
| security: the Docker provider is dev only and refused in production | 2.1, 2.2, 2.3 | `packages/infrastructure/tests/test_sandbox_provider_selection.py` :: `def test_`<br>`packages/infrastructure/tests/test_docker_spec.py` :: `def test_` |

### 3.2 Outcome of each test group

| Test group | Rules | Outcome |
|------------|-------|---------|
| `packages/infrastructure/tests/test_lab_check_gate_property.py` | 2 | PASSED: 1 passed in 1.10s |
| `packages/domain/tests/test_property_lab_mastery.py` | 2 | PASSED: 1 passed in 0.45s |
| `packages/infrastructure/tests/test_sandbox_provider_contract.py` | 2 | PASSED: 12 passed in 5.35s |
| `packages/infrastructure/tests/test_sandbox_provider_lifecycle_property.py` | 1 | PASSED: 1 passed in 2.19s |
| `packages/domain/tests/test_lab_ports.py` | 2 | PASSED: 11 passed in 0.25s |
| `packages/infrastructure/tests/test_fake_lab_adapters.py` | 1 | PASSED: 8 passed in 0.74s |
| `packages/infrastructure/tests/test_sandbox_provider_selection.py` | 3 | PASSED: 3 passed in 0.84s |
| `apps/web/src/components/terminal-pane.test.tsx` | 2 | PASSED: covered by check labs-web |
| `apps/web/src/lab-state.property.test.ts` | 2 | PASSED: covered by check labs-web |
| `packages/application/tests/test_terminal_property_auth.py` | 2 | PASSED: 1 passed in 1.64s |
| `apps/api/tests/test_labs_terminal_route.py` | 1 | PASSED: 5 passed in 1.02s |
| `apps/worker/tests/test_lab_worker_wiring.py` | 1 | PASSED: 9 passed in 0.73s |
| `packages/application/tests/test_reap_sessions_property.py` | 2 | PASSED: 1 passed in 1.28s |
| `packages/application/tests/test_provision_sandbox_properties.py` | 5 | PASSED: 1 passed in 1.01s |
| `tests/labs/test_docker_terminal.py` | 2 | PASSED: 5 passed in 6.42s |
| `packages/application/tests/test_terminal_gateway.py` | 1 | PASSED: 10 passed in 1.40s |
| `packages/infrastructure/tests/test_docker_spec_properties.py` | 4 | PASSED: 1 passed in 0.94s |
| `tests/labs/test_docker_isolation.py` | 6 | PASSED: 11 passed in 28.56s |
| `packages/infrastructure/tests/test_docker_provider.py` | 1 | PASSED:   warnings.warn(pytest.PytestUnraisableExceptionWarning(msg)) |
| `packages/application/tests/test_terminal_property_drain.py` | 2 | PASSED: 2 passed in 1.16s |
| `apps/api/tests/test_terminal_failure_scenarios.py` | 2 | PASSED: 4 passed in 1.08s |
| `packages/application/tests/test_provision_sandbox.py` | 1 | PASSED: 11 passed in 0.74s |
| `packages/application/tests/test_reap_sessions.py` | 1 | PASSED: 9 passed in 0.73s |
| `packages/application/tests/test_grade_run.py` | 1 | PASSED: 11 passed in 0.80s |
| `tests/labs/test_grading_acceptance_property.py` | 3 | PASSED: 8 passed in 0.95s |
| `packages/application/tests/test_grade_run_scheduling_properties.py` | 4 | PASSED: 2 passed in 1.58s |
| `packages/application/tests/test_grade_run_queue_delay.py` | 1 | PASSED: 5 passed in 0.74s |
| `tests/labs/test_docker_check_abuse.py` | 5 | PASSED: 3 passed in 23.93s |
| `tests/labs/test_provisioning_failure_scenarios.py` | 2 | PASSED: 12 passed in 0.89s |
| `packages/application/tests/test_terminal_property_no_persistence.py` | 2 | PASSED: 1 passed in 4.92s |
| `tests/labs/test_docker_harness.py` | 2 | PASSED: 14 passed in 18.42s |
| `apps/api/tests/test_errors_lab_problems_properties.py` | 1 | PASSED: 1 passed in 1.40s |
| `packages/domain/tests/test_lab_property_provision_rate.py` | 3 | PASSED: 2 passed in 0.43s |
| `packages/domain/tests/test_lab_property_learner_view.py` | 1 | PASSED: 4 passed in 0.55s |
| `packages/infrastructure/tests/test_docker_spec.py` | 2 | PASSED: 10 passed in 0.67s |
| `tests/labs/test_static_analysis_canary.py` | 1 | PASSED: 20 passed in 1.62s |
| `tests/labs/test_docker_grading_isolation.py` | 1 | PASSED: 15 passed in 8.16s |
| `packages/application/tests/test_grade_run_independence_properties.py` | 1 | PASSED: 1 passed in 1.80s |
| `tests/labs/test_docker_freeze_failure.py` | 1 | PASSED: 6 passed in 13.75s |
| `packages/application/tests/test_grade_run_freeze_property.py` | 1 | PASSED: 1 passed in 2.31s |
| `tests/labs/test_seed_lab_1_tamper.py` | 1 | PASSED: 19 passed, 2 skipped in 14.02s |
| `tests/labs/test_seed_lab_2_tamper.py` | 1 | PASSED: 20 passed, 2 skipped in 52.36s |
| `tests/labs/test_unsafe_learner_files_property.py` | 1 | PASSED: 1 skipped in 0.36s |
| `tests/labs/test_one_active_session_property.py` | 1 | PASSED: 1 passed in 1.56s |
| `packages/application/tests/test_request_grading_sequences_properties.py` | 1 | PASSED: 1 passed in 1.25s |
| `apps/api/tests/test_labs_second_session_failures.py` | 1 | PASSED: 8 passed in 1.24s |

## 4. Expert review outcome (Requirement 23)

- `lab.fix-the-shared-folder`: reviewer recorded: `kanli-adanlessossi` at 2026-11-02T09:00:00Z
- `lab.world-writable-entries`: reviewer recorded: `kanli-adanlessossi` at 2026-11-02T09:00:00Z
- Pending_Review_List: empty
- Strict `content validate` (no tolerated failure): PASSED
- Golden_Set addition confirmed after the recorded reviewer (approval-flow tests, Requirements 23.3, 23.5, 23.9): PASSED
