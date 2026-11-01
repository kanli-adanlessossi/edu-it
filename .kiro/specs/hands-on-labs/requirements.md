# Requirements Document

## Introduction

This spec builds the **hands-on labs** of EDU-IT, running locally: sandboxed terminals, graded code
exercises, and two seed labs, wired into progression and mastery. It follows the learning-core phase
(`docs/reports/phase-2.md`), where labs were surfaced only as "coming soon". It inherits every
authoritative rule from steering: labs, sandbox, and terminal (#[[file:.kiro/steering/labs.md]]),
progression, mastery, and hints (#[[file:.kiro/steering/domain-rules.md]]), content pipeline and
localization (#[[file:.kiro/steering/content.md]]), quality and testing bars
(#[[file:.kiro/steering/quality.md]]), API conventions
(#[[file:.kiro/steering/api-conventions.md]]), authentication and privacy
(#[[file:.kiro/steering/auth-privacy.md]]), layering (#[[file:.kiro/steering/structure.md]]), and
process (#[[file:.kiro/steering/working-agreements.md]]). The `SandboxProvider` signatures and the
lab failure behavior are in `docs/LABS-DESIGN.md` (D4); the lab tables are in `docs/DATA-MODEL.md`
(D3); ADRs A1 (versions), A3 (task queue), and A4 (session storage) apply.

**Threat model.** Learner code is untrusted and the learner controls the sandbox, including its
shell, environment, files, and processes. Every security and grading-integrity rule in
#[[file:.kiro/steering/labs.md]] is therefore a requirement here, and every such requirement is
covered by at least one automated test (Requirement 33).

Steering is the single source of truth. This document does not restate rule numbers as new rules; it
references steering and ties each requirement to the authoritative value. Business numbers
(timeouts, limits, intervals, rate limits) live in `domain/config.py`
(#[[file:.kiro/steering/quality.md]]). Numbers that steering and D4 fix (15 s grading target, one
grading per 10 s per session, 2 provisioning retries, one provisioning attempt per 10 s per session)
are used unchanged. Numbers marked "initial Config value" below are proposals for the user to confirm
at review; they are not fixed by steering.

The work spans three milestones:

- **Milestone A: Sandbox runtime.** `SandboxProvider` with Docker (dev only) and fake providers, lab
  images, worker jobs (provisioning, grading, reaper, retention), session limits, and grading
  isolation.
- **Milestone B: Terminal and code.** Authenticated terminal WebSocket between xterm.js and the
  sandbox with reconnect, and Monaco-based code exercises that run tests inside the sandbox.
- **Milestone C: Lab experience and content.** Lab view, translated per-check feedback, two seed labs
  (origin `ai`, FR and EN), the expert review checkpoint, `content validate` for labs, progression
  and mastery wiring, GDPR coverage, and `docs/LABS.md`.

**Out of scope:** the Kubernetes provider (the port stays implementable by it) and AI runtime
features.

## Glossary

- **Sandbox**: The isolated per-session execution environment created through the SandboxProvider,
  one per Lab_Session, in which all learner code and commands run.
- **SandboxProvider**: The domain port with `provision`, `exec`, `attach_terminal`, `copy_in`,
  `copy_out`, `destroy`, and `health` (`docs/LABS-DESIGN.md` D4), implemented by Docker_Provider
  (dev only) and Fake_Provider (tests), with a warm-pool extension point.
- **Docker_Provider**: The SandboxProvider implementation backed by local Docker. Dev only.
- **Fake_Provider**: The in-memory SandboxProvider implementation used by tests.
- **Lab_Image**: A container image defined under `lab-images/` (Linux base and Python) from which a
  Sandbox is created.
- **Lab_Session**: A running lab instance for one learner (`lab_session`: `started_at`, `ttl_at`,
  `status`), owning exactly one Sandbox.
- **Active_Session**: A Lab_Session whose status is provisioning, ready, or grading.
- **Ready_For_Grading**: The condition of a Lab_Session that has status ready, is owned by the
  requester, was not received strictly after `ttl_at` (a request received at exactly `ttl_at` meets
  this part of the condition; one received strictly after `ttl_at` does not), and has had no
  accepted grading request within the last 10 s (a request at exactly 10 s does not meet the
  condition; one at more than 10 s does).
- **Session_Service**: The `apps/api` component that creates, looks up, resets, and ends
  Lab_Sessions and enforces session limits.
- **Provisioning_Job**: The `apps/worker` job that provisions a Sandbox for a Lab_Session.
- **Grading_Job**: The `apps/worker` job that grades a Lab_Session.
- **Grading_Run**: One execution of a Grading_Job (`lab_run`: `status`, `graded_at`).
- **Usable_Run_Budget**: The run deadline minus the freeze verification bound minus the teardown
  reserve, all three read from Config. The run deadline is 15 s (fixed by
  #[[file:.kiro/steering/labs.md]]). Initial Config value of the freeze verification bound: 1 s
  (covering the 5 attempts of Requirement 10.7). Initial Config value of the teardown reserve: 2 s.
  The initial Usable_Run_Budget is therefore 15 - 1 - 2 = 12 s.
- **Pending_Review_List**: A list committed in the repository that names, by `stable_id`, the
  Seed_Labs awaiting the expert review of Requirement 23.
- **Check**: One graded assertion of a lab task (`lab_check`) with its own timeout.
- **Check_Bundle**: The check scripts and fixtures of a lab, stored in the repository as a read-only
  source and never inside a Sandbox before grading time.
- **Grader_User**: The unprivileged user, distinct from the learner user, as which Checks run.
- **Reaper**: The `apps/worker` job that destroys idle, expired, and orphaned Sandboxes.
- **Retention_Job**: The `apps/worker` job that deletes lab data older than its Config retention.
- **Terminal_Gateway**: The `apps/api` authenticated WebSocket endpoint that attaches the xterm.js
  terminal to a Sandbox TTY.
- **Lab_View**: The `apps/web` page presenting instructions, the terminal or editor, hints, "Check
  my work", "Reset lab", and the timer.
- **Code_Exercise**: A lab whose learner work is source files edited in Monaco and tested inside the
  Sandbox.
- **Feedback_Key**: A stable identifier of a translated, learner-facing message for a Check outcome.
- **Seed_Labs**: The two labs authored in Milestone C: the terminal lab "Fix the shared folder" and
  the code lab "World-writable entries".
- **Golden_Set**: The approved seed content marked as the reference set for Phase 4 evals.
- **Config**: `domain/config.py`, the single framework-free home for business numbers
  (#[[file:.kiro/steering/quality.md]]).
- **Content_Validator**: The `edu-it content validate` command.
- **Content_Sync**: The `edu-it content sync` command.
- **Domain_Engine**: The framework-free domain layer that computes mastery and unlocking, reading
  time only through the injected `Clock`.
- **Learning_API**: The `apps/api` FastAPI boundary.
- **Web_App**: The `apps/web` Next.js UI with `/fr` and `/en` routes.
- **Idle_Timeout**: 15 minutes without learner activity in a Lab_Session. Activity means terminal
  input, editor file saves, or a grading request; Sandbox output is not activity.
- **Session_TTL**: The maximum lifetime of a Lab_Session from `started_at`. Initial Config value:
  60 minutes.

## Requirements

### Requirement 1: Sandbox port, providers, and layering

**User Story:** As a platform engineer, I want all sandbox technology behind the SandboxProvider
port with a fake, so that labs logic is testable without Docker and a production provider can be
added later.

#### Acceptance Criteria

1. THE Domain_Engine SHALL define the SandboxProvider port with the signatures in
   `docs/LABS-DESIGN.md` (D4) and import no Docker, web, or queue framework
   (#[[file:.kiro/steering/structure.md]]).
2. THE Docker_Provider and THE Fake_Provider SHALL each implement every SandboxProvider operation.
3. THE Fake_Provider SHALL execute no process on the host and reach no external system.
4. THE test suite SHALL run one shared provider contract test suite against both THE Docker_Provider
   and THE Fake_Provider.
5. THE SandboxProvider port SHALL define a warm-pool extension point, and THE system SHALL implement
   no warm pool in this spec.
6. THE Domain_Engine SHALL read every lab timeout, limit, interval, and rate limit from Config.
7. WHEN lab code requires the current instant, THE system SHALL obtain it from the injected Clock.
8. IF a required lab Config value is missing or not positive, THEN THE system SHALL refuse to start,
   name the offending Config key in the error, and create no Sandbox.
9. IF the Docker daemon is unreachable, THEN THE Docker_Provider SHALL return a provider-unavailable
   error within a Config-defined operation timeout and leave all Lab_Session and Sandbox state
   unchanged. Initial Config value: 10 s.
10. THE test suite SHALL verify criteria 8, 9, and 11 with the Fake_Provider, and criterion 9
    additionally against a Docker_Provider configured with an unreachable daemon.
11. IF the Config freeze verification bound plus the Config teardown reserve is greater than or equal
    to the Config run deadline, THEN THE system SHALL refuse to start, name the offending Config keys
    (the freeze verification bound, the teardown reserve, and the run deadline) in the error, and
    create no Sandbox.

### Requirement 2: Docker provider is dev only

**User Story:** As a platform owner, I want the Docker provider unable to run in production, so that
a sandbox with weaker isolation than the production design never serves real learners.

#### Acceptance Criteria

1. WHEN THE Docker_Provider starts, THE Docker_Provider SHALL log a warning that it is for
   development only and that plain `runc` isolation is not production-grade.
2. IF THE Docker_Provider is selected AND the configured environment is production, THEN THE system
   SHALL refuse to start and report a configuration error.
3. THE Docker_Provider SHALL NOT mount the Docker socket, host paths, or host devices into any
   Sandbox.

### Requirement 3: Sandbox isolation and resource limits

**User Story:** As a platform owner, I want every Sandbox locked down, so that hostile learner code
cannot harm the host, other learners, or the network.

#### Acceptance Criteria

1. THE Sandbox SHALL run every process, including the terminal shell and Checks, as a non-root user.
2. THE Sandbox SHALL have no network interface other than loopback.
3. THE Sandbox SHALL enforce exactly four resource limits, each read from Config: a CPU limit, a
   memory limit, a process-count (PID) limit, and a writable-disk (storage quota) limit. Initial
   Config values: 1 CPU, 512 MiB memory, 128 PIDs, 256 MiB writable-disk quota.
4. THE Sandbox SHALL drop all Linux capabilities and set no-new-privileges.
5. THE Sandbox SHALL run unprivileged, without access to host namespaces.
6. IF a Sandbox process exceeds the memory or PID limit, THEN THE Sandbox SHALL terminate or refuse
   that process without affecting the host or any other Sandbox.
7. WHEN a learner process in a Sandbox attempts an outbound connection to any non-loopback address,
   THE Sandbox SHALL fail the connection.
8. IF a Sandbox process writes beyond the writable-disk quota, THEN THE Sandbox SHALL fail the write
   without affecting the host or any other Sandbox.
9. WHILE a Sandbox process spins on the CPU, THE Sandbox SHALL limit its CPU use to the CPU limit.
10. THE test suite SHALL verify criteria 1 through 9 against a running Docker_Provider Sandbox,
    including a fork bomb (PID limit), a memory allocation loop (memory limit), a disk-fill loop
    (writable-disk quota), a CPU spin (CPU limit), and an outbound connection attempt.

### Requirement 4: Lab images

**User Story:** As a platform engineer, I want reproducible, minimal, scanned lab images, so that
the sandbox base is trustworthy.

#### Acceptance Criteria

1. THE repository SHALL define Lab_Images under `lab-images/` for a Linux base image and a Python
   image built on it.
2. THE Lab_Image definitions SHALL pin every base image by immutable digest.
3. THE Lab_Image definitions SHALL set a non-root default user.
4. THE Lab_Images SHALL contain no secrets, no Check_Bundle, and no solution files.
5. THE CI pipeline SHALL build every Lab_Image on each change under `lab-images/`.
6. THE CI pipeline SHALL scan every built Lab_Image for known vulnerabilities AND fail WHEN the scan
   reports any vulnerability, regardless of severity or fixability.
7. THE CI pipeline SHALL run the image scan job on each change under `lab-images/` and on a weekly
   schedule, and SHALL NOT run the image scan job as a gate on changes outside `lab-images/`.
8. WHEN a scheduled image scan reports a vulnerability, THE CI pipeline SHALL report it as a failed
   CI run.
9. THE `docs/LABS.md` SHALL document the procedure to bump a base-image digest.
10. THE CI pipeline SHALL provide no waiver, allowlist, or ignore mechanism for image scan findings.
11. THE test suite SHALL verify criteria 2 through 4 by inspecting the Lab_Image definitions and a
    built image.
12. WHERE a new framework or tool is introduced by this spec (xterm.js, Monaco editor, a WebSocket
    client or server library, a vulnerability scanner), THE repository SHALL pin its exact version in
    ADR 0001 and its manifest.
13. THE repository SHALL pin the exact version of each dependency introduced by this spec in ADR 0001
    before, or in the same change as, the change that adds the dependency to a manifest.
14. THE test suite SHALL fail WHEN a manifest dependency introduced by this spec has no pinned
    version in ADR 0001.
15. THE Lab_Images SHALL provide every shell command that the harness, setup scripts, or Checks use
    inside a Sandbox (`ps`, `kill`, `timeout`, `tar`), supporting every flag used.
16. THE test suite SHALL verify against the built Lab_Images that each command in criterion 15 exists
    and accepts the flags used.

### Requirement 5: Learner code never runs in the API or worker process

**User Story:** As a platform owner, I want learner-controlled code to execute only inside Sandboxes,
so that a compromised lab cannot compromise the platform.

#### Acceptance Criteria

1. THE Learning_API and THE worker SHALL execute learner-supplied code and commands only through
   `SandboxProvider.exec` or `SandboxProvider.attach_terminal`.
2. THE Learning_API and THE worker SHALL NOT import, evaluate, compile, or spawn a subprocess from
   learner-supplied content outside the Docker_Provider adapter's call to the container runtime.
3. THE Learning_API and THE worker SHALL treat learner-written files, command output, and exit codes
   as data only.
4. THE test suite SHALL include an import-and-call analysis over `apps/api` and `apps/worker` that
   fails WHEN a dynamic-execution or subprocess call appears outside the allowed adapter.
5. THE test suite SHALL submit a canary payload that, if executed in the API or worker process,
   would write a marker file on the host AND SHALL verify that the marker is absent after a full
   session and grading flow against both providers.

### Requirement 6: Session lifecycle and provisioning job

**User Story:** As a learner, I want to start a lab and get a ready sandbox, so that I can work
immediately.

#### Acceptance Criteria

1. WHEN a learner who has no Active_Session starts a lab, THE Session_Service SHALL create a
   Lab_Session with status provisioning, set `ttl_at` to `started_at` plus the Session_TTL, and enqueue a Provisioning_Job
   through the `TaskQueue` with a dedupe key per Lab_Session.
2. WHEN THE Provisioning_Job succeeds, THE worker SHALL set the Lab_Session status to ready and
   record the Sandbox handle.
3. THE Provisioning_Job SHALL run the lab's setup script inside the Sandbox before the Lab_Session
   becomes ready.
4. THE Learning_API SHALL expose the Lab_Session status, remaining time, and idle remaining time to
   its owner.
5. WHEN a Lab_Session ends, THE worker SHALL set its terminal status (ended, expired, idle-expired,
   or failed) and call `destroy` on its Sandbox.
6. WHEN THE Provisioning_Job is delivered twice for one Lab_Session, THE worker SHALL provision at
   most one Sandbox for it.

### Requirement 7: Session limits

**User Story:** As a platform owner, I want strict session limits, so that no learner can hold more
than one sandbox or exhaust capacity.

#### Acceptance Criteria

1. THE Session_Service SHALL permit at most one Active_Session per learner.
2. WHEN a Lab_Session has had no learner activity for the Idle_Timeout of 15 minutes, THE Reaper
   SHALL destroy its Sandbox and set status idle-expired.
3. WHEN the Clock instant is strictly after the `ttl_at` of a Lab_Session, THE Reaper SHALL destroy
   its Sandbox and set status expired, subject to Requirement 27.
4. IF a grading request for a Lab_Session is received 10 s or less after the previous accepted
   grading request, THEN THE Session_Service SHALL reject it; a request received more than 10 s
   after the previous accepted request is eligible for acceptance.
5. THE Session_Service SHALL rate-limit provisioning requests per learner. Initial Config value: 5
   requests per 10 minutes.
6. THE Session_Service SHALL rate-limit provisioning per Lab_Session to at most 1 attempt per 10 s
   (`docs/LABS-DESIGN.md` D4 §4.1).
7. IF a learner who already has an Active_Session requests a new provisioning, THEN THE
   Session_Service SHALL reject the new request and leave the existing Active_Session active.
8. WHEN a grading request is received at exactly `ttl_at`, THE Session_Service SHALL accept it,
   provided the other conditions of Ready_For_Grading hold; THE Session_Service SHALL reject only
   grading requests received strictly after `ttl_at` (Requirement 27).
9. THE test suite SHALL verify criteria 1 through 8 with the Fake_Provider and the fake Clock, and
   criteria 2 and 3 additionally against the Docker_Provider, including a grading request at
   exactly 10 s (rejected) and at just over 10 s (eligible), and a grading request at exactly
   `ttl_at` (accepted) and just after `ttl_at` (rejected).

### Requirement 8: Reaper

**User Story:** As a platform owner, I want a reaper that cleans up every abandoned sandbox, so that
no resources leak.

#### Acceptance Criteria

1. THE Reaper SHALL run periodically at an interval read from Config. Initial Config value: 30 s.
2. WHEN a Sandbox belongs to a Lab_Session that is idle-expired, expired, ended, or failed, THE
   Reaper SHALL destroy the Sandbox.
3. WHEN a Sandbox exists with no owning Lab_Session record (orphan), THE Reaper SHALL destroy the
   Sandbox.
4. WHEN THE Reaper destroys a Sandbox, THE Reaper SHALL be idempotent so that destroying an
   already-destroyed Sandbox succeeds.
5. IF `destroy` fails for a Sandbox, THEN THE Reaper SHALL retry on its next run AND log the failure.
6. THE test suite SHALL verify that, after the Idle_Timeout and after the TTL elapse on the fake
   Clock, no Sandbox remains for the Lab_Session in the Fake_Provider, and that a real Docker
   Sandbox is gone from the container runtime.

### Requirement 9: Retention job

**User Story:** As a privacy officer, I want old lab data deleted automatically, so that retention
covers lab sessions and grading results.

#### Acceptance Criteria

1. THE Retention_Job SHALL delete `lab_session`, `lab_run`, and `lab_task_completion` rows older than
   the lab-data retention period read from Config
   (#[[file:.kiro/steering/auth-privacy.md]], `docs/DATA-MODEL.md` D3 §13). Initial Config value: 90
   days. This value is a proposal for the user to confirm; steering requires retention but sets no
   period.
2. THE Retention_Job SHALL delete a Lab_Session's data only after the Lab_Session has ended.
3. WHEN THE Retention_Job runs twice over the same data and Clock instant, THE Retention_Job SHALL
   make no changes on the second run.
4. THE Retention_Job SHALL NOT delete mastery records derived from lab results, which retain their
   own retention.

### Requirement 10: Grading isolation

**User Story:** As a platform owner, I want Checks to run isolated from anything the learner can
touch, so that a learner cannot make a failing lab pass.

#### Acceptance Criteria

1. WHEN grading starts, THE Grading_Job SHALL copy the Check_Bundle into the Sandbox at grading time
   through `copy_in` from a read-only source, into a directory owned by root and not writable by the
   learner user.
2. THE Grading_Job SHALL NOT read test code, Check scripts, or expected values from any path writable
   by the learner user.
3. THE Grading_Job SHALL run every Check as the Grader_User, which is distinct from the learner user.
4. THE Grading_Job SHALL run every Check with a clean environment containing only variables defined
   by the Check_Bundle and Config, inheriting nothing from the learner's shell, and with an explicit
   working directory.
5. THE Grading_Job SHALL invoke every Check script and every system binary by absolute path from
   root-owned directories, and SHALL set `PATH` to fixed root-owned directories.
6. WHEN grading completes, THE Grading_Job SHALL remove the copied Check_Bundle from the Sandbox.
7. WHILE a Check runs, THE Grading_Job SHALL have stopped all processes owned by the learner user,
   verified before the first Check starts by re-reading the Sandbox process list for a bounded number
   of attempts read from Config, all within the freeze verification bound read from Config. Initial
   Config values: 5 attempts within a freeze verification bound of 1 s. The freeze verification bound
   is reserved out of the 15 s run deadline and is excluded from the Usable_Run_Budget.
8. IF the learner processes cannot be verified as stopped within the bound in criterion 7, THEN THE
   Grading_Job SHALL run no Check, record the Grading_Run as could-not-complete, grant no mastery
   gain, and resume the learner processes afterwards; THE Grading_Job SHALL resume the learner
   processes after every Grading_Run in every outcome, including failure and error.
9. THE Grading_Job SHALL derive each Check outcome from the Check's own result channel and exit
   status, and the learner user SHALL have no write access to that channel.
10. THE test suite SHALL verify criteria 1 through 9 against the Docker_Provider and, for criteria 1,
    2, and 6, against the Fake_Provider.
11. THE test suite SHALL include a freeze-failure test in which a learner process cannot be verified
    as stopped, and SHALL assert that no Check runs, the Grading_Run is could-not-complete, no
    mastery is granted, and the learner processes are resumed.
12. THE test documentation SHALL state that tamper resistance is evidenced by the Docker_Provider
    tests; Fake_Provider tests verify call logic only and are not security evidence.

### Requirement 11: Grading runs, per-check timeouts, and bounds

**User Story:** As a learner, I want my work checked quickly with bounded effort, so that feedback is
fast and the system cannot be abused.

#### Acceptance Criteria

1. THE Session_Service SHALL create a Grading_Run if and only if a grading request arrives AND the
   Lab_Session is ready for grading, and SHALL start no Grading_Run for any other reason. The new
   Grading_Run has status running and THE Session_Service SHALL enqueue a Grading_Job for it.
2. THE Grading_Job SHALL apply a per-Check timeout read from Config to every Check, and the per-Check
   timeout SHALL NOT exceed the Usable_Run_Budget. Initial Config value: 5 s.
3. THE Grading_Job SHALL complete every Grading_Run within the 15 s grading target
   (#[[file:.kiro/steering/labs.md]]) by bounding the sum of Check executions by the Usable_Run_Budget
   (run deadline of 15 s minus the freeze verification bound minus the teardown reserve; initially
   15 - 1 - 2 = 12 s), reserving the freeze verification bound for the process-freeze verification of
   Requirement 10.7 and the teardown reserve for teardown.
4. WHEN a Grading_Run finishes, THE Grading_Job SHALL record the Grading_Run with its status
   (passed, failed, or could-not-complete) and `graded_at`, and per-task pass or fail in
   `lab_task_completion`.
5. IF a grading request arrives for a Lab_Session 10 s or less after the previous grading request
   was accepted, THEN THE Session_Service SHALL reject it per Requirement 30.4.
6. THE Grading_Job SHALL deliver each Check result to the Learning_API as soon as the Check finishes
   so the Lab_View can display it incrementally (Requirement 18).
7. THE test suite SHALL verify criteria 2 through 4 with the Fake_Provider and a slow-check fixture,
   and criterion 3 against the Docker_Provider.
8. THE Grading_Job SHALL measure the run deadline from the instant the grading request was accepted
   (read from the injected Clock and recorded on the Grading_Run), so that queueing time before the
   Grading_Job starts is deducted from the available budget. THE Grading_Job SHALL compute its
   remaining budget at start as the Usable_Run_Budget minus the time elapsed since the accepted
   instant, SHALL NOT start a Check when the remaining budget is zero or less, and SHALL record each
   Check not reached as could-not-complete (Requirement 28.5).
9. THE test suite SHALL verify criterion 8 with the Fake_Provider and the fake Clock by delaying the
   Grading_Job start after acceptance by (a) less than the Usable_Run_Budget, asserting that the
   Checks run only within the reduced remaining budget, and (b) at least the Usable_Run_Budget,
   asserting that no Check runs and every Check is recorded as could-not-complete.

### Requirement 12: Terminal WebSocket

**User Story:** As a learner, I want a responsive terminal in my browser, connected only to my own
sandbox, so that I can practice real shell work safely.

#### Acceptance Criteria

1. THE Terminal_Gateway SHALL require an authenticated server-side session on every WebSocket
   connect (ADR 0004).
2. WHEN a WebSocket connects, THE Terminal_Gateway SHALL verify that the authenticated learner owns
   the target Lab_Session AND SHALL refuse the connection otherwise, using a refusal identical to the
   refusal for a nonexistent or non-active Lab_Session, so that the refusal does not reveal whether
   the Lab_Session exists.
3. THE Terminal_Gateway SHALL attach the browser terminal to the Sandbox TTY through
   `SandboxProvider.attach_terminal`.
4. WHEN the browser terminal is resized, THE Terminal_Gateway SHALL apply the new rows and columns to
   the Sandbox TTY.
5. THE Terminal_Gateway SHALL send periodic heartbeats and close a connection that misses them.
   Initial Config value: ping every 20 s, close after 2 missed pongs.
6. WHEN the learner types in the terminal, THE Terminal_Gateway SHALL record learner activity for the
   Idle_Timeout.
7. THE Terminal_Gateway SHALL persist no terminal input or output.
8. WHEN the api process shuts down gracefully, THE Terminal_Gateway SHALL close each open terminal
   WebSocket with a reconnect message (`docs/LABS-DESIGN.md` D4 §4.2).
9. THE Terminal_Gateway SHALL limit each connection's message size and rate. Initial Config values:
   64 KiB per message, 1000 messages per second. The rate limit is a token bucket: a burst of up
   to the configured rate is allowed, then the configured rate is sustained, so over any span of
   w seconds at most RATE + RATE * w messages are accepted.
10. WHEN a WebSocket connects, THE Terminal_Gateway SHALL validate the request origin against the
    Config allowlist AND SHALL refuse the connection, with the refusal of criterion 2, WHEN the
    target Lab_Session is not an Active_Session.
11. Beyond authentication (criterion 1), origin validation, and Active_Session state (criterion 10),
    THE Terminal_Gateway SHALL make ownership verification (criterion 2) the only authorization
    decision on connect, and message limits (criterion 9) and heartbeats (criterion 5) SHALL apply
    only after connect.
12. THE test suite SHALL verify criteria 1, 2, 5, 7, 8, 10, and 11 with the Fake_Provider and
    criteria 3, 4, and 6 against the Docker_Provider.

### Requirement 13: Reconnect and browser refresh

**User Story:** As a learner, I want a refresh or a network blip to put me back in the same sandbox,
so that I do not lose my work.

#### Acceptance Criteria

1. WHILE a Lab_Session is Active_Session, THE Sandbox SHALL remain alive when no terminal WebSocket
   is connected, until Requirement 7 ends it.
2. WHEN a terminal WebSocket reconnects, THE Terminal_Gateway SHALL re-verify ownership of the
   Lab_Session exactly as on first connect.
3. WHEN the browser is refreshed AND the Lab_Session is still alive, THE Lab_View SHALL reconnect to
   the same Sandbox AND the files the learner created SHALL still exist.
4. WHILE the terminal WebSocket is disconnected, THE Lab_View SHALL display a visible reconnect
   indication and retry with bounded backoff. Initial Config value: delays of 1, 2, 4, 8 s, then 8 s,
   for up to 60 s.
5. THE Terminal_Gateway SHALL keep at most one live terminal connection per Lab_Session at all
   times, and WHEN a new connection for a Lab_Session is accepted, THE Terminal_Gateway SHALL close
   any earlier connection for it.
6. THE test suite SHALL verify criteria 1 through 5 with an end-to-end browser test that creates a
   file, refreshes, and finds the file.

### Requirement 14: Code exercises

**User Story:** As a learner, I want to write code in an editor and have tests run against it inside
the sandbox, so that I can practice programming safely.

#### Acceptance Criteria

1. THE Lab_View SHALL present a Monaco editor for each learner file of a Code_Exercise.
2. WHEN the learner saves or requests grading, THE Session_Service SHALL copy the learner files into
   the Sandbox through `copy_in` into the learner workspace.
3. THE Grading_Job SHALL run the Code_Exercise tests inside the Sandbox as the Grader_User, executing
   the learner's code as an unprivileged subprocess with the per-Check timeout.
4. THE Grading_Job SHALL execute the tests from the Check_Bundle copied at grading time
   (Requirement 10) AND SHALL NOT import or run any test code, helper, or configuration file from the
   learner workspace.
5. THE Grading_Job SHALL compare the learner program's observed behavior (output and exit status)
   against expected values held in the Check_Bundle.
6. THE Session_Service SHALL cap learner file count and size. Initial Config values: 10 files, 64 KiB
   per file.
7. IF learner files exceed the caps, THEN THE Session_Service SHALL reject the request with an RFC
   9457 problem-details response and copy nothing into the Sandbox.
8. THE Lab_View SHALL provide keyboard access to the editor, including a documented way to leave the
   editor with the keyboard.
9. THE learner files SHALL be stored only in the Sandbox and SHALL NOT be persisted after the Lab_Session
   is destroyed.
10. WHEN grading a Code_Exercise, THE Grading_Job SHALL copy each declared learner file into a
    Grader_User-owned directory using no-follow semantics before executing it, and SHALL execute
    only the copy.
11. IF a declared learner file is a symlink or a non-regular file, THEN THE Grading_Job SHALL reject
    it, execute nothing from it, and record the Grading_Run as could-not-complete.
12. THE test suite SHALL verify criteria 10 and 11 against the Docker_Provider (Requirement 29.9).

### Requirement 15: Lab view

**User Story:** As a learner, I want one page with instructions, my terminal or editor, hints, a
check button, a reset, and a timer, so that I can focus on the task.

#### Acceptance Criteria

1. THE Lab_View SHALL render the lab's instructions and tasks in the active locale.
2. THE Lab_View SHALL render an xterm.js terminal for a terminal lab and a Monaco editor for a
   Code_Exercise.
3. THE Lab_View SHALL provide progressive hints; WHEN the learner reveals a hint, THE Learning_API
   SHALL record a `hint_reveal` and the next hint SHALL become available only after the previous one
   is revealed (#[[file:.kiro/steering/domain-rules.md]]).
4. THE Lab_View SHALL provide a "Check my work" button that requests grading.
5. WHILE a Grading_Run is running or 10 s or less have passed since the last accepted request, THE
   Lab_View SHALL disable "Check my work" and indicate why.
6. WHEN more than 10 s have passed since the last accepted request AND no Grading_Run is running,
   THE Lab_View SHALL enable "Check my work".
7. THE Lab_View SHALL provide a "Reset lab" action that, after confirmation, ends the current
   Lab_Session, destroys its Sandbox, and starts a fresh Lab_Session for the same lab, WHILE leaving
   recorded completions intact.
8. THE Lab_View SHALL display a timer showing time remaining until `ttl_at`, and a warning when idle
   time remaining or TTL remaining falls below a Config threshold. Initial Config value: 2 minutes.
9. THE Lab_View SHALL be fully operable with the keyboard alone, including every action above.
10. THE Lab_View SHALL provide a documented keyboard shortcut that moves focus out of the terminal,
   display the shortcut in the Lab_View, and document it in `docs/LABS.md`.
11. THE Lab_View SHALL be available under `/fr` and `/en`, with every label, status, hint, and error
    translated.
12. WHEN a lab is referenced by a lesson, THE Web_App SHALL replace the "coming soon" placeholder of
    the learning-core phase with a link to the Lab_View.

### Requirement 16: Accessibility

**User Story:** As a learner using assistive technology, I want the lab view to be accessible, so
that I can complete labs.

#### Acceptance Criteria

1. THE Lab_View SHALL have no axe-core violations in `/fr` and `/en`, in the ready, grading,
   results, error, and reconnecting states.
2. THE Lab_View SHALL announce Check results, timer warnings, and reconnect status to assistive
   technology through live regions.
3. THE Lab_View SHALL provide a visible keyboard focus indicator on every interactive element.
4. THE Lab_View SHALL NOT trap keyboard focus in the terminal or the editor.
5. THE test suite SHALL run Playwright with axe-core against the Lab_View in each state and locale
   listed in criterion 1.

### Requirement 17: Lab API conventions

**User Story:** As a client developer, I want lab endpoints to follow the platform conventions, so
that they behave like the rest of the API.

#### Acceptance Criteria

1. THE Learning_API SHALL address Lab_Sessions, Grading_Runs, and labs by `stable_id`, and no
   internal numeric key SHALL appear in any lab response body, header, cursor, or stream payload
   (#[[file:.kiro/steering/api-conventions.md]] §4).
2. THE Learning_API SHALL serve every lab endpoint under `/v1`
   (#[[file:.kiro/steering/api-conventions.md]] §3).
3. WHEN a lab request fails, THE Learning_API SHALL return an RFC 9457 problem-details response
   whose learner-facing `detail` is translated and actionable and contains no raw sandbox output,
   paths, or stack traces (#[[file:.kiro/steering/api-conventions.md]] §1).
4. THE Learning_API SHALL accept an `Idempotency-Key` on lab-session creation and grading requests
   AND WHEN the same key repeats, THE Learning_API SHALL return the stored result without creating a
   second Lab_Session or Grading_Run (#[[file:.kiro/steering/api-conventions.md]] §5). A new
   start request with a different key from a learner who has an Active_Session is not a repeat and
   SHALL be rejected per Requirement 7.7 and Requirement 30.
5. THE Learning_API SHALL list a learner's past Lab_Sessions with cursor-based pagination
   (#[[file:.kiro/steering/api-conventions.md]] §2).
6. THE test suite SHALL verify criterion 1 by calling every lab endpoint, including success, error,
   pagination, and stream responses, and asserting that no internal numeric key appears in any body,
   header, cursor, or stream payload.
7. THE test suite SHALL verify criterion 2 by enumerating the registered lab routes and failing WHEN
   any lab route is outside `/v1`.

### Requirement 18: Per-check results and translated feedback

**User Story:** As a learner, I want to see which checks passed and why one failed, in my language,
so that I can fix my work without reading raw program output.

#### Acceptance Criteria

1. WHEN a Check finishes, THE Learning_API SHALL stream its result (task, Check identifier, passed
   or failed, Feedback_Key) to the Lab_View before the whole Grading_Run ends.
2. THE Lab_View SHALL render each Check result using the Feedback_Key translated into the active
   locale.
3. THE Learning_API SHALL NOT send raw stdout, stderr, exit codes, file contents, or paths from
   Check execution to any learner-facing channel.
4. WHEN a Check has no matching Feedback_Key for an outcome, THE Content_Validator SHALL report the
   error (Requirement 20.5) so this case never reaches a learner.
5. WHEN every Check of a lab passes, THE Lab_View SHALL display a translated success state.
6. THE test suite SHALL verify criterion 3 by planting a unique marker in Check output and asserting
   it is absent from every response and stream payload.

### Requirement 19: Progression and mastery wiring

**User Story:** As a learner, I want passing a lab to count toward my mastery and unlock the next
module, so that labs are a real mastery mechanism.

#### Acceptance Criteria

1. WHEN a Check passes for a lab task for the first time for a learner, THE Domain_Engine SHALL
   record a `lab_task_completion` and a `mastery_event` with source lab for each concept the lab
   covers, using the Config lab mastery delta.
2. WHEN a Check that already passed for the learner passes again, THE Domain_Engine SHALL grant no
   additional mastery gain and SHALL keep the earlier `lab_task_completion`.
3. WHEN every required task of a lab has a `lab_task_completion`, THE Domain_Engine SHALL treat the
   lab check as passed for the Mastered gate of each covered concept
   (#[[file:.kiro/steering/domain-rules.md]]).
4. WHEN a hint was revealed for a task before its first passing Check, THE Domain_Engine SHALL apply
   the Config hint multiplier to the mastery gain of that task.
5. WHEN a Grading_Run is recorded as could-not-complete or failed, THE Domain_Engine SHALL grant no
   mastery gain for the failing tasks and SHALL NOT lower existing mastery.
6. WHEN lab results raise concept mastery to the required state, THE Domain_Engine SHALL unlock
   dependent modules by the learning-core unlocking rules, including module "Processes" after the
   prerequisite concepts of "Files & Permissions", as soon as concept mastery reaches the required
   state, including in the middle of a lab, without waiting for every lab task to complete.
7. THE progress endpoint SHALL include lab completion and lab-derived mastery.
8. THE Domain_Engine SHALL read the lab mastery delta and the hint multiplier from Config.

### Requirement 20: Lab content schema and `content validate`

**User Story:** As a content engineer, I want labs validated like the rest of the content, so that
broken labs never reach learners.

#### Acceptance Criteria

1. THE Content_Validator SHALL validate `lab`, `lab_task`, and `lab_check` items against their
   Content_Schema in addition to the learning-core checks.
2. IF a lab, task, hint, or Feedback_Key text lacks a French or English translation, THEN THE
   Content_Validator SHALL report the missing-translation error and exit non-zero.
3. IF a lab references a Lab_Image, concept, or Check_Bundle file that does not exist, THEN THE
   Content_Validator SHALL report the broken-reference error and exit non-zero.
4. IF a Check lacks a per-Check timeout, or declares one above the Usable_Run_Budget (initial 12 s),
   THEN THE Content_Validator SHALL report the error, with its rule name and the `stable_id` of the
   Check, and exit non-zero.
5. IF a Check outcome has no Feedback_Key with both translations, THEN THE Content_Validator SHALL
   report the error and exit non-zero.
6. IF a lab declares a Check_Bundle path inside a learner-writable location, THEN THE
   Content_Validator SHALL report the error and exit non-zero.
7. IF a lab with `origin` `ai` has no reviewer recorded in provenance, THEN THE Content_Validator
   SHALL report the `ai-reviewer-present` failure (the missing-reviewer error) and exit non-zero.
8. IF the sum of the per-Check timeouts of a lab exceeds the Usable_Run_Budget (initial 12 s), THEN THE
   Content_Validator SHALL report the `check-timeouts-fit-budget` failure, with its rule name and the
   `stable_id` of the lab, and exit non-zero.
9. WHILE a Seed_Lab has no reviewer recorded, THE Content_Validator SHALL report for that Seed_Lab
   exactly the `ai-reviewer-present` failure and no other failure.
10. THE Content_Sync SHALL upsert labs, tasks, and Checks by `stable_id` idempotently, keep existing
    `lab_task_completion` rows on edits, and archive removed labs rather than delete them
    (#[[file:.kiro/steering/content.md]]).
11. WHEN THE Content_Sync upserts an item whose `stable_id` already exists but whose structure
    changed, or an item was removed, THE Content_Sync SHALL preserve the existing learner progress
    records tied to the changed or removed item as orphaned data and SHALL NOT purge them.
12. THE CI pipeline SHALL run `edu-it content validate` over labs.
13. THE CI pipeline SHALL validate unreviewed Seed_Labs in a mode that tolerates only the
    `ai-reviewer-present` failure, and only for labs named in the Pending_Review_List; THE CI pipeline
    SHALL fail on any other rule failure and on any unreviewed lab not named in the Pending_Review_List.
14. THE test suite SHALL verify criteria 4, 8, 9, and 13 with a fixture lab and the Seed_Labs, including
    a listed lab with only the reviewer missing (passes in the tolerant mode), a listed lab with
    any other failure (fails), and an unlisted unreviewed lab (fails).

### Requirement 21: Seed lab 1, terminal lab "Fix the shared folder"

**User Story:** As a learner, I want a realistic terminal lab on shared-folder permissions, so that I
apply what the permissions lessons taught.

#### Acceptance Criteria

1. THE Seed_Labs SHALL include a terminal lab "Fix the shared folder" with `origin` `ai` and
   French and English instructions, tasks, hints, and Feedback_Keys.
2. THE lab SHALL require the learner to create the directories `/workspace/team/docs` and
   `/workspace/team/scripts`.
3. THE lab SHALL require the learner to give `/workspace/team` the group `devs` with the setgid bit
   set.
4. THE lab setup script SHALL plant a world-writable file under `/workspace/team` AND THE lab SHALL
   require the learner to find it and remove world-write permission without deleting the file or
   changing its content.
5. THE lab setup script SHALL make the group `devs` usable by the learner user without root
   privileges.
6. THE lab SHALL define one Check per requirement in criteria 2 through 4 (directories exist, group
   and setgid, no world-writable entry remains, planted file intact), each with a distinct Feedback_Key
   for each failure reason.
7. THE lab SHALL be solvable using only commands covered by the permissions lessons, and THE test
   suite SHALL run a reference solution that passes every Check and several partial solutions that
   each fail the expected Check.
8. THE lab SHALL be tagged with the concepts it covers so Requirement 19 applies.
9. THE lab setup script SHALL be idempotent.

### Requirement 22: Seed lab 2, code lab "World-writable entries"

**User Story:** As a learner, I want to write a small Python program that reads `ls -l` output, so
that I practice both permission strings and code.

#### Acceptance Criteria

1. THE Seed_Labs SHALL include a Code_Exercise "World-writable entries" with `origin` `ai` and
   French and English instructions, hints, and Feedback_Keys.
2. THE learner program SHALL read `ls -l` output on standard input and print the names of the
   world-writable entries, one per line, in input order.
3. THE Code_Exercise tests SHALL cover regular files, directories, symlinks, and sticky-bit entries
   (for example a directory with mode 1777), with at least one reported and one not-reported case
   per category, plus the `total` header line, names containing spaces, and empty input.
4. THE tests SHALL define the classification so that a symlink is never reported, because a
   symlink's mode bits are not enforced (an entry shown as `lrwxrwxrwx` is not reported), and the
   expected outputs SHALL follow this rule.
5. THE tests SHALL define the classification so that a world-writable directory with the sticky bit
   (for example `drwxrwxrwt`) is reported as a world-writable entry, and a sticky-bit entry without
   world-write permission is not reported; the user SHALL confirm the symlink and sticky-bit
   classification at the review checkpoint (Requirement 23).
6. THE Code_Exercise SHALL provide a starter file with the program skeleton.
7. THE Code_Exercise tests SHALL run inside the Sandbox in the Python Lab_Image, per Requirement 14.
8. THE test suite SHALL run a reference solution that passes every Check and several wrong solutions
   (such as reporting all `w` bits, reporting symlinks, ignoring directories, or ignoring
   sticky-bit directories) that each fail the expected Check.
9. THE tests SHALL map each failing outcome to a distinct translated Feedback_Key that does not
   include the expected output.

### Requirement 23: Expert review checkpoint and golden set

**User Story:** As a reviewer, I want to approve the AI-drafted labs before they are served, so that
only expert-approved labs become the reference set.

#### Acceptance Criteria

1. WHEN the Seed_Labs are authored and validated, THE workflow SHALL stop and ask the user to review
   the Seed_Labs, including the classification decisions in Requirements 22.4 and 22.5, before any further
   content step proceeds (#[[file:.kiro/steering/working-agreements.md]]).
2. WHEN the user approves the Seed_Labs, THE Content_Sync SHALL record that user as the reviewer in
   provenance (`reviewed_by`, `reviewed_at`), using the same identity convention as the learning-core
   Seed_Content.
3. WHEN the Content_Sync has recorded the reviewer AND the recording is confirmed as successful, THE
   workflow SHALL add the approved Seed_Labs to the Golden_Set for Phase 4 evals; THE workflow SHALL
   add no Seed_Lab to the Golden_Set before that confirmation.
4. IF the Seed_Labs are not approved, THEN THE workflow SHALL NOT add any lab to the Golden_Set,
   SHALL NOT record a reviewer, and SHALL NOT sync or serve the rejected labs in any form.
5. IF recording the reviewer fails, THEN THE workflow SHALL add no Seed_Lab to the Golden_Set.
6. WHILE a Seed_Lab has no reviewer, THE workflow SHALL treat the single `ai-reviewer-present`
   failure of `content validate` as the expected pre-review state, and SHALL treat any other
   failure as a defect to fix before the review of criterion 1 (Requirement 20.9).
7. THE Content_Sync SHALL refuse to sync a lab with `origin` `ai` that has no reviewer recorded.
8. WHEN the reviewer has been recorded for the Seed_Labs, THE workflow SHALL remove them from the
   Pending_Review_List, AND from then on THE Content_Validator and THE CI pipeline SHALL apply the
   full strict validation to them with no tolerated failure (Requirement 20.13).
9. THE test suite SHALL verify criteria 6 through 8 with the Seed_Labs before and after the
   reviewer is recorded.

### Requirement 24: GDPR export, deletion, and retention

**User Story:** As a learner exercising my data rights, I want lab data included in export and
deletion, so that my record is complete and removable.

#### Acceptance Criteria

1. WHEN a learner's data is exported, THE Learning_API SHALL include `lab_session`, `lab_run`, and
   `lab_task_completion` rows of that learner (#[[file:.kiro/steering/auth-privacy.md]]).
2. WHEN a learner's data is deleted, THE Learning_API SHALL delete the same rows AND SHALL destroy
   any Active_Session Sandbox of that learner.
3. THE Learning_API SHALL include no terminal input or output in an export, because none is persisted.
4. THE lab tables SHALL be covered by export, deletion, and retention (Requirement 9) as recorded in
   `docs/DATA-MODEL.md` D3 §13.
5. THE test suite SHALL fail WHEN a personal-data table introduced by this spec is not covered by
   export, deletion, and retention.

### Requirement 25: Failure scenario, provisioning failure or slowness

**User Story:** As a learner, I want a clear message when my lab cannot start, so that I know what to
do and nothing is left running.

#### Acceptance Criteria

1. IF sandbox provisioning fails, THEN THE worker SHALL retry up to 2 times (3 attempts in total) and
   no more often than 1 attempt per 10 s.
2. IF every provisioning attempt fails, THEN THE worker SHALL set the Lab_Session status to failed,
   call `destroy` for any partially created Sandbox, and THE Learning_API SHALL surface a translated,
   actionable error with no raw provider output.
3. IF a provisioning attempt does not finish within the provisioning timeout, THEN THE worker SHALL
   treat it as failed and apply criteria 1 and 2. Initial Config value: 30 s per attempt.
4. WHEN the 20 s readiness target is exceeded while provisioning is still in progress, THE Lab_View
   SHALL immediately display a distinct, translated slow-provisioning message and keep waiting
   until success or failure.
5. IF a Sandbox exists for a failed Lab_Session after the attempts end, THEN THE Reaper SHALL destroy it
   on its next run.
6. IF a provisioning failure occurs, THEN THE Session_Service SHALL NOT count the failed Lab_Session
   as an Active_Session.
7. THE test suite SHALL verify criteria 1 through 3, 5, and 6 with a Fake_Provider configured to fail,
   hang, and partially create a Sandbox.

### Requirement 26: Failure scenario, WebSocket drop or browser refresh

**User Story:** As a learner, I want a dropped connection or refresh to be recoverable, so that I do
not lose my session.

#### Acceptance Criteria

1. IF a terminal WebSocket drops, THEN THE Lab_View SHALL show a reconnect indication and retry
   (Requirement 13.4).
2. IF a terminal WebSocket reconnects, THEN THE Terminal_Gateway SHALL re-verify ownership and refuse
   the connection when the learner does not own the Lab_Session.
3. IF the browser is refreshed during an Active_Session, THEN THE Lab_View SHALL reconnect to the same
   Sandbox.
4. IF the Lab_Session ended while the WebSocket was down, THEN THE Lab_View SHALL display a
   translated message and offer to start a new Lab_Session.
5. IF the api process shuts down gracefully, THEN THE Terminal_Gateway SHALL send a reconnect message
   to each open terminal WebSocket before closing it.
6. IF a terminal WebSocket drops, THEN THE Terminal_Gateway SHALL persist no terminal input or
   output.
7. IF a WebSocket targets a Lab_Session owned by another learner, THEN THE Terminal_Gateway SHALL
   refuse it on connect and on reconnect.

### Requirement 27: Failure scenario, timer expiry while working or while grading

**User Story:** As a learner, I want a fair outcome when my time runs out, so that a result in flight
is not lost and my earlier passes are kept.

#### Acceptance Criteria

1. IF the TTL expires while the learner is working, THEN THE Reaper SHALL destroy the Sandbox, set
   status expired, and THE terminal WebSocket SHALL receive a translated expiry message before it
   closes.
2. IF the TTL or Idle_Timeout is reached while a Grading_Run is running, THEN THE Reaper SHALL defer
   destroying the Sandbox until the Grading_Run finishes or the 15 s run deadline passes, whichever is
   first.
3. IF a Grading_Run finishes before the deferred destruction, THEN THE Grading_Job SHALL record its
   results normally.
4. IF the Sandbox is destroyed before a Grading_Run finishes, THEN THE worker SHALL record the
   Grading_Run as could-not-complete and grant no mastery gain for unfinished tasks.
5. IF a grading request is received strictly after `ttl_at`, THEN THE Session_Service SHALL reject
   it with a translated problem-details response.
6. IF a Lab_Session expires, THEN THE Domain_Engine SHALL keep all previously recorded
   `lab_task_completion` rows.
7. WHEN a grading request is received at or before `ttl_at`, THE Session_Service SHALL process it
   even if processing finishes after `ttl_at`; THE Session_Service SHALL reject only requests
   received strictly after `ttl_at`.
8. THE test suite SHALL verify criteria 1 through 7 with the fake Clock and the Fake_Provider,
   including a grading request at exactly `ttl_at` (accepted) and one just after `ttl_at` (rejected).

### Requirement 28: Failure scenario, a Check exceeds its timeout

**User Story:** As a learner, I want a clear message when a check cannot finish, so that I am not
shown a hang or raw output.

#### Acceptance Criteria

1. IF a Check does not return within its per-Check timeout, THEN THE Grading_Job SHALL terminate the
   Check and every process it started, and record that Check as could-not-complete.
2. IF a Check is recorded as could-not-complete, THEN THE Learning_API SHALL deliver the translated
   "could not complete" feedback and no raw output (#[[file:.kiro/steering/labs.md]]).
3. IF a Check is recorded as could-not-complete, THEN THE Grading_Run SHALL be recorded with a failed
   status.
4. IF a Check times out, THEN THE Grading_Job SHALL still run the remaining Checks within the 15 s
   run deadline.
5. IF the run deadline passes with Checks not yet run, THEN THE Grading_Job SHALL record those Checks
   as could-not-complete.
6. IF a learner program loops forever, forks, or allocates unbounded memory during a Check, THEN THE
   Grading_Job SHALL still finish the Grading_Run within the 15 s target.
7. IF a learner program loops forever, forks, or allocates unbounded memory during a Check, THEN THE
   Grading_Job SHALL record that Check as could-not-complete, identically to a timeout, and SHALL
   NOT record a distinct resource-violation outcome.
8. IF the learner processes cannot be verified as stopped before the Checks run (Requirement 10.8),
   THEN THE Grading_Job SHALL run no Check and record the Grading_Run as could-not-complete, and THE
   Learning_API SHALL deliver the translated "could not complete" feedback and no raw output.
9. THE test suite SHALL verify criteria 1 through 8 against the Docker_Provider; Fake_Provider tests
   verify call logic only and are not security evidence.

### Requirement 29: Failure scenario, the learner tampers with files, PATH, aliases, or environment

**User Story:** As a platform owner, I want no tampering to change a Check result, so that a pass
means the learner did the work.

#### Acceptance Criteria

1. IF the learner modifies, deletes, or replaces any file in the learner workspace that has the name
   of a Check script, fixture, or expected-output file, THEN THE Grading_Job SHALL produce the same
   Check results as an untampered Sandbox in the same task state.
2. IF the learner prepends directories to `PATH`, THEN THE Grading_Job SHALL produce the same Check
   results as an untampered Sandbox.
3. IF the learner defines shell aliases or functions, edits shell startup files, or sets `BASH_ENV`,
   `ENV`, `LD_PRELOAD`, `LD_LIBRARY_PATH`, `PYTHONPATH`, `PYTHONSTARTUP`, or `IFS`, THEN THE
   Grading_Job SHALL produce the same Check results.
4. IF the learner places a fake executable named like a system binary (such as `ls`, `stat`, `id`,
   `python3`, or `find`) in a learner-writable directory, THEN THE Grading_Job SHALL not execute it.
5. IF the learner replaces a path that a Check inspects with a symlink to another location, THEN THE
   Grading_Job SHALL not follow the symlink in a way that changes the Check result.
6. IF the learner leaves a background process running that modifies files or prints a fake result, THEN
   THE Grading_Job SHALL produce the same Check results as an untampered Sandbox.
7. IF a Code_Exercise program writes to its standard output a line that imitates the result protocol
   of the harness, THEN THE Grading_Job SHALL ignore it.
8. IF the learner makes a Check file or fixture directory unreadable or writable, THEN THE Grading_Job
   SHALL not be affected, because the Check_Bundle is copied in at grading time (Requirement 10.1).
9. IF the learner replaces a declared learner file (Code_Exercise) or a path that a Check inspects
   (terminal lab) with a symlink to a file, a FIFO, or a device node, THEN THE Grading_Job SHALL not
   follow the link, SHALL not block on the replacement, SHALL execute nothing from it, and SHALL
   record the result as could-not-complete instead of a pass (Requirement 14.10 and 14.11).
10. THE test suite SHALL include a tamper test for each of criteria 1 through 9 against both
    Seed_Labs running in the Docker_Provider, and each test SHALL compare Check results to those of
    an untampered run, except for criterion 9, whose expected result is could-not-complete.
11. THE test documentation SHALL state that tamper resistance is evidenced by the Docker_Provider
    tests; Fake_Provider tests verify call logic only and are not security evidence.

### Requirement 30: Failure scenario, a second session or grading too often

**User Story:** As a platform owner, I want limits to hold under abuse, so that one learner cannot
hold several sandboxes or hammer the grader.

#### Acceptance Criteria

1. IF a learner starts a lab AND already has an Active_Session, whether for the same lab or for a
   different lab, THEN THE Session_Service SHALL reject the request with a `409 Conflict`
   problem-details response whose translated `detail` names the running lab and offers to end it,
   SHALL leave the existing Active_Session active, and SHALL create no Lab_Session.
2. IF two start requests for one learner arrive concurrently, THEN THE Session_Service SHALL create
   at most one Active_Session.
3. IF a learner exceeds the provisioning rate limit, THEN THE Session_Service SHALL reject the request
   with a `429 Too Many Requests` problem-details response with a `Retry-After` header, and SHALL
   create no Lab_Session.
4. IF a grading request arrives 10 s or less after the previous accepted grading request for the
   Lab_Session (including exactly 10 s), THEN THE Session_Service SHALL reject it with a `429 Too Many Requests`
   problem-details response with a `Retry-After` header, and SHALL create no Grading_Run.
5. IF a grading request arrives while a Grading_Run is running for the Lab_Session, THEN THE
   Session_Service SHALL reject it and SHALL NOT start a second Grading_Run.
6. IF a learner requests grading on a Lab_Session they do not own, THEN THE Session_Service SHALL reject
   the request without revealing whether the Lab_Session exists.
7. THE test suite SHALL verify criteria 1 through 6, including concurrency, with the fake Clock.

### Requirement 31: Performance targets

**User Story:** As a learner, I want a fast lab, so that waiting does not break my concentration.

#### Acceptance Criteria

1. THE system SHALL make a Sandbox ready within 20 s at the 95th percentile on the local
   development setup, measured from the start request to status ready.
2. THE Terminal_Gateway SHALL complete a keystroke round trip (key sent in the browser to echoed
   output in the browser) in under 150 ms at the 95th percentile on the local development setup.
3. THE Grading_Job SHALL return the results of a shell Check run within 15 s of "Check my work" for
   every run.
4. THE phase report SHALL record, for each target, the sample count, the method (nearest-rank
   percentile), the measured values of the final measurement, and the machine setup.
5. IF the final measurement misses any target, THEN THE final task SHALL halt per Requirement 35.3
   and THE system SHALL produce no phase report.
6. THE performance tests SHALL use at least 30 samples for criterion 1 and at least 200 keystrokes
   for criterion 2.
7. THE performance tests SHALL discard the first 3 warm-up samples of each target before counting
   the samples of criterion 6, and SHALL run on an otherwise idle machine.
8. IF a target is missed, THEN THE performance tests MAY perform one full re-measurement, AND every
   attempt, including the failed one, SHALL be logged in the verification output; the final
   measurement is the one that counts.

### Requirement 32: End-to-end journey

**User Story:** As a product owner, I want the full learner journey proven end to end, so that labs
work as part of the platform.

#### Acceptance Criteria

1. THE test suite SHALL include an end-to-end test that signs up a learner, enrolls them, passes a
   quiz, opens the lab, runs a command in the terminal, passes a Check, submits the project, and
   observes module "Processes" unlock.
2. THE test suite SHALL include end-to-end tests in which a learner passes the Checks of both
   Seed_Labs and sees translated feedback in `/fr` and in `/en`.
3. THE test suite SHALL run these tests against the Docker_Provider and the real worker, API, and
   web app, using the deterministic mock API only for page-level accessibility tests.

### Requirement 33: Traceability of labs rules to automated tests

**User Story:** As a reviewer, I want every security and grading-integrity rule in the labs steering
mapped to a requirement and a test, so that no rule is untested.

#### Acceptance Criteria

1. THE repository SHALL maintain a mapping from every rule in #[[file:.kiro/steering/labs.md]],
   #[[file:.kiro/steering/domain-rules.md]] (Labs section), and `docs/LABS-DESIGN.md` §4 to at least
   one requirement of this document and at least one automated test.
2. THE test suite SHALL fail WHEN a rule has no mapped requirement or no mapped test, or WHEN the
   mapping names a test that does not exist.
3. THE phase report SHALL list the mapping and the outcome of each test group.

### Requirement 34: Documentation, `docs/LABS.md`

**User Story:** As a developer and operator, I want one document explaining how labs work and what is
unsafe, so that nobody ships the dev provider by mistake.

#### Acceptance Criteria

1. THE repository SHALL include `docs/LABS.md` describing the architecture, the sandbox limits, the
   grading isolation, the lab content format, how to run labs locally, and how to add a lab.
2. THE `docs/LABS.md` SHALL state prominently that the Docker provider is never for production.
3. THE `docs/LABS.md` SHALL document the keyboard shortcut that leaves the terminal and the editor.
4. THE `docs/LABS.md` SHALL document every Config value introduced by this spec and its initial value.
5. THE required-docs test SHALL fail WHEN `docs/LABS.md` lacks the Docker-provider production warning
   of criterion 2.
6. THE required-docs test SHALL fail WHEN `docs/LABS.md` lacks the terminal-exit shortcut of
   criterion 3; this check is independent of the check in criterion 5.

### Requirement 35: Phase report

**User Story:** As the project owner, I want an acceptance report with real outcomes, so that I can
decide whether the phase is done.

#### Acceptance Criteria

1. WHEN every acceptance check has been run AND has passed, THE final task SHALL write
   `docs/reports/phase-3.md` recording, for each acceptance check, the command invoked and its real
   pass outcome.
2. THE `docs/reports/phase-3.md` SHALL include the performance measurements of Requirement 31, the
   mapping of Requirement 33, and the review outcome of Requirement 23.
3. IF any acceptance check fails or cannot be run, THEN THE final task SHALL halt and SHALL produce no
   `docs/reports/phase-3.md`.
4. WHEN a performance target is measured more than once, THE final task SHALL log every attempt in
   the verification output and SHALL use only the final measurement for the pass decision of
   Requirement 31 (including the re-measurement rule of Requirement 31.8).
