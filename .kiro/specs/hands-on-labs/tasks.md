# Implementation Plan: Hands-on Labs

## Overview

This plan implements the hands-on labs of EDU-IT across three milestones, honoring the layering
`api → application → domain ← infrastructure`, the injected `Clock`, and the rule that every
business number lives in `packages/domain/edu_it_domain/config.py` (never hardcoded in services).
Learner code is hostile: it runs only inside a Sandbox behind the `SandboxProvider` port, never in
the api or worker process, and grading integrity never depends on anything the learner can touch.

- **Milestone A: Sandbox runtime** (tasks 1 to 9): Config and its startup validation, pure domain
  rules, ports, the fake and Docker providers (Docker is dev only), lab images, the dedicated image
  CI workflow and the image-command flag-compatibility spike, persistence, worker jobs
  (provisioning, grading with a fail-closed freeze, reaper, retention), the labs API, and the Docker
  isolation, failure scenario, and static-analysis tests.
- **Milestone B: Terminal and code** (tasks 10 to 13): the authenticated terminal WebSocket
  gateway with reconnect and draining shutdown, xterm.js and Monaco in the web app, and the code
  exercise harness with no-follow learner-file intake.
- **Milestone C: Lab experience and content** (tasks 14 to 24): content schema, `content validate`
  with strict and `--allow-pending` modes, mastery and GDPR wiring, the Lab_View, the two seed labs,
  the EXPERT REVIEW CHECKPOINT (task 18), golden-set addition, `docs/LABS.md`, end-to-end and
  axe-core tests, performance measurement, the Requirement 33 traceability suite, and the phase
  report.

ADR 0001 pinning (task 20.3) is in the first wave and the ADR-vs-manifest test (task 20.4) runs from
the next wave on, so no dependency is added to a manifest before it is pinned (Requirements 4.12
to 4.14). The flag-compatibility spike (task 3.4) comes before any provider, harness, setup, or
Check code that calls `ps`, `kill`, `timeout`, or `tar` inside an image.

Property-based tests use Hypothesis (Python) and fast-check (web), run a minimum of 100 iterations,
read numbers from `domain/config.py`, are driven by the fake `Clock` (`Test_Clock`), use
`Fake_SandboxProvider` and in-memory repositories, and are tagged with a comment of the form
`Feature: hands-on-labs, Property {n}: {property text}`. Stateful properties (2, 3, 5, 7, 8, 14, 35)
use Hypothesis rule-based state machines. Behavior that depends on a real kernel or Docker daemon is
covered by tests carrying the `docker` pytest marker instead. Fake_Provider tests verify call logic
only; tamper resistance is evidenced by the Docker tests (Requirements 10.12, 29.11).

Languages: Python for `packages/domain`, `packages/application`, `packages/infrastructure`,
`apps/api`, `apps/worker`, `tools/`, and the `edu-it` CLI; TypeScript + Next.js App Router for
`apps/web`; Dockerfiles and a bake file for `lab-images/`; POSIX shell for lab setup and Check
scripts. No new stack beyond the pinned additions recorded in ADR 0001 (task 20.3).

## Tasks

- [x] 1. Milestone A: Domain foundation (Config, docs amendments, models, pure rules)
  - [x] 1.1 Extend `domain/config.py` with all lab business numbers
    - Add every constant in the design's Config table: `LAB_SESSION_TTL`, `LAB_IDLE_TIMEOUT`, `LAB_GRADING_MIN_INTERVAL`, `LAB_GRADING_RUN_DEADLINE` (15 s), `LAB_GRADING_FREEZE_BOUND` (1 s), `LAB_GRADING_FREEZE_ATTEMPTS` (5), `LAB_GRADING_TEARDOWN_RESERVE` (2 s), `SANDBOX_OPERATION_TIMEOUT` (10 s), `LAB_ALLOWED_WEB_ORIGINS` (defaults to the existing configured web origin, must be non-empty), `LAB_CHECK_TIMEOUT_DEFAULT`, `LAB_PROVISION_*`, `SANDBOX_*` limits and the 160/32/32/32 MiB quota split, `LAB_PROGRAM_*`, `LAB_REAPER_INTERVAL`, `LAB_SWEEP_INTERVAL`, `LAB_RETENTION_*`, `TERMINAL_*`, `LAB_RECONNECT_BACKOFF`, `LAB_FILE_MAX_*`, `LAB_WARNING_THRESHOLD`, `LAB_TASK_MASTERY_GAIN`, `LAB_RESULT_POLL_INTERVAL`
    - Expose the derived Usable_Run_Budget (not stored) as run deadline minus freeze bound minus teardown reserve = 15 - 1 - 2 = 12 s
    - All named constants read by services; no value hardcoded outside Config; the hint multiplier reuses `HINT_MULTIPLIER`
    - _Requirements: 1.6, 1.8, 1.9, 1.11, 3.3, 7.2, 7.5, 7.6, 8.1, 9.1, 10.7, 11.2, 11.3, 12.5, 12.9, 12.10, 13.4, 14.6, 15.8, 19.8, 25.3_
  - [x] 1.2 Apply the additive D4 amendments and the D3 §13 lab-table coverage to the design docs
    - Update `docs/LABS-DESIGN.md` (D4) in the same change as the code: `list_sandboxes()`, optional `timeout_s` on `exec`, optional `as_user` on `copy_in`, a defined `ExecResult`, a synchronous `TerminalChannel`
    - Update `docs/DATA-MODEL.md` (D3) §13 so the lab tables' export, deletion, and retention coverage reflects the added columns (including `lab_run.accepted_at` and `deadline_at`)
    - _Requirements: 1.1, 24.4_
  - [x] 1.3 Define framework-free lab domain models in `edu_it_domain/labs/`
    - `LabSession`, `GradingRun` (with `accepted_at`, `deadline_at`, `started_at`), `CheckResult`, `TaskCompletion`, `SandboxSpec`, `WritableMounts`, status enums (`provisioning`, `ready`, `grading`, `ended`, `expired`, `idle-expired`, `failed`; run: `running`, `passed`, `failed`, `could-not-complete`)
    - `sandbox_spec.py`: `build_sandbox_spec(lab, session_id, config)` (non-root, no network, four Config limits, drop all capabilities, no-new-privileges, no host mounts, tmpfs mounts) and `split_writable_quota(total_bytes)` whose parts sum exactly to the total
    - `CheckResult` never carries output, exit codes, or paths
    - _Requirements: 1.1, 1.6, 3.1, 3.2, 3.3, 3.4, 3.5, 11.4, 11.8_
  - [x] 1.4 Define the lab ports in `edu_it_domain/ports/`
    - `sandbox.py`: `SandboxProvider`, `TerminalChannel`, `SandboxHandle`, `ExecResult`, `HealthStatus`, `ProvisionError`, `ProvisionTimeout`, `ProviderUnavailable`, `SandboxGone`, and the `WarmPool` protocol (no implementation)
    - `lab_sessions.py`: `LabSessionRepository` (incl. `add_if_no_active`, CAS `transition`, `try_accept_grading`, `claim_provision_attempt`) and `GradingRunRepository`; `lab_catalog.py`: `LabCatalog`, `LabDefinition`, `labs_covering`
    - `@runtime_checkable` Protocol pattern; no Docker, web, or queue import in the domain
    - _Requirements: 1.1, 1.5, 1.9, 7.1, 7.4, 17.5_
  - [x] 1.5 Implement `labs/session_rules.py`
    - `deadlines`, `last_activity` (terminal input, file saves, grading requests only; sandbox output is not activity), `reap_decision` (`KEEP`, `EXPIRE`, `IDLE_EXPIRE`, `DEFER`, `DESTROY_ONLY`; `EXPIRE` only when `now > ttl_at`, so a session at exactly `ttl_at` is kept), `grading_eligibility` (accept iff status `ready`, `now <= ttl_at`, gap strictly more than 10 s; returns `retry_after`), `provision_rate_decision`, `provision_retry_decision`
    - Time only through the injected `Clock`
    - _Requirements: 1.7, 6.4, 7.2, 7.3, 7.4, 7.5, 7.6, 7.8, 11.1, 25.1, 27.2, 27.5, 27.7, 30.3, 30.4_
  - [x] 1.6 Implement `labs/grading.py`
    - `remaining_budget(accepted_at, job_started_at, spent_on_checks, config)` = Usable_Run_Budget minus queue delay (measured from `accepted_at`) minus time spent on Checks; `effective_timeout(check_timeout, remaining)` = `min(check_timeout, remaining)` where a non-positive result means "do not run"; `learner_all_stopped(ps_rows)` true iff no learner-owned process is in a running state (stopped and zombie count as stopped)
    - `map_exit_code` (unknown code, signal, timeout, and the reserved code 90 for an unsafe path all become `could_not_complete`), `aggregate_run(results, abort)` (`abort` for a vanished Sandbox, an unverified freeze, or an unsafe learner file gives a `could_not_complete` run), `to_learner_view` (task id, check id, outcome, feedback key, translated message only), and strict-schema `parse_harness_report` that treats embedded learner output as base64 data
    - Malformed or missing reports yield `could_not_complete`; verdicts come only from strict comparison with expected values
    - _Requirements: 10.7, 10.8, 10.9, 11.2, 11.3, 11.4, 11.8, 14.5, 18.3, 28.1, 28.2, 28.3, 28.5, 28.7, 28.8, 29.7, 29.9_
  - [x] 1.7 Implement `labs/files.py`, `labs/limits.py`, and `labs/progression.py`
    - `validate_learner_files` (count and size caps, allowed names, no path separators or traversal), `TokenBucket` and `HeartbeatTracker` over `Clock`, and `apply_lab_results` (first pass writes one counted completion and one `lab` mastery event per covered concept with `LAB_TASK_MASTERY_GAIN`, hint multiplier only for pre-first-pass reveals, zero-delta `lab_passed` marker, no negative deltas)
    - _Requirements: 12.9, 14.6, 14.7, 19.1, 19.2, 19.4, 19.5, 19.8_
  - [x] 1.8 Write property test for deadlines
    - **Property 4: Deadlines are derived from learner activity and the TTL only**
    - **Validates: Requirements 6.4, 7.2, 12.6**
  - [x] 1.9 Write property test for the provisioning rate limit
    - **Property 9: Provisioning rate limit**
    - **Validates: Requirements 1.6, 7.5, 30.3**
  - [x] 1.10 Write property test for learner-facing check results
    - **Property 10: Learner-facing check results carry no raw output**
    - Plant a unique marker in arbitrary stdout/stderr; assert it never appears in the view; timeout, signal, unmapped code, reserved code 90, and resource abuse give the identical `could_not_complete` view
    - **Validates: Requirements 5.3, 18.3, 18.6, 22.9, 28.2, 28.7, 28.8**
  - [x] 1.11 Write property test for harness report parsing
    - **Property 27: Harness reports are data, comparison is strict**
    - Include forged report lines inside embedded learner output
    - **Validates: Requirements 10.9, 14.5, 29.7**
  - [x] 1.12 Write example boundary tests for the pure rules
    - Grading at exactly 10 s (rejected) and just over 10 s (eligible); grading request at exactly `ttl_at` (accepted) and strictly after `ttl_at` (rejected); `reap_decision` expires only when `now > ttl_at` (kept at exactly `ttl_at`, `EXPIRE` just after); idle deadline boundary; exit-code mapping table including code 90; no wall-clock reads in `labs/`
    - _Requirements: 7.4, 7.8, 7.9, 27.5, 27.7, 30.4, 1.7_
  - [x] 1.13 Implement `labs/config_rules.py` with `validate_lab_config(config)`
    - Pure, no I/O; raises `LabConfigError` naming exactly the offending keys when any lab Config value is missing or not positive (including an empty `LAB_ALLOWED_WEB_ORIGINS` and every non-positive element of `LAB_RECONNECT_BACKOFF`), or when `LAB_GRADING_FREEZE_BOUND + LAB_GRADING_TEARDOWN_RESERVE >= LAB_GRADING_RUN_DEADLINE` (the error names those three keys); a valid Config returns without error
    - _Requirements: 1.8, 1.11_
  - [x] 1.14 Write property test for lab Config validation
    - **Property 34: Lab Config validation names every offending key**
    - Each value independently valid, zero, negative, or missing; vary the origin list, freeze bound, teardown reserve, and run deadline; a valid Config yields a Usable_Run_Budget equal to deadline minus bound minus reserve
    - **Validates: Requirements 1.8, 1.11**
  - [x] 1.15 Write unit tests for the grading budget functions
    - `remaining_budget` deducts queue delay measured from `accepted_at` and time spent on Checks (12 s budget, delay under, equal to, and over the budget); `effective_timeout` returns `min` and a non-positive result for no remaining budget; `learner_all_stopped` for running, stopped, zombie, other-user, and empty listings
    - _Requirements: 10.7, 11.2, 11.3, 11.8_

- [x] 2. Milestone A: Sandbox providers (fake, Docker, contract suite)
  - [x] 2.1 Implement `Fake_SandboxProvider`, in-memory lab repositories, and a fake `LabCatalog`
    - `fakes/sandbox_provider.py`: in-memory sandboxes (users, file map, processes as data), advances `Test_Clock` by a configurable virtual duration per call, configurable to fail, hang (`ProvisionTimeout`), or partially create; imports no subprocess, socket, or Docker module
    - In-memory `LabSessionRepository`/`GradingRunRepository` honoring CAS semantics and one-active-session-per-user
    - _Requirements: 1.2, 1.3, 25.7_
  - [x] 2.2 Write property test for the provider lifecycle model
    - **Property 2: Provider lifecycle model, destroy is idempotent**
    - Hypothesis rule-based state machine over provision, exec, copy_in, copy_out, health, destroy, list
    - **Validates: Requirements 1.2, 8.4**
  - [x] 2.3 Implement the pure `docker_spec.py` translator
    - `to_container_kwargs(spec)`: `user="1000:1000"`, `network_mode="none"`, `nano_cpus`, equal `mem_limit`/`memswap_limit`, `pids_limit`, `read_only=True` with size-limited tmpfs for `/workspace`, `/home/learner`, `/tmp`, `/grader`, `cap_drop=["ALL"]`, `no-new-privileges:true`, `privileged=False`, `init=True`, no binds/volumes/devices/socket, labels `edu-it.managed` and `edu-it.session`
    - _Requirements: 2.3, 3.1, 3.2, 3.3, 3.4, 3.5_
  - [x] 2.4 Write property test for sandbox spec lockdown
    - **Property 1: Sandbox specifications are always locked down**
    - **Validates: Requirements 1.6, 2.3, 3.1, 3.2, 3.3, 3.4, 3.5**
  - [x] 2.5 Implement `DockerSandboxProvider` (dev only) with the Docker SDK for Python
    - Written against the argv forms recorded by the flag-compatibility spike (task 3.4); the Docker SDK dependency is already pinned in ADR 0001 (task 20.3)
    - Implements every `SandboxProvider` operation; the SDK client is built with `timeout=SANDBOX_OPERATION_TIMEOUT` so an unreachable daemon returns `ProviderUnavailable` within that bound and leaves Lab_Session and Sandbox state unchanged; `copy_in`/`copy_out` stream a tar through an internal `exec` (`tar -x`/`tar -c`), never `put_archive`; `exec` enforces `timeout_s` via `timeout -s KILL` plus a client-side backstop, then `kill -KILL -- -1` as the same user; `attach_terminal` returns a synchronous `TerminalChannel`; `list_sandboxes` filters on the managed label; idempotent `destroy`
    - Logs the dev-only `runc` warning at startup
    - _Requirements: 1.2, 1.9, 2.1, 2.3, 3.7, 4.15, 8.4_
  - [x] 2.6 Implement the shared provider contract test suite
    - One suite parametrized over `Fake_SandboxProvider` and `DockerSandboxProvider` (`docker` marker, skipped when no daemon); proves `copy_in` into tmpfs-backed `/grader`, `copy_out` round trip, `SandboxGone` after destroy, idempotent destroy, `list_sandboxes`
    - _Requirements: 1.2, 1.4, 10.1_
  - [x] 2.7 Implement provider selection and the production refusal in composition
    - Select `docker` or `fake` from configuration; refuse to start with a configuration error when `docker` is selected and `EDU_IT_ENV=production`
    - _Requirements: 2.1, 2.2_
  - [x] 2.8 Write tests for the dev-only guard
    - Startup warning is logged; production plus `docker` refuses to start; `fake` and non-production `docker` start; no Docker socket, host path, or device mount in the translated kwargs
    - _Requirements: 2.1, 2.2, 2.3_
  - [x] 2.9 Wire `validate_lab_config()` into the api and worker composition
    - Both `apps/api` and `apps/worker` call it when they compose their dependencies, before any provider call; on `LabConfigError` they refuse to start, report the offending keys, and create no Sandbox
    - _Requirements: 1.8, 1.11_
  - [x] 2.10 Write startup and provider-unavailable tests
    - Composition with a missing or non-positive value, an empty `LAB_ALLOWED_WEB_ORIGINS`, and freeze bound plus teardown reserve at or above the run deadline each refuse to start, name the keys, and create no Sandbox (api and worker); the Docker provider configured with an unreachable daemon returns provider-unavailable within `SANDBOX_OPERATION_TIMEOUT` and leaves state unchanged (the same behavior is asserted against the Fake_Provider)
    - _Requirements: 1.8, 1.9, 1.10, 1.11_

- [x] 3. Milestone A: Lab images, CI build and scan, command spike
  - [x] 3.1 Define `lab-images/linux-base`, `lab-images/python`, `docker-bake.hcl`, and a README
    - `alpine@sha256:<digest>` plus `bash`, `coreutils`, `findutils`, `tar`, `procps`; `python3` only in the Python image (built on the base through a bake named context); users `learner` (1000) and `grader` (1001); group `devs` with the learner as member; no setuid binaries; `USER learner`; no secrets, Check_Bundle, or solutions
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 21.5_
  - [x] 3.2 Add the dedicated image workflow `.github/workflows/lab-images.yml` and the Docker test job
    - `lab-images.yml` builds both Lab_Images and scans each with a scanner configured to fail on any finding regardless of severity or fixability; triggers are `push` and `pull_request` with `paths: ["lab-images/**"]` plus a weekly `schedule`, so it is not a gate on other paths; a scheduled finding is simply a failed run; no scanner ignore file, allowlist, severity threshold, or waiver key anywhere in the repository
    - Add a Linux-runner job to the existing `ci.yml` for `docker`-marked tests
    - The scanner version is already pinned in ADR 0001 (task 20.3)
    - _Requirements: 4.5, 4.6, 4.7, 4.8, 4.10_
  - [x] 3.3 Write static and built-image tests for the Lab_Images and workflows
    - Digest pins on every external `FROM`, non-root default user, no secrets/bundles/solution files in a built image
    - Workflow tests parse `lab-images.yml`: it contains the build step and a fail-on-any-finding scan step; its triggers are exactly `push`/`pull_request` limited to `lab-images/**` plus a weekly `schedule`; no job in it is required by other paths
    - Waiver-absence test fails if any scanner ignore file, allowlist, waiver key, or severity threshold exists in the repository or workflows
    - _Requirements: 4.2, 4.3, 4.4, 4.5, 4.6, 4.7, 4.8, 4.10, 4.11_
  - [x] 3.4 Run the flag-compatibility spike for `ps`, `kill`, `timeout`, and `tar` in the Alpine images
    - Build both images and, inside them, determine the absolute path of each command and the exact BusyBox-compatible argv forms the code will use (freeze `kill -s STOP -1`, thaw `kill -s CONT -1`, `kill -s KILL -1` and `kill -KILL -- -1`, the `ps` listing with columns for user and state, `timeout -s KILL <s>`, `tar -x`/`tar -c` through stdin/stdout)
    - Record the paths and argv forms in `lab-images/README.md`; this must land before 2.5, 6.8, 6.10, 6.13, 12.1, and the Check and setup scripts of 17.1 and 17.2 are written
    - _Requirements: 4.15_
  - [x] 3.5 Write `tests/labs/test_image_commands.py` (`docker` marker)
    - Runs in each built Lab_Image and asserts that `ps`, `kill`, `timeout`, and `tar` exist at the recorded paths and accept every flag the harness, setup scripts, Checks, and provider use, using the argv forms recorded in `lab-images/README.md`
    - _Requirements: 4.15, 4.16_

- [x] 4. Checkpoint: domain, providers, and images
  - Ensure all tests pass, ask the user if questions arise.

- [x] 5. Milestone A: Persistence
  - [x] 5.1 Add Alembic revision `0002_labs_runtime` and update `metadata.py`
    - Additive only (`op.add_column`, index creation): `lab` (`kind`, `image`, `concept_stable_ids`, `archived_at`), `lab_task.required`, `lab_check` (`timeout_seconds`, `script`), `lab_session` (`sandbox_ref`, `last_activity_at`, `last_grading_accepted_at`, `ended_at`, `provision_attempts`, `next_provision_at`, `terminal_epoch`, partial unique index on `user_id` where status is `provisioning`, `ready`, or `grading`), `lab_run` (`stable_id` unique, `accepted_at` and `deadline_at` (`accepted_at` + run deadline, both from the `Clock`), nullable `started_at`, `results`), `lab_task_completion.counted_for_mastery`, `hint_reveal.lab_task_id` with a check constraint requiring exactly one of `lab_task_id` and `question_id`
    - Extend `HintRevealRecord` with an optional `lab_task_id`; the quiz hint path keeps working
    - _Requirements: 6.1, 7.1, 11.4, 11.8, 15.3, 19.1, 24.4_
  - [x] 5.2 Implement PostgreSQL adapters for the lab repositories
    - `LabSessionRepository` and `GradingRunRepository` with compare-and-set transitions, `try_accept_grading`, `claim_provision_attempt`, keyset pagination by `stable_id`, idempotent per-check `append_result`, run creation storing `accepted_at` and `deadline_at`; in-memory parity with task 2.1
    - _Requirements: 6.1, 7.1, 11.4, 11.8, 17.1, 17.5, 30.2_
  - [x] 5.3 Write repository contract and migration tests
    - Same suite over in-memory and PostgreSQL; concurrent starts yield one Active_Session; CAS losers do not win; `lab_run.accepted_at`/`deadline_at` round trip; migration upgrade/rollback and set-equality tests keep passing; no new personal-data table outside `PERSONAL_DATA_TABLES`
    - _Requirements: 7.1, 11.8, 24.5, 30.2_

- [x] 6. Milestone A: Application use cases and worker jobs
  - [x] 6.1 Implement `StartLab`, `EndSession`, `GetSession`, and `ListSessions`
    - Rate-limit check, `add_if_no_active`, enqueue `lab.provision` with dedupe key `provision:<session_id>`, `Idempotency-Key` through the existing store; `EndSession` CAS to `ended` and enqueues `lab.destroy`; reset = end then start for the same lab, completions untouched
    - Typed application errors for active-session conflict (same or different lab) and rate limiting
    - _Requirements: 6.1, 6.4, 7.1, 7.5, 7.7, 15.7, 17.4, 30.1, 30.2, 30.3_
  - [x] 6.2 Write property test for the one-active-session invariant
    - **Property 3: At most one Active_Session per learner**
    - Hypothesis rule-based state machine over start, end, reset, fail, expire, and concurrent starts
    - **Validates: Requirements 6.1, 7.1, 7.7, 15.7, 25.6, 30.1, 30.2**
  - [x] 6.3 Implement `SaveFiles`, `RequestGrading`, and `RevealLabHint`
    - `SaveFiles` validates caps and `copy_in`s as the learner user, records activity; `RequestGrading` runs `grading_eligibility`, then `try_accept_grading`, creates the `GradingRun` with `accepted_at = Clock.now()` and `deadline_at = accepted_at + run deadline` and enqueues `lab.grade`, repeat `Idempotency-Key` returns the stored run, non-owner indistinguishable from missing; `RevealLabHint` grants level k only after k-1 and records one `hint_reveal`
    - _Requirements: 11.1, 11.5, 11.8, 14.2, 14.6, 14.7, 15.3, 17.4, 27.5, 27.7, 30.4, 30.5, 30.6_
  - [x] 6.4 Write property test for grading acceptance
    - **Property 6: Grading is accepted exactly when the rule says so**
    - Include a request at exactly `ttl_at` (accepted) and strictly after (rejected)
    - **Validates: Requirements 7.3, 7.4, 7.8, 11.1, 11.5, 27.5, 27.7, 30.4, 30.5, 30.6**
  - [x] 6.5 Write property test for throttled, idempotent grading sequences
    - **Property 7: Grading request sequences stay throttled and idempotent**
    - **Validates: Requirements 17.4**
  - [x] 6.6 Write property test for progressive hints
    - **Property 20: Hints are revealed progressively**
    - **Validates: Requirements 15.3**
  - [x] 6.7 Write property test for learner file caps and path safety
    - **Property 21: Learner file caps and path safety**
    - Rejected requests copy nothing into the Sandbox
    - **Validates: Requirements 14.2, 14.6, 14.7**
  - [x] 6.8 Implement `ProvisionSandbox` (worker job logic)
    - Claim an attempt by CAS on `provision_attempts`, build the spec, `provision` under `LAB_PROVISION_TIMEOUT`, `copy_in` and run the setup script as the learner (using the argv forms recorded by task 3.4), remove it, CAS `provisioning → ready` and record `sandbox_ref`; on failure destroy any partial handle, set `next_provision_at = now + 10 s`; after the third failed attempt set `failed`; duplicate deliveries exit
    - _Requirements: 6.2, 6.3, 6.6, 7.6, 25.1, 25.2, 25.3_
  - [x] 6.9 Write property test for bounded provisioning
    - **Property 8: Provisioning is bounded, spaced, and creates at most one sandbox**
    - **Validates: Requirements 6.2, 6.6, 7.6, 25.1, 25.2, 25.3, 25.7**
  - [x] 6.10 Implement `GradeRun` and `RecordCheckResult`
    - Run deadline = `accepted_at` + 15 s = freeze bound (1 s) + Usable_Run_Budget (12 s) + teardown reserve (2 s), all from Config; the whole body runs in a `try` whose `finally` always thaws
    - Step order: (1) read `accepted_at`/`deadline_at` and compute `remaining = remaining_budget(accepted_at, now, 0, config)`, deducting queue delay; if `remaining <= 0` record every Check `could_not_complete` and go straight to teardown without freezing or copying; (2) freeze the learner processes with SIGSTOP and verify with `learner_all_stopped` up to `LAB_GRADING_FREEZE_ATTEMPTS` times within `LAB_GRADING_FREEZE_BOUND`, fail-closed BEFORE any `copy_in` or Check: if not verified, copy nothing in, run no Check, record the run and every Check `could_not_complete`, grant no mastery; (3) `copy_in` of the `sandbox/` bundle part from the read-only source into root-owned `/grader` (plus Grader_User-owned `/grader/run`); (4) each Check as the Grader_User with clean env, fixed `PATH`, absolute paths, explicit cwd, per-Check `effective_timeout(check_timeout, remaining)` (skip when not positive), `kill -KILL -- -1` as grader after each Check; shell Checks use the exit code as result channel (reserved code 90 = unsafe path = `could_not_complete`); code Checks via the in-sandbox harness with expected values read by the worker from `expected/`, and a harness `unsafe-learner-file` report aborts the run as `could_not_complete`; (5) persist each `CheckResult` immediately and apply mastery; (6) `SandboxGone` records remaining Checks and the run as `could_not_complete`; (7) teardown in `finally`: remove `/grader`, and in a nested `finally` thaw with SIGCONT in every outcome, then finish the run and CAS `grading → ready`
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 10.7, 10.8, 10.9, 11.2, 11.3, 11.4, 11.6, 11.8, 14.3, 14.4, 14.5, 14.11, 27.3, 27.4, 28.1, 28.3, 28.4, 28.5, 28.8, 29.9_
  - [x] 6.11 Write property test for grading run scheduling and aggregation
    - **Property 11: Grading run scheduling and aggregation**
    - Updated to cover queue delay deducted from the Usable_Run_Budget (budget measured from the accepted instant), the freeze bound reserved outside the Usable_Run_Budget, `effective_timeout` per Check, and Checks not reached recorded `could_not_complete`
    - **Validates: Requirements 11.2, 11.3, 11.4, 11.7, 11.8, 11.9, 27.4, 28.1, 28.3, 28.4, 28.5**
  - [x] 6.12 Write property test for grading independence from learner-controlled state
    - **Property 26: Grading calls never depend on learner-controlled state**
    - Compare the provider-call sequence against an untampered sandbox; bundle removed even on failure
    - Evidence class: this verifies call logic only with the Fake_Provider and is not security evidence; tamper resistance is evidenced by the Docker tamper tests (Requirements 10.12, 29.11)
    - **Validates: Requirements 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 14.3, 14.4, 29.1, 29.2, 29.3, 29.4**
  - [x] 6.13 Implement `ReapSessions`
    - Apply `reap_decision` to every active session (expire only when `now > ttl_at`), destroy sandboxes of terminal sessions, destroy orphans via `list_sandboxes()`, defer destruction while a run is inside its 15 s deadline, close stale `running` runs as `could_not_complete` and thaw the learner processes (retrying a failed thaw); destroy failures are logged and retried next tick
    - _Requirements: 6.5, 7.2, 7.3, 8.2, 8.3, 8.4, 8.5, 10.8, 13.1, 25.5, 27.1, 27.2_
  - [x] 6.14 Write property test for the reaper
    - **Property 5: Reaper leaves no resources behind and respects deferral**
    - Hypothesis rule-based state machine; a second reaper run changes nothing
    - **Validates: Requirements 6.5, 7.3, 8.2, 8.3, 8.6, 13.1, 25.5, 27.2, 27.3**
  - [x] 6.15 Implement `PurgeLabData` (retention job logic)
    - Delete ended sessions, their runs, and completions older than `LAB_RETENTION_PERIOD` in one transaction; never touch mastery or `mastery_event`; idempotent
    - _Requirements: 9.1, 9.2, 9.3, 9.4_
  - [x] 6.16 Write property test for retention
    - **Property 24: Retention deletes only old ended lab data, idempotently**
    - **Validates: Requirements 9.1, 9.2, 9.3, 9.4**
  - [x] 6.17 Implement worker job handlers, scheduler, and consumer wiring in `apps/worker/edu_it_worker/labs/`
    - Consumers for `lab.provision`, `lab.grade`, `lab.destroy`, `lab.reap`, `lab.retain`, `lab.sweep`; `Scheduler` thread enqueuing periodic jobs on `Clock` ticks with a per-tick dedupe key; composition calls `validate_lab_config()` (task 2.9)
    - _Requirements: 1.8, 6.1, 6.5, 8.1, 9.1_
  - [x] 6.18 Write example tests for worker behavior
    - Setup script runs before `ready`; duplicate `lab.provision` delivery provisions once; duplicate `lab.grade` appends results idempotently; reaper retries a failed destroy and logs it; reaper closes a stale running run and thaws; scheduler tick dedupe
    - _Requirements: 6.3, 6.6, 8.5, 25.5_
  - [x] 6.19 Write property test for the fail-closed freeze
    - **Property 35: Freeze failure is fail-closed**
    - Hypothesis rule-based state machine over freeze-verification outcomes (verified on attempt k of at most 5, never verified, `ps` listings that keep showing a running learner process, slow calls exhausting the 1 s bound on the fake `Clock`, exec errors) and `GradeRun` outcomes (success, exhausted budget, exception at any step, vanished Sandbox); assert no `copy_in` and no Check `exec` without a verified freeze, every Check `could_not_complete`, no mastery, and thaw attempted exactly once after the last Check and `/grader` removal
    - **Validates: Requirements 10.7, 10.8, 10.11, 28.8**

- [x] 7. Milestone A: Labs API
  - [x] 7.1 Implement `errors/labs.py` with translated RFC 9457 problem types
    - Map typed application errors to the design's error table (`409 lab-session-active`, `429 lab-provision-rate-limited`, `429 lab-grading-throttled` with `Retry-After`, `409 lab-grading-in-progress`, `409 lab-session-expired`, `409 lab-session-not-ready`, `404 lab-session-not-found`, `422 lab-files-invalid`, `503 lab-sandbox-unavailable`); FR and EN `title`/`detail`; diagnostics to logs only
    - _Requirements: 14.7, 17.3, 25.2, 27.5, 30.1, 30.3, 30.4, 30.5_
  - [x] 7.2 Write property test for lab error documents
    - **Property 30: Lab errors are translated problem documents without leaks**
    - **Validates: Requirements 14.7, 17.3, 25.2, 27.5, 30.1, 30.3, 30.4, 30.5**
  - [x] 7.3 Implement the labs router and schemas under `/v1`
    - `GET /labs/{lab_id}` (hint text withheld), `POST /lab-sessions` (`Idempotency-Key`, `201`), `GET /lab-sessions/{id}` (status, `server_now`, `ttl_at`, `idle_deadline`, `grading_available_at`, translated error for `failed`), `GET /lab-sessions` (cursor pagination), `DELETE /lab-sessions/{id}`, `POST /lab-sessions/{id}/reset`, `PUT /lab-sessions/{id}/files`, `POST /lab-sessions/{id}/hints`; stable-id addressing only, no internal numeric key in any body, header, or cursor
    - _Requirements: 6.4, 14.2, 14.7, 15.3, 17.1, 17.2, 17.3, 17.4, 17.5, 30.6_
  - [x] 7.4 Write property test for session list pagination
    - **Property 29: Session lists page without gaps or repeats**
    - **Validates: Requirements 17.5**
  - [x] 7.5 Implement grading-run endpoints and Server-Sent Events
    - `POST /lab-sessions/{id}/grading-runs` (`202`, `Idempotency-Key`), `GET .../grading-runs/{run_id}`, and `GET .../grading-runs/{run_id}/events` (one `result` event per Check then `run`, resumable with `Last-Event-ID`, polling the run repository every `LAB_RESULT_POLL_INTERVAL`); only the learner-facing view leaves the API; no internal numeric key in any stream payload
    - _Requirements: 11.6, 17.1, 17.2, 17.4, 18.1, 18.2, 18.3, 18.5_
  - [x] 7.6 Write tests for the grading stream
    - SSE ordering of per-Check results before the final run event; resume with `Last-Event-ID`; a planted marker in Check output is absent from every response and stream payload
    - _Requirements: 18.1, 18.3, 18.6_
  - [x] 7.7 Extend the progress and lesson endpoints
    - `GET /v1/progress` gains a `labs` array (completed tasks, lab-derived mastery); the lesson view gains `lab_ids`
    - _Requirements: 15.12, 19.7_
  - [x] 7.8 Write property test that lab responses expose no internal key
    - **Property 37: Lab responses expose no internal key**
    - Plant internal numeric keys in a range that cannot collide with other numeric fields; call every lab endpoint (success, error, paginated list with cursors, and each SSE payload) and assert the serialized body, headers, cursor, and stream payload contain none and every entity is addressed by `stable_id`
    - **Validates: Requirements 17.1, 17.6**
  - [x] 7.9 Write the route-enumeration test for `/v1`
    - Enumerate the registered lab routes (REST, SSE, and the terminal WebSocket) and fail when any lab route is outside `/v1`
    - _Requirements: 17.2, 17.7_

- [x] 8. Milestone A: Docker isolation, failure-scenario, and static tests
  - [x] 8.1 Write Docker isolation and resource-limit tests
    - Non-root `id -u`; no network (outbound connect fails); fork bomb contained by the PID limit; memory loop contained; disk-fill fails with `ENOSPC` at the tmpfs size; CPU spin held to the CPU limit; capabilities and no-new-privileges visible in `/proc/self/status`; no Docker socket; host and other sandboxes unaffected
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9, 3.10_
  - [x] 8.2 Write Docker lifecycle tests for idle expiry, TTL, and the reaper
    - After the Idle_Timeout and after the TTL elapse on the fake `Clock`, no Sandbox remains in the Fake_Provider and the real container is gone from the runtime; criteria 2 and 3 of Requirement 7 against the Docker_Provider
    - _Requirements: 7.2, 7.3, 7.9, 8.6_
  - [x] 8.3 Write Docker grading-isolation tests
    - Bundle `copy_in`ed root-owned and not learner-writable; no Check or expected value read from a learner-writable path; Checks run as the Grader_User with clean env, fixed `PATH`, absolute paths, explicit cwd; bundle removed after grading; learner processes frozen during a Check; result taken from the Check's own channel
    - Use a minimal test-bundle fixture loaded from repository files
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 10.7, 10.9, 10.10_
  - [x] 8.4 Write Docker Check-timeout and abuse tests
    - Infinite loop, fork bomb, and unbounded allocation inside a Check each finish the run within 15 s, record `could_not_complete` identically to a timeout, later Checks still run while budget remains, the run is `failed`, and no raw output is delivered
    - _Requirements: 11.3, 11.7, 28.1, 28.2, 28.3, 28.4, 28.5, 28.6, 28.7, 28.9_
  - [x] 8.5 Write Fake_Provider grading-isolation and slow-check tests
    - Bundle copied from a read-only source into a root-owned directory and removed after grading (criteria 1, 2, 6); slow-check fixture verifies per-Check timeout and run deadline on the fake `Clock`
    - Documented as call-logic evidence only, not security evidence (Requirement 10.12)
    - _Requirements: 10.10, 10.12, 11.2, 11.3, 11.4, 11.7_
  - [x] 8.6 Write static-analysis and canary tests
    - AST scan over `apps/api` and `apps/worker` failing on `eval`, `exec`, `compile`, `subprocess`, `os.system`, or dynamic imports outside the Docker adapter; the fake provider imports no subprocess or socket; a canary payload that would write a host marker if executed in the api or worker is absent after a full session and grading flow on both providers
    - _Requirements: 1.3, 5.1, 5.2, 5.3, 5.4, 5.5_
  - [x] 8.7 Write failure-scenario tests for provisioning failure or slowness
    - Fake_Provider configured to fail, hang, and partially create: at most 3 attempts at least 10 s apart, each under 30 s; every partial handle destroyed; `failed` session not Active; translated error with no raw provider output; reaper destroys leftovers
    - _Requirements: 25.1, 25.2, 25.3, 25.5, 25.6, 25.7_
  - [x] 8.8 Write failure-scenario tests for timer expiry while working or grading
    - TTL expiry destroys the Sandbox and sets `expired`; the reaper expires only when `now > ttl_at` (a session at exactly `ttl_at` is kept, one just after is expired); expiry during grading defers until the run finishes or the 15 s deadline; a finished run records normally; a vanished Sandbox records `could_not_complete` with no mastery for unfinished tasks; a grading request at exactly `ttl_at` is accepted and one strictly after is rejected, a request before `ttl_at` is processed even if it finishes after; completions kept
    - _Requirements: 7.3, 7.8, 27.1, 27.2, 27.3, 27.4, 27.5, 27.6, 27.7, 27.8_
  - [x] 8.9 Write failure-scenario tests for a second session or grading too often
    - Second start for the same and a different lab (409 naming the running lab, no new session), concurrent starts, provisioning rate limit (429 with `Retry-After`), grading at exactly 10 s (429 with `Retry-After`), grading while running (no second run), foreign owner indistinguishable from missing, all on the fake `Clock`
    - _Requirements: 30.1, 30.2, 30.3, 30.4, 30.5, 30.6, 30.7_
  - [x] 8.10 Write the Docker freeze-failure test
    - A learner process that ignores or escapes `SIGSTOP` (for example, one that keeps respawning) so the freeze cannot be verified: assert no Check ran, nothing was copied into `/grader`, the Grading_Run is `could_not_complete`, no mastery is granted, the translated "could not complete" feedback carries no raw output, and the learner processes run again afterwards (call-logic counterpart: Property 35)
    - _Requirements: 10.7, 10.8, 10.11, 28.8, 28.9_
  - [x] 8.11 Write queue-delay tests with the Fake_Provider and the fake Clock
    - Delay the Grading_Job start after acceptance by (a) less than the Usable_Run_Budget, asserting the Checks run only within the reduced remaining budget, and (b) at least the Usable_Run_Budget, asserting no freeze, no copy-in, no Check runs and every Check is recorded `could_not_complete`
    - _Requirements: 11.8, 11.9_

- [x] 9. Checkpoint: Milestone A complete
  - Ensure all tests pass, ask the user if questions arise.

- [x] 10. Milestone B: Terminal gateway
  - [x] 10.1 Implement `AuthorizeTerminal` and `TerminalGatewayCore`
    - `SocketPort` (`receive`, `send`, `close`) plus a `TerminalChannel`; connect checks in this order: (1) authentication through the cookie `SessionStore`, (2) `Origin` against the `LAB_ALLOWED_WEB_ORIGINS` allowlist, (3) Active_Session state (`provisioning`, `ready`, `grading`), (4) ownership, the only authorization decision on connect; steps 3 and 4 share one uniform refusal (same close code and reason) so unknown, ended, and foreign sessions are indistinguishable; message limits and heartbeats apply only after connect
    - One-live-connection registry plus epoch fence; `TokenBucket` limits (close 1009/1008); ping every 20 s, close 4408 after 2 missed pongs; resize; activity writes throttled to one per 5 s; 2 s status watcher sending a translated `status` frame before closing; `registry.drain()`; reconnects run the same four checks
    - Nothing from terminal frames is logged or stored (INFO logs session id, byte counts, close codes only)
    - _Requirements: 12.1, 12.2, 12.3, 12.4, 12.5, 12.6, 12.7, 12.9, 12.10, 12.11, 13.2, 13.5, 27.1_
  - [x] 10.2 Write property test for terminal authorization and exclusivity
    - **Property 14: Terminal connections are authorized and exclusive**
    - Hypothesis rule-based state machine over connects and reconnects, including origin allowed/not allowed, authenticated or not, Active_Session or not, owner or not; refusals for unknown, non-active, and foreign sessions are identical
    - **Validates: Requirements 12.1, 12.2, 12.10, 12.11, 13.2, 13.5, 26.2, 26.7**
  - [x] 10.3 Write property test for message limits
    - **Property 15: Message limits**
    - **Validates: Requirements 12.9**
  - [x] 10.4 Write property test for heartbeat closure
    - **Property 16: Heartbeat closes after consecutive missed pongs**
    - **Validates: Requirements 12.5**
  - [x] 10.5 Write property test that no terminal I/O or learner file content is persisted
    - **Property 17: No terminal I/O or learner file content is persisted**
    - A unique marker never appears in any repository row, log record, export payload, or queue payload, including after a socket drop
    - **Validates: Requirements 12.7, 14.9, 24.3, 26.6**
  - [x] 10.6 Implement the `apps/api` terminal WebSocket route
    - `WS /v1/lab-sessions/{id}/terminal`: connect order authentication (cookie session through `SessionStore`, ADR 0004), then `Origin` allowlist check, then Active_Session state, then ownership, with the uniform refusal for the last two; binary frames for terminal bytes and text JSON frames for `resize`, `ping`/`pong`, `reconnect`, `status`; the CSRF middleware passes `websocket` scopes through; the WebSocket library is already pinned in ADR 0001
    - _Requirements: 12.1, 12.2, 12.3, 12.4, 12.10, 12.11, 26.7_
  - [x] 10.7 Implement the draining server in `serve.py`
    - `uvicorn.Server` subclass overriding `handle_exit` to call `registry.drain()` first (send `{"type":"reconnect"}` then close 1012) before normal shutdown; include a spike test confirming the ordering on the pinned uvicorn; switch the api entrypoint to it
    - _Requirements: 12.8, 26.5_
  - [x] 10.8 Write property test for graceful drain
    - **Property 31: Graceful shutdown drains every terminal**
    - **Validates: Requirements 12.8, 26.5**
  - [x] 10.9 Write terminal unit tests and the WebSocket failure-scenario tests
    - Fake_Provider: auth required; origin not on the allowlist refused; non-Active session refused identically to a missing one; ownership refusal indistinguishable from missing; connect-check order (authentication, origin, state, ownership); heartbeat close; no persistence; drain; ownership re-verified on reconnect; foreign refusal on connect and reconnect; ended-while-down status frame; CSRF middleware passes websocket scopes through
    - _Requirements: 12.12, 13.2, 26.1, 26.2, 26.4, 26.5, 26.6, 26.7_
  - [x] 10.10 Write Docker terminal tests
    - Attach a real TTY, resize applies rows and columns, typing records learner activity, sandbox stays alive with no WebSocket connected, files survive a reconnect
    - _Requirements: 12.3, 12.4, 12.6, 12.12, 13.1, 13.3_

- [x] 11. Milestone B: Web terminal and code editor
  - [x] 11.1 Add the web dependencies and the Monaco asset copy script
    - Add `@xterm/xterm`, its fit addon, Monaco and its loader at exact versions to `package.json` (already pinned in ADR 0001 by task 20.3, so the ADR-vs-manifest test passes); build script copies Monaco `min/vs` assets to `public/monaco` so no CDN request is made
    - _Requirements: 4.12, 4.13, 14.1_
  - [x] 11.2 Implement `lab-api.ts` and `lab-state.ts`
    - Pure client rules: `canCheckWork`, `timeRemaining`, `warningActive`, `reconnectDelays`, and a view-state reducer (ready, grading, results, error, reconnecting); uses API `server_now` to compute an offset so client clock skew does not matter
    - _Requirements: 13.4, 15.5, 15.6, 15.8_
  - [x] 11.3 Write fast-check tests for the pure client rules
    - **Property 18: Reconnect backoff schedule**
    - **Validates: Requirements 13.4**
    - **Property 19: Lab_View derived state**
    - **Validates: Requirements 15.5, 15.6, 15.8**
  - [x] 11.4 Implement `TerminalPane`
    - Client-only dynamic import of xterm.js and the fit addon, forwards resize, bounded-backoff reconnect with a visible indication, the terminal pane swallows the exit chord (Ctrl+M, Ctrl+Shift+M on macOS) so Enter is unaffected and focus moves to "Check my work"
    - _Requirements: 13.3, 13.4, 15.2, 15.10, 16.4, 26.1_
  - [x] 11.5 Implement `CodeEditor`
    - Self-hosted Monaco, one editor per learner file, keyboard access, the same documented exit chord (Ctrl+M toggles Tab focus mode in Monaco), no focus trap
    - _Requirements: 14.1, 14.8, 15.2, 15.9, 15.10, 16.4_
  - [x] 11.6 Write vitest component tests for terminal and editor panes
    - Exit chord moves focus out, Enter unaffected, reconnect indication shown while disconnected, editor reachable and escapable by keyboard
    - _Requirements: 14.8, 15.9, 15.10, 16.4_

- [x] 12. Milestone B: Code exercise harness
  - [x] 12.1 Implement the in-sandbox harness `run_cases.py`
    - Lives in the code-lab bundle's `sandbox/harness/`; runs as the Grader_User; written against the argv forms recorded by task 3.4
    - Learner-file intake: open `/workspace` once with `O_DIRECTORY | O_NOFOLLOW`, then for each declared flat file name `openat` with `O_RDONLY | O_NOFOLLOW | O_NONBLOCK` (a symlink fails with `ELOOP`, a FIFO never blocks), `fstat` the descriptor and accept only `S_ISREG` files within `LAB_FILE_MAX_BYTES`, read the bytes from that descriptor and write them into the grader-owned `/grader/run/learner/` (mode 0700); reject any symlink, FIFO, device node, socket, directory, missing, or oversized file by reporting `unsafe-learner-file`, executing nothing; only the copy is ever executed
    - Executes the learner program as a hardened subprocess in a new session with closed file descriptors, clean env, `RLIMIT_CPU`, `RLIMIT_AS` (128 MiB), `RLIMIT_NPROC` (32), sets itself non-dumpable (`PR_SET_DUMPABLE=0`), reads only `/grader` and the copies, and writes one JSON report with the learner output embedded as base64 data
    - _Requirements: 14.3, 14.4, 14.5, 14.10, 14.11, 29.7, 29.9_
  - [x] 12.2 Write Docker tests for the harness
    - Forged report lines in learner stdout are ignored; resource-limit kills map to `could_not_complete`; no test code, helper, or config is imported from the learner workspace; a declared learner file replaced by a symlink, a FIFO, or a device node is rejected, never blocks the harness, executes nothing, and the run is `could_not_complete`; the executed path is the grader-owned copy
    - _Requirements: 14.3, 14.4, 14.10, 14.11, 14.12, 28.6, 28.7, 29.7, 29.9_
  - [x] 12.3 Write code-exercise tests for file saves
    - Save and grading `copy_in` learner files into the workspace, over-cap requests return problem details and copy nothing, learner files are not persisted after the session is destroyed
    - _Requirements: 14.2, 14.6, 14.7, 14.9_
  - [x] 12.4 Write property test for unsafe learner files
    - **Property 36: Unsafe learner files are rejected and never executed**
    - Runs on Linux (CI runner, or inside the Python Lab_Image locally through the `docker` marker) because it needs `O_NOFOLLOW`, FIFOs, and symlinks: symlinks (to a file, a directory, a missing target), FIFOs, device nodes, sockets, directories, missing and oversized files are rejected with `unsafe-learner-file` and the runner is never invoked; for regular files within the cap the copy is byte-identical and the path handed to the runner is the copy
    - **Validates: Requirements 14.10, 14.11, 29.9**

- [x] 13. Checkpoint: Milestone B complete
  - Ensure all tests pass, ask the user if questions arise.

- [x] 14. Milestone C: Lab content schema, `content validate`, and sync
  - [x] 14.1 Extend the Pydantic v2 content schemas for labs
    - `Lab` (`kind`, `image`, `concept_stable_ids`, `instructions`, `setup_script`, `check_bundle`, `check_bundle_dest` default `/grader`, `learner_files`, `starter_files`), `LabTask` (`description`, `required`, `hints`), `LabCheck` (`timeout_seconds`, `script`, `outcomes` with a mandatory `pass` code, `feedback_key`, localized `message`), reusing `Localized` and `Provenance`
    - _Requirements: 20.1, 20.2_
  - [x] 14.2 Add lab rules to `edu-it content validate`
    - `lab-reference` (image under `lab-images/`, concepts, bundle and setup files exist), `lab-check-timeout` (a timeout is present), `check-timeouts-fit-budget` (each Check's timeout and the sum of a lab's Check timeouts are at most the Usable_Run_Budget computed from Config as deadline - freeze bound - reserve, initially 12 s; the per-Check failure names the Check `stable_id`, the sum failure names the lab `stable_id`), `lab-feedback-complete` (every outcome has a key with both translations, keys unique within the lab), `lab-bundle-location` (not under `/workspace`, `/home`, `/tmp`, `/var/tmp`, `/dev/shm`); existing bilingual and `ai-reviewer-present` rules cover labs; each failure names the `stable_id` and rule and exits non-zero
    - _Requirements: 18.4, 20.1, 20.2, 20.3, 20.4, 20.5, 20.6, 20.7, 20.8_
  - [x] 14.3 Write property test for lab content validation
    - **Property 22: Lab content validation reports each defect**
    - One fixture per rule (including `check-timeouts-fit-budget` for a single Check and for the sum) plus the all-valid exit-zero case; also covers strict and `--allow-pending` modes, a Seed_Lab with only the reviewer missing reporting exactly `ai-reviewer-present`, and the post-review strict state
    - **Validates: Requirements 18.4, 20.1, 20.2, 20.3, 20.4, 20.5, 20.6, 20.7, 20.8, 20.9, 20.13, 23.6, 23.8**
  - [x] 14.4 Extend `edu-it content sync` for labs
    - Idempotent upsert of labs, tasks, and Checks by `stable_id`, new lab columns filled by the SQL store, existing `lab_task_completion` kept on edits, removed labs archived (never deleted), progress tied to changed or removed items preserved as orphaned data; sync refuses to sync a lab with `origin` `ai` that has no reviewer recorded, whether or not it is in the Pending_Review_List
    - _Requirements: 20.10, 20.11, 23.7_
  - [x] 14.5 Write property test for lab content sync
    - **Property 23: Lab content sync is idempotent and preserves progress**
    - Includes the refusal to sync an unreviewed `ai` lab
    - **Validates: Requirements 20.10, 20.11, 23.7**
  - [x] 14.6 Extend the CI `content` step to validate labs in pending mode
    - The existing `content` step in `ci.yml` runs `edu-it content validate --allow-pending` over lab content and fails the pipeline naming the step on any error, so unreviewed Seed_Labs listed in the Pending_Review_List pass only for the missing reviewer and anything else fails
    - _Requirements: 20.12, 20.13_
  - [x] 14.7 Implement the Pending_Review_List and the `--allow-pending` mode
    - Loader for `content/linux-fundamentals/pending-review.json` (`{"stable_ids": []}` initially; the Seed_Lab ids are added when the labs are authored in 17.1 and 17.2); strict mode (default) applies every rule; `--allow-pending` runs every rule, then drops only `ai-reviewer-present` failures for labs whose `stable_id` is in the list; any other failure and any unreviewed lab not listed still fails
    - _Requirements: 20.9, 20.13, 23.6, 23.8_
  - [x] 14.8 Write tests for pending and strict validation modes
    - Fixture labs: a listed lab with only the reviewer missing (passes in `--allow-pending`, reports exactly `ai-reviewer-present` in strict mode); a listed lab with any other failure (fails); an unlisted unreviewed lab (fails); `content sync` refuses an unreviewed `ai` lab listed or not; a listed and reviewed lab validates strictly once the list is emptied
    - _Requirements: 20.9, 20.13, 20.14, 23.6, 23.7, 23.9_

- [x] 15. Milestone C: Mastery, gate, and GDPR wiring
  - [x] 15.1 Implement `Content_LabCatalog`
    - `LabCatalog` adapter over the content store exposing `LabDefinition` (tasks, `required`, checks, outcomes, bundle paths, localized text) and `labs_covering(concept_id)`
    - _Requirements: 19.3, 20.10_
  - [x] 15.2 Implement the real `Repository_LabCheck` adapter
    - `covers` is true when a non-archived lab lists the concept; `passed` is true when some covering lab has every required task completed for the user or a `lab_passed` marker exists
    - _Requirements: 19.3_
  - [x] 15.3 Write property test for the lab-check gate
    - **Property 13: The lab-check gate follows completed labs**
    - **Validates: Requirements 19.3**
  - [x] 15.4 Wire mastery application into `RecordCheckResult`
    - Persist mastery per Check result so dependent modules unlock mid-lab; counted completion plus one `lab` event per covered concept on first pass; later passes insert `counted_for_mastery=False` rows with no gain; failed and could-not-complete never lower mastery; `lab_passed` zero-delta marker when the last required task completes
    - _Requirements: 19.1, 19.2, 19.3, 19.4, 19.5, 19.6, 19.8, 27.6_
  - [x] 15.5 Write property test for lab mastery application
    - **Property 12: Lab results change mastery once, monotonically, and tempered by hints**
    - **Validates: Requirements 11.4, 19.1, 19.2, 19.4, 19.5, 19.6, 19.8, 27.6**
  - [x] 15.6 Write integration tests for progression
    - Module "Processes" unlocks after the prerequisite concepts of "Files & Permissions" reach the required state, including mid-lab; expiry keeps completions; progress endpoint returns lab completion and lab-derived mastery
    - _Requirements: 19.6, 19.7, 27.6_
  - [x] 15.7 Extend the GDPR application service for lab tables
    - Export includes `lab_session`, `lab_run`, and `lab_task_completion` rows of the learner and no terminal I/O; deletion removes the same rows atomically and destroys any Active_Session Sandbox; retention coverage per task 6.15
    - _Requirements: 24.1, 24.2, 24.3, 24.4_
  - [x] 15.8 Write property test and coverage test for GDPR
    - **Property 25: Lab data export and deletion are complete**
    - A test fails when a personal-data table introduced by this spec is missing from export, deletion, or retention coverage
    - **Validates: Requirements 24.1, 24.2, 24.5**

- [x] 16. Milestone C: Lab_View and accessibility
  - [x] 16.1 Add the `Labs` namespace to `messages/en.json` and `messages/fr.json`
    - Every label, status, hint, error, timer warning, reconnect, slow-provisioning, and session-ended string in both locales (the existing key-parity test covers both)
    - _Requirements: 15.11, 25.4, 26.4_
  - [x] 16.2 Implement the Lab_View page and components
    - `src/app/[locale]/labs/[labId]/page.tsx` + client `LabView`: instructions and tasks in the active locale, terminal or editor by lab kind, progressive hints, "Check my work" disabled with an explanation per the 10 s rule, confirmed "Reset lab" (completions intact), TTL timer with a warning below `LAB_WARNING_THRESHOLD`, SSE results shown incrementally, success state, slow-provisioning message at 20 s, session-ended message with an offer to start a new session, live regions (`role="status"`, polite) for results, timer warnings, and reconnect; visible focus indicator on every interactive element; remove `LabPlaceholder` and link lessons to the Lab_View when `lab_ids` is non-empty
    - _Requirements: 15.1, 15.2, 15.3, 15.4, 15.5, 15.6, 15.7, 15.8, 15.9, 15.10, 15.11, 15.12, 16.2, 16.3, 16.4, 18.1, 18.2, 18.5, 25.4, 26.4_
  - [x] 16.3 Write vitest component tests for Lab_View states
    - Ready, grading, results, error, reconnecting, slow provisioning; hint progression; button enable/disable rules; keyboard operation of every action; lesson link replaces the "coming soon" placeholder
    - _Requirements: 15.3, 15.4, 15.5, 15.6, 15.7, 15.9, 15.12, 25.4_
  - [x] 16.4 Extend the deterministic mock API `e2e/mock-api.mjs` for lab endpoints and states
    - Serve ready, grading, results, error, and reconnecting states for page-level accessibility tests only
    - _Requirements: 16.5, 32.3_
  - [x] 16.5 Write Playwright + axe-core tests for the Lab_View
    - No axe-core violations in `/fr` and `/en` in the ready, grading, results, error, and reconnecting states; focus is never trapped in the terminal or editor
    - _Requirements: 16.1, 16.4, 16.5_

- [x] 17. Milestone C: Seed labs (origin `ai`, FR and EN)
  - [x] 17.1 Author seed lab 1 "Fix the shared folder" (terminal, `concept.changing-permissions`)
    - `labs.json`, `lab_tasks.json`, `lab_checks.json`, and the bundle under `content/linux-fundamentals/lab-bundles/fix-the-shared-folder/` (`setup/`, `sandbox/`, `expected/`); idempotent setup creating `/workspace/team`, planting a world-writable `shared-notes.txt`, verifying `id -nG` includes `devs`; Checks `dirs-exist`, `group-setgid`, `no-world-writable`, `planted-intact` with the design's exit-code mapping, absolute `/usr/bin` tools (argv forms from the task 3.4 spike), no symlink following, and the reserved exit code 90 (`unsafe-path`, `could_not_complete`) when a symlink, FIFO, or device node is found where a directory or regular file is expected, tested with `stat` before anything is read; distinct FR/EN Feedback_Keys per failure reason; progressive hints; lesson reference via `lab_ids`; provenance `origin` `ai`
    - Add the real `stable_id` of this lab to `content/linux-fundamentals/pending-review.json`
    - _Requirements: 21.1, 21.2, 21.3, 21.4, 21.5, 21.6, 21.7, 21.8, 21.9, 15.12, 20.13, 29.9_
  - [x] 17.2 Author seed lab 2 "World-writable entries" (code, `concept.reading-permissions`)
    - Starter `solution.py` skeleton, bundle with the harness (task 12.1), expected cases covering regular files, directories, symlinks (never reported), sticky-bit entries (`drwxrwxrwt` reported; sticky without world-write not reported), the `total` header, names with spaces, empty input, at least one reported and one not-reported case per category; outcomes `pass`, `wrong-output`, `crashed`, `no-output` with distinct FR/EN keys that never include expected output; hints; lesson reference; provenance `origin` `ai`
    - Add the real `stable_id` of this lab to `content/linux-fundamentals/pending-review.json`
    - _Requirements: 22.1, 22.2, 22.3, 22.4, 22.5, 22.6, 22.7, 22.9, 15.12, 20.13_
  - [x] 17.3 Validate the seed labs in pending mode with `edu-it content validate --allow-pending`
    - Confirm the authored labs pass `--allow-pending` (exit zero) before review, and that strict `edu-it content validate` reports for each Seed_Lab exactly the `ai-reviewer-present` failure and no other; any other failure is a defect to fix before the review checkpoint; do not sync or serve them
    - _Requirements: 20.1, 20.7, 20.9, 20.13, 21.1, 22.1, 23.6_
  - [x] 17.4 Write property test for the seed program classification
    - **Property 28: The seed program classifies entries by the stated rule**
    - **Validates: Requirements 22.2, 22.4, 22.5**
  - [x] 17.5 Write seed lab manifest tests
    - Categories covered with reported and not-reported cases, one Check per requirement of lab 1, distinct Feedback_Keys, no expected output in any message, lab 1 solvable using only commands from the permissions lessons, both Seed_Lab `stable_id`s present in `pending-review.json`
    - _Requirements: 21.6, 21.7, 22.3, 22.9_
  - [x] 17.6 Write Docker reference and partial-solution tests for lab 1
    - Reference solution passes every Check; each partial solution fails the expected Check with the expected Feedback_Key; setup script run twice is idempotent
    - Bundles are loaded directly from repository files; nothing is synced or served
    - _Requirements: 21.2, 21.3, 21.4, 21.7, 21.9_
  - [x] 17.7 Write Docker reference and wrong-solution tests for lab 2
    - Reference passes every Check; wrong solutions (all `w` bits, symlinks reported, directories ignored, sticky directories ignored) each fail the expected Check with a distinct Feedback_Key; tests run in the Python Lab_Image
    - _Requirements: 22.7, 22.8, 22.9_
  - [x] 17.8 Write tamper tests for seed lab 1 against the Docker_Provider
    - One test per criterion 1 through 9 of Requirement 29 (modified/deleted check-named files, prepended `PATH`, aliases and the listed environment variables, fake `ls`/`stat`/`id`/`python3`/`find`, symlink swap, background process, forged report line, unreadable or writable fixture directory, and replacement of an inspected path with a symlink, FIFO, or device node); each compares Check results with an untampered run, except criterion 9 whose expected result is `could_not_complete` (reserved exit code 90), never a pass, and which never blocks on the FIFO
    - Docker tests are the tamper evidence; Fake_Provider tests are call logic only
    - _Requirements: 29.1, 29.2, 29.3, 29.4, 29.5, 29.6, 29.7, 29.8, 29.9, 29.10, 29.11_
  - [x] 17.9 Write tamper tests for seed lab 2 against the Docker_Provider
    - Same criteria 1 through 9, including a program printing a forged harness result line and a declared learner file `solution.py` replaced by a symlink, a FIFO, or a device node (expected `could_not_complete`, nothing executed from it), each other test compared with an untampered run
    - _Requirements: 14.12, 29.1, 29.2, 29.3, 29.4, 29.5, 29.6, 29.7, 29.8, 29.9, 29.10, 29.11_

- [x] 18. EXPERT REVIEW CHECKPOINT: STOP and ask the user to review the seed labs
  - STOP here and ask the user (use the `user_input` tool) to review both Seed_Labs ("Fix the shared folder" and "World-writable entries"), including the classification decisions that a symlink is never reported (Requirement 22.4) and that a world-writable sticky directory is reported while a sticky entry without world-write is not (Requirement 22.5). Do not sync, serve, record a reviewer, or add anything to the Golden_Set until the user responds. Tasks in waves 9 and later MUST NOT start before this task is resolved.
  - If the user does not approve: add no lab to the Golden_Set, record no reviewer, sync and serve nothing in any form, and halt the later content steps (19, 21).
  - _Requirements: 23.1, 23.4_

- [x] 19. Record the reviewer and add the approved labs to the Golden_Set (only after approval)
  - [x] 19.1 On approval, record the user as reviewer, empty the Pending_Review_List, and validate strictly
    - Record `reviewed_by`/`reviewed_at` provenance for each Seed_Lab using the learning-core identity convention, then run `content sync` (which now accepts them) and read back to confirm the recording succeeded; if recording fails, add nothing to the Golden_Set and report the failure
    - Empty `content/linux-fundamentals/pending-review.json` (remove both Seed_Labs) and run strict `edu-it content validate` (no `--allow-pending`), which must exit zero with no tolerated failure
    - _Requirements: 23.2, 23.5, 23.7, 23.8_
  - [x] 19.2 Add the approved Seed_Labs to the Golden_Set only after the confirmation in 19.1
    - Same golden-set mechanism as the learning-core Seed_Content, for Phase 4 evals; add no lab before the recording is confirmed
    - _Requirements: 23.3_
  - [x] 19.3 Write tests for the approval flow
    - Approval records the reviewer; Golden_Set addition happens only after the confirmed recording; a failed recording adds nothing; rejection records no reviewer, adds no golden entry, and syncs or serves nothing; the Seed_Labs before the reviewer fail strict validation with exactly `ai-reviewer-present` and sync refuses them, and after the reviewer is recorded and the list emptied they validate strictly and sync
    - _Requirements: 23.2, 23.3, 23.4, 23.5, 23.6, 23.7, 23.8, 23.9_

- [x] 20. Documentation and dependency pins
  - [x] 20.1 Write `docs/LABS.md`
    - Architecture, sandbox limits, grading isolation (including the freeze bound, teardown reserve, and 12 s Usable_Run_Budget), lab content format, how to run labs locally, how to add a lab, a prominent warning that the Docker provider is never for production, the documented keyboard shortcut that leaves the terminal and the editor (Ctrl+M, Ctrl+Shift+M on macOS), the procedure to bump a base-image digest (rebuild, rescan, update the pinned digest in the Dockerfiles, re-run `lab-images.yml`), a statement that no scan waiver mechanism exists, and every Config value introduced by this spec with its initial value
    - _Requirements: 4.9, 4.10, 15.10, 34.1, 34.2, 34.3, 34.4_
  - [x] 20.2 Write required-docs tests
    - Three independent assertions: `docs/LABS.md` contains the Docker-provider production warning, contains the terminal-exit shortcut, and contains the digest-bump procedure
    - **Property 33: Every lab Config value is documented**
    - **Validates: Requirements 4.9, 34.4, 34.5, 34.6**
  - [x] 20.3 Pin every new dependency in ADR 0001 (first wave, before any manifest change)
    - Exact versions for xterm.js and its fit addon, Monaco and its loader, the WebSocket server library, the Docker SDK for Python, the vulnerability scanner, and any other tool introduced by this spec, recorded before, or in the same change as, the change that adds each to a manifest; this must complete before 11.1, 2.5, and 3.2
    - _Requirements: 4.12, 4.13_
  - [x] 20.4 Write `tools/tests/test_labs_adr_pins.py` (ADR 0001 vs manifests)
    - Holds the list of dependencies introduced by this spec, reads `package.json`, the Python manifests, and the CI workflows, and fails when any is present at a version not exactly pinned in ADR 0001 or has no pin; it runs in CI from this task's wave on, so every later dependency addition is checked
    - _Requirements: 4.13, 4.14_

- [x] 21. End-to-end journey and performance (run against the real stack)
  - [x] 21.1 Write the Playwright end-to-end journey test
    - Sign up, enroll, pass the quiz, open the lab, run a command in the terminal, pass a Check, submit the project, and observe module "Processes" unlock; real Docker_Provider, worker, API, and web app (the mock API is used only for page-level accessibility tests)
    - _Requirements: 32.1, 32.3_
  - [x] 21.2 Write Playwright tests for both seed labs and refresh recovery
    - Pass both Seed_Labs and see translated feedback in `/fr` and `/en`; create a file, refresh, and find the file in the same Sandbox
    - _Requirements: 13.3, 13.6, 32.2, 32.3_
  - [x] 21.3 Implement the performance harness `tools/perf/lab_perf.py` and the pure protocol logic `tools/perf/protocol.py`
    - Nearest-rank percentile; measures start-to-ready, terminal keystroke round trip, and shell-Check run time; protocol: start on an otherwise idle machine and log a pre-run CPU and memory snapshot plus the machine setup (CPU, RAM, OS, Docker version); per target discard the first 3 warm-up samples, then collect at least 30 counted start-to-ready samples and at least 200 counted keystroke samples (at least 33 and 203 raw); compute the p95 and compare with the target; if any target is missed perform at most one full re-measurement; write every attempt (including the failed one) with its sample counts and values to the verification output; the final attempt is the one that counts
    - Records sample count, method, values, and machine setup to a results file consumed by the phase report
    - _Requirements: 31.4, 31.6, 31.7, 31.8_
  - [x] 21.4 Write property test for the nearest-rank percentile
    - **Property 32: Nearest-rank percentile**
    - **Validates: Requirements 31.4, 31.6**
  - [x] 21.5 Run the performance measurements
    - Run the protocol of 21.3: 3 warm-up samples discarded, at least 30 counted start-to-ready samples (p95 under 20 s), at least 200 counted keystroke round trips (p95 under 150 ms), every shell Check run under 15 s, on an otherwise idle machine; at most one logged full re-measurement and every attempt logged; the final measurement counts; if the final measurement misses a target, record the failure so task 24 halts and writes no report
    - _Requirements: 31.1, 31.2, 31.3, 31.4, 31.5, 31.6, 31.7, 31.8_

- [x] 22. Traceability suite and verification runner
  - [x] 22.1 Write `tools/tests/test_labs_verification_suite.py` with a `RULE_MAP`
    - Follow the phase-2 `RULE_MAP` pattern: every rule in `.kiro/steering/labs.md` (sandbox port, terminal, runtime and topology, grading bounds, the provisioning, WebSocket drop, and grading-timeout failure scenarios), the Labs section of `.kiro/steering/domain-rules.md`, and `docs/LABS-DESIGN.md` §4 maps to requirement ids and `(test file, needle)` pairs; every security and grading-integrity rule (non-root, no network, four limits, capabilities, no learner code in api/worker, grading isolation, fail-closed freeze, tamper resistance, throttles, dev-only Docker) maps to its automated test from tasks 2 to 21; re-map the phase-2 entry "Grading is bounded" to Requirements 7.4, 11, and 28
    - Test documentation (module docstring and `RULE_MAP` header) states the evidence classes: Fake_Provider tests verify call logic only and are not security evidence; tamper resistance is evidenced by the Docker_Provider tests, and security and grading-integrity rules map to the Docker tests (Requirements 10.12, 29.11)
    - The test fails when a rule has no mapped requirement or test, or the mapping names a test or needle that does not exist
    - _Requirements: 10.12, 29.11, 33.1, 33.2_
  - [x] 22.2 Write negative tests for the traceability suite
    - Fixtures with an unmapped rule, a missing test file, and a missing needle each make the suite fail; a test asserts the evidence-class statement is present in the suite documentation
    - _Requirements: 10.12, 29.11, 33.2_
  - [x] 22.3 Extend `tools/run_verification.py` and add the phase-report writer
    - Add the labs commands (unit, property, docker-marked, web, Playwright, axe-core, content validate in strict and pending modes, ADR pin test, traceability, performance, image build and scan); the writer consumes the performance attempt log, records per acceptance check the command and real outcome, includes the performance measurements of the final attempt (sample count, nearest-rank method, values, machine setup), the Requirement 33 mapping with the outcome of each test group, and the Requirement 23 review outcome, logs every performance attempt, and refuses to write `docs/reports/phase-3.md` when any check failed or could not be run or the final performance attempt missed a target
    - _Requirements: 31.4, 31.5, 31.8, 33.3, 35.1, 35.2, 35.3, 35.4_
  - [x] 22.4 Write tests for the report writer
    - Writes nothing when any check fails or is unrun; writes a report listing command and pass outcome per check when all pass
    - _Requirements: 35.1, 35.3_
  - [x] 22.5 Write property test for the performance measurement protocol
    - **Property 38: Performance measurement protocol**
    - Over sequences of attempts with raw samples: the first 3 samples are discarded, an attempt is valid only with at least 30 counted start-to-ready and 200 counted keystroke samples, at most one re-measurement follows a miss, every attempt appears in the log, the decision uses only the final attempt, and the report writer produces a report iff the final attempt meets every target
    - **Validates: Requirements 31.4, 31.5, 31.6, 31.7, 31.8, 35.3, 35.4**

- [x] 23. Checkpoint: Final verification before the report
  - Ensure all tests pass, ask the user if questions arise.

- [x] 24. Final task: write the phase-3 report
  - Run every acceptance check through `tools/run_verification.py`. Only if every check ran and passed, write `docs/reports/phase-3.md` recording for each acceptance check the command invoked and its real pass outcome, plus the Requirement 31 performance measurements of the final attempt (sample count, nearest-rank method, values, machine setup, with every attempt logged), the Requirement 33 rule-to-test mapping with each test group's outcome, and the Requirement 23 review outcome (reviewer recorded and golden-set confirmation).
  - If any acceptance check fails or cannot be run, HALT, report the failing or unrun checks to the user, and produce no `docs/reports/phase-3.md`.
  - _Requirements: 31.4, 31.5, 31.8, 33.3, 35.1, 35.2, 35.3, 35.4_

## Notes

- Test sub-tasks are not postfixed with `*`, matching the learning-core plan: Requirements 33 and 35 make every mapped test an acceptance check, so none may be skipped.
- Each task references specific requirements for traceability (using the revised requirement numbering); property tasks also name their Property number (1 to 38) from design.md.
- Property-based tests use Hypothesis (Python) or fast-check (web), at least 100 iterations, read numbers from `domain/config.py`, use `Test_Clock` and `Fake_SandboxProvider`, and are tagged `Feature: hands-on-labs, Property {n}: {property text}`.
- Evidence classes: Fake_Provider tests (including Property 26) verify call logic only and are not security evidence. Tamper resistance and grading isolation are evidenced by the `docker`-marked tests (Requirements 10.12, 29.11). Tests that need a real container carry the `docker` marker and run in CI and locally when a Docker daemon is available. Tests that load seed lab bundles do so from repository files and never sync or serve the labs.
- Ordering constraints encoded in the graph: ADR 0001 pinning (20.3) is in wave 0 and the ADR-vs-manifest test (20.4) in wave 1, ahead of the web dependencies (11.1), the Docker SDK use (2.5), and the scanner use (3.2); the flag-compatibility spike (3.4, wave 1) precedes 2.5, 6.8, 6.10, 6.13, 12.1, 17.1, and 17.2.
- Grading budget: run deadline 15 s = freeze bound 1 s + Usable_Run_Budget 12 s + teardown reserve 2 s, measured from `accepted_at`; queue delay is deducted; the freeze is verified fail-closed before any copy-in or Check; the learner is always thawed in a `finally`.
- Content validation modes: CI runs `edu-it content validate --allow-pending` (tolerating only `ai-reviewer-present` for labs in `content/linux-fundamentals/pending-review.json`); local default is strict. Task 19.1 empties the list and runs strict validation after the reviewer is recorded; `content sync` always refuses unreviewed `ai` labs.
- The EXPERT REVIEW CHECKPOINT (task 18) STOPS execution and waits for the user. Waves 9 and later contain only tasks that depend on approval (19, 21.1, 21.2, 21.5, 22 outputs); do not start them until task 18 is resolved. If the user rejects the labs, record no reviewer, add nothing to the Golden_Set, and sync and serve nothing.
- Checkpoint tasks (4, 9, 13, 23), the review task (18), and the final report task (24) are outside the dependency graph.
- The Docker provider is for development only; production composition refuses it.
- The last task writes `docs/reports/phase-3.md` only when every acceptance check ran and passed (Requirement 35).
- The workflow produces planning artifacts only; begin execution from the task list once reviewed.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2", "3.1", "5.1", "14.1", "16.1", "20.3"] },
    { "id": 1, "tasks": ["1.3", "1.13", "3.2", "3.4", "11.1", "14.2", "20.4", "21.3"] },
    { "id": 2, "tasks": ["1.4", "1.5", "1.6", "1.7", "1.14", "2.3", "3.3", "3.5", "11.2", "14.4", "14.7", "21.4"] },
    { "id": 3, "tasks": ["1.8", "1.9", "1.10", "1.11", "1.12", "1.15", "2.1", "2.4", "2.5", "11.3", "11.4", "11.5", "12.1", "14.3", "14.5", "14.6", "14.8", "15.1", "16.4", "17.1"] },
    { "id": 4, "tasks": ["2.2", "2.6", "2.7", "5.2", "6.1", "6.3", "6.8", "6.10", "6.13", "6.15", "8.1", "10.1", "11.6", "12.2", "12.4", "16.2", "17.2", "20.1"] },
    { "id": 5, "tasks": ["2.8", "2.9", "5.3", "6.2", "6.4", "6.5", "6.6", "6.7", "6.9", "6.11", "6.12", "6.14", "6.16", "6.19", "7.1", "8.2", "8.3", "8.4", "8.5", "8.7", "8.8", "8.9", "8.10", "8.11", "10.2", "10.3", "10.4", "10.5", "10.8", "12.3", "15.2", "15.4", "15.7", "16.3", "16.5", "17.3", "17.4", "17.5", "17.6", "17.7", "17.8", "17.9", "20.2"] },
    { "id": 6, "tasks": ["2.10", "6.17", "7.2", "7.3", "15.3", "15.5", "15.6", "15.8"] },
    { "id": 7, "tasks": ["6.18", "7.4", "7.5", "7.7", "10.6"] },
    { "id": 8, "tasks": ["7.6", "7.8", "7.9", "8.6", "10.7", "10.9", "10.10"] },
    { "id": 9, "tasks": ["19.1"] },
    { "id": 10, "tasks": ["19.2", "21.1", "21.2", "21.5"] },
    { "id": 11, "tasks": ["19.3", "22.3"] },
    { "id": 12, "tasks": ["22.1", "22.4", "22.5"] },
    { "id": 13, "tasks": ["22.2"] }
  ]
}
```
