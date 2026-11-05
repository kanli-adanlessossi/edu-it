# Hands-on labs

How labs, the sandbox, and grading work, and how to run and extend them. Rules live in
`.kiro/steering/labs.md` and the spec in `.kiro/specs/hands-on-labs/`; this page is the
operator and author guide. The `SandboxProvider` signatures are in `docs/LABS-DESIGN.md`.

> **WARNING: The Docker provider is never for production.**
> `SANDBOX_PROVIDER=docker` is for local development only. It talks to the local Docker daemon and
> offers no multi-tenant isolation guarantee. The composition root refuses to start when
> `SANDBOX_PROVIDER=docker` is combined with `EDU_IT_ENV=production`. Do not work around that
> refusal. A production provider (for example Kubernetes) is future work behind the same port.

## Architecture

All sandbox technology sits behind the `SandboxProvider` port (`edu_it_domain/ports/sandbox.py`).
Two implementations exist: `DockerSandboxProvider` (dev only) and `Fake_SandboxProvider` (tests,
in memory, no Docker, subprocess, or socket imports).

- `apps/api` serves the REST endpoints, the SSE result stream, and the terminal WebSocket. It never
  executes learner content.
- `apps/worker` consumes `lab.provision`, `lab.grade`, `lab.destroy`, `lab.reap`, `lab.retain`,
  and `lab.sweep` jobs from the Redis task queue (ADR 0003). A scheduler enqueues the periodic
  jobs with a per-tick dedupe key.
- One container per Lab_Session, labelled `edu-it.managed=1` and `edu-it.session=<stable_id>`.
- Learner code is hostile. It runs only inside a Sandbox, never in the api or worker process.
- Domain rules (deadlines, eligibility, reaping, scheduling, outcome sanitization) are pure
  functions in `edu_it_domain/labs/`. Business numbers live only in
  `packages/domain/edu_it_domain/config.py`.

Session statuses: `provisioning`, `ready`, `grading`, `ended`, `expired`, `idle-expired`, `failed`.
A session is active in `provisioning`, `ready`, or `grading`. Every transition is a
compare-and-set on `(id, expected status)`. A learner has at most one active session.

## Sandbox limits

Each Sandbox runs as a non-root user with no network, all capabilities dropped,
`no_new_privileges`, no host mounts, and a read-only root filesystem.

| Limit | Value | Config |
|---|---|---|
| CPU | 1 CPU | `SANDBOX_CPU_LIMIT` |
| Memory | 512 MiB | `SANDBOX_MEMORY_LIMIT` |
| Processes | 128 | `SANDBOX_PID_LIMIT` |
| Writable quota | 256 MiB | `SANDBOX_WRITABLE_QUOTA` |

Docker cannot enforce a disk quota on every host (`--storage-opt size=` needs overlay2 on XFS with
`pquota`). The quota is enforced with size-limited tmpfs mounts for `/workspace`, `/home/learner`,
`/tmp`, and a root-owned `/grader`, and the parts sum exactly to the quota. tmpfs counts against the
memory limit, so the 256 MiB quota sits inside the 512 MiB memory limit.

Session lifetime: 60 minutes maximum (`LAB_SESSION_TTL`), 15 minutes of inactivity
(`LAB_IDLE_TIMEOUT`). Only learner activity (terminal input, file save, grading request) counts.

## Grading isolation

Grading never trusts anything the learner can touch.

1. The run deadline is **15 s** from the accepted instant (`accepted_at`). It splits into a
   **freeze verification bound of 1 s**, a **12 s Usable_Run_Budget**, and a **teardown reserve of
   2 s** (15 = 1 + 12 + 2). `USABLE_RUN_BUDGET` is derived, never stored. Startup validation
   fails if freeze bound plus teardown reserve is not below the run deadline.
2. Queue delay before the job starts is deducted from the Usable_Run_Budget, so queueing never
   pushes a run past 15 s. If nothing is left, no Check runs and every Check is
   `could_not_complete`.
3. Learner processes are stopped (SIGSTOP) and verified stopped, up to 5 attempts within the 1 s
   bound, before the bundle is copied in or any Check runs. If this cannot be verified the run
   fails closed: no Check runs, the run is `could_not_complete`, and no mastery is granted.
4. The sandbox part of the Check_Bundle is copied into the root-owned `/grader` area and Checks run
   as the Grader_User with a clean environment and absolute paths. An unsafe learner path
   (symlink, FIFO, device) maps to `could_not_complete`.
5. Code-lab expected values never enter the Sandbox. The in-sandbox harness runs the learner
   program as a hardened subprocess and reports observed output as data. The worker compares it
   with the `expected/` part read from the repository.
6. Sandbox output is data only. What reaches learners is limited to Check ids, outcome,
   `feedback_key`, and the translated message.
7. In a `finally`, `/grader` is emptied and the learner processes are always thawed.

Grading requests are accepted at most once per 10 s per session
(`LAB_GRADING_MIN_INTERVAL`), only when the session is `ready` and `now <= ttl_at`.

## Lab content format

Labs live under `content/<track>/` (seed labs: `content/linux-fundamentals/`).

| File | Holds |
|---|---|
| `labs.json` | Lab: provenance, `lesson_stable_id`, localized `title` and `instructions` (fr, en), `kind` (`terminal` or code), `image` (`linux-base` or `python`), `concept_stable_ids`, `setup_script`, `check_bundle`, `check_bundle_dest` |
| `lab_tasks.json` | Tasks of a lab, with a `required` flag |
| `lab_checks.json` | Checks: `stable_id`, `lab_task_stable_id`, `timeout_seconds`, `script`, and `outcomes` (code, `exit_code`, `feedback_key`, localized `message`) |
| `pending-review.json` | `{"stable_ids": [...]}`: labs whose only tolerated failure is a missing AI reviewer |

Bundle layout, `content/<track>/lab-bundles/<lab>/`:

- `setup/setup.sh` runs once as the learner after provisioning and creates the planted files.
- `sandbox/` is copied into `/grader` at grading time (Check scripts, harness, test cases).
- `expected/` is read by the worker and never copied into a Sandbox.
- `starter/` holds starter files for code labs.

A Check script exits with a code declared in its `outcomes`. An unknown code, signal, or timeout
becomes `could_not_complete`. Exit code 90 is reserved for an unsafe path and also maps to
`could_not_complete`. Each Check timeout must be at most the Usable_Run_Budget, and the sum of a
lab's Check timeouts must fit it too (`check-timeouts-fit-budget`).

## Running labs locally

1. Install Docker and build the images:
   ```sh
   cd lab-images
   docker buildx bake --load
   ```
   This produces `edu-it/lab-linux-base:dev` and `edu-it/lab-python:dev`. See
   `lab-images/README.md` for the mirror override and the command forms used in sandboxes.
2. Select the Docker provider for api and worker (development only):
   ```sh
   SANDBOX_PROVIDER=docker
   EDU_IT_ENV=development
   LAB_IMAGE_TAG=dev   # optional, default "dev"
   ```
   The default provider is `fake`, which needs no Docker.
3. Validate and sync content. Local validation is strict and reports `ai-reviewer-present` for any
   lab still unreviewed. CI uses `--allow-pending`.
   ```sh
   uv run edu-it-content validate --content-root content
   uv run edu-it-content validate --allow-pending --content-root content
   ```
   `content sync` always refuses an unreviewed AI lab.
4. Start the api, worker, and web app as for the other features, then open a lab from its lesson.

## Adding a lab

1. Pick the image (`linux-base` or `python`). Add tools to an image only through `lab-images/`.
2. Add the lab to `labs.json`, its tasks to `lab_tasks.json`, and its Checks to `lab_checks.json`,
   with fr and en text for every learner-facing string and a `feedback_key` per outcome.
3. Create the bundle: `setup/setup.sh`, `sandbox/` Check scripts (or harness and cases), and
   `expected/` for any expected values. Never put solutions or expected values in `sandbox/`.
4. Keep every Check timeout within the Usable_Run_Budget and their sum within it too.
5. List the lab in `pending-review.json` until an expert reviewer is recorded, then remove it.
6. Run `edu-it-content validate` and fix every reported rule failure.
7. Run the sandbox and grading tests, including the Docker-marked image-command test.

## Updating a base-image digest

The only base image is `alpine@sha256:<digest>` in `lab-images/linux-base/Dockerfile`; the Python
image builds on the base. The scan fails on any finding, so a newly published CVE turns the
scheduled run red until the digest is bumped. To bump it:

1. Pick the new multi-arch index digest of the Alpine tag.
2. Rebuild the images: `cd lab-images && docker buildx bake --load`.
3. Rescan both images with the pinned Trivy (see ADR 0001) and confirm there are no findings.
4. Update the pinned digest in the Dockerfiles. Only `lab-images/linux-base/Dockerfile` carries a
   pin; `lab-images/python/Dockerfile` builds `FROM linux-base`, so it follows automatically. Then
   re-run the Docker-marked image-command test.
5. Push and re-run the `lab-images.yml` workflow (`Lab images`) until it is green.

The workflow runs on changes under `lab-images/` and weekly (Mondays 05:17 UTC). It is not a gate
on other paths.

**No scan waiver mechanism exists.** There is no ignore file, allowlist, severity threshold, or
waiver for the image scan, and none may be added. A finding is fixed by changing the image, never
by suppressing it.

## Keyboard shortcut to leave the terminal and the editor

Press **Ctrl+M** (**Ctrl+Shift+M** on macOS) to move focus out of the terminal or the code editor,
so keyboard users are never trapped. In a terminal Ctrl+M is the same byte as Enter, so the
terminal pane swallows that chord; Enter itself is unaffected.

## Config values

Every value below is defined in `packages/domain/edu_it_domain/config.py`. Durations are
`timedelta`, sizes are bytes. The startup check `validate_lab_config` rejects a missing or
non-positive value.

| Config | Initial value | Meaning |
|---|---|---|
| `LAB_SESSION_TTL` | 60 minutes | Maximum Lab_Session lifetime |
| `LAB_IDLE_TIMEOUT` | 15 minutes | Inactivity before expiry |
| `LAB_GRADING_MIN_INTERVAL` | 10 s | Minimum spacing of accepted grading requests |
| `LAB_GRADING_RUN_DEADLINE` | 15 s | Grading run deadline from acceptance |
| `LAB_GRADING_FREEZE_BOUND` | 1 s | Freeze verification bound |
| `LAB_GRADING_FREEZE_ATTEMPTS` | 5 | Freeze verification attempts |
| `LAB_GRADING_TEARDOWN_RESERVE` | 2 s | Reserve for teardown and thaw |
| `USABLE_RUN_BUDGET` | 12 s (derived: 15 - 1 - 2) | Time available for Checks |
| `LAB_CHECK_TIMEOUT_DEFAULT` | 5 s | Timeout of a Check that declares none |
| `LAB_RESULT_POLL_INTERVAL` | 500 ms | Grading result poll interval |
| `SANDBOX_OPERATION_TIMEOUT` | 10 s | Bound on each call to the sandbox backend |
| `LAB_ALLOWED_WEB_ORIGINS` | `("http://localhost:3000",)` | Origins allowed to open the terminal WebSocket (non-empty) |
| `LAB_PROVISION_MAX_RETRIES` | 2 | Retries after the first failed attempt (3 attempts) |
| `LAB_PROVISION_MIN_INTERVAL` | 10 s | Minimum spacing between attempts |
| `LAB_PROVISION_TIMEOUT` | 30 s | Timeout of one attempt |
| `LAB_PROVISION_READY_TARGET` | 20 s | Target time to become ready |
| `LAB_PROVISION_RATE_LIMIT` | 5 | Lab starts per user per window |
| `LAB_PROVISION_RATE_WINDOW` | 10 minutes | Window of the start rate limit |
| `SANDBOX_CPU_LIMIT` | 1 | CPUs per Sandbox |
| `SANDBOX_MEMORY_LIMIT` | 512 MiB | Memory per Sandbox |
| `SANDBOX_PID_LIMIT` | 128 | Processes per Sandbox |
| `SANDBOX_WRITABLE_QUOTA` | 256 MiB | Total writable quota |
| `SANDBOX_QUOTA_WORKSPACE` | 160 MiB | `/workspace` share of the quota |
| `SANDBOX_QUOTA_HOME` | 32 MiB | `/home/learner` share |
| `SANDBOX_QUOTA_TMP` | 32 MiB | `/tmp` share |
| `SANDBOX_QUOTA_GRADER` | 32 MiB | `/grader` share |
| `LAB_PROGRAM_MEMORY_LIMIT` | 128 MiB | Memory of a learner program run in a Sandbox |
| `LAB_PROGRAM_PROC_LIMIT` | 32 | Processes of a learner program run in a Sandbox |
| `LAB_REAPER_INTERVAL` | 30 s | Session reaper interval |
| `LAB_SWEEP_INTERVAL` | 5 s | Provisioning sweep interval |
| `LAB_RETENTION_PERIOD` | 90 days | Lab data retention (proposed, to confirm) |
| `LAB_RETENTION_INTERVAL` | 24 hours | Retention purge interval |
| `TERMINAL_PING_INTERVAL` | 20 s | Server ping interval |
| `TERMINAL_MAX_MISSED_PONGS` | 2 | Missed pongs before close |
| `TERMINAL_MAX_MESSAGE_BYTES` | 64 KiB | Largest accepted terminal message |
| `TERMINAL_MAX_MESSAGES_PER_SECOND` | 1000 | Terminal message rate limit |
| `TERMINAL_STATUS_POLL_INTERVAL` | 2 s | Session status re-check interval |
| `LAB_ACTIVITY_WRITE_INTERVAL` | 5 s | Minimum spacing of `last_activity_at` writes |
| `LAB_RECONNECT_BACKOFF` | 1 s, 2 s, 4 s, 8 s (last repeats) | Client reconnect delays |
| `LAB_RECONNECT_WINDOW` | 60 s | Total reconnect time before giving up |
| `LAB_DESTROY_TIMEOUT` | 30 s | Bound on destroying a sandbox and waiting until it is gone (Kubernetes) |
| `LAB_PROVISION_CAPACITY_BACKOFF` | 10 s | Delay before a capacity-rejected request is retried; at least `LAB_PROVISION_MIN_INTERVAL` |
| `K8S_READY_CEILING` | 60 s | Hard ceiling on waiting for a pod to be Ready; at least `LAB_PROVISION_TIMEOUT` |
| `LAB_NODE_LOSS_TERMINAL_CLOSE_BOUND` | 10 s | Close terminals within this time of a node drain or failure |
| `LAB_INFRA_LOSS_MARK_BOUND` | 30 s | Mark a session ended-by-infrastructure within this time of detecting the loss |
| `LAB_EVICTED_POD_REMOVAL_BOUND` | 60 s | Remove an evicted pod object within this time of detecting it |
| `TERMINAL_DRAIN_RECONNECT_DELAYS` | 1, 2, 4, 8, 16, 30, 30, 30, 30, 30 s | Reconnect delays after a reconnect message |
| `TERMINAL_DRAIN_RECONNECT_MAX_ATTEMPTS` | 10 | Most attempts of the drain schedule |
| `TERMINAL_DRAIN_RECONNECT_CAP` | 30 s | No drain delay exceeds this |
| `TERMINAL_DRAIN_RECONNECT_FIRST_WITHIN` | 2 s | First drain attempt starts within this time |
| `TERMINAL_RESTART_RECONNECT_DELAYS` | 1, 2, 4, 5 s (last repeats) | Reconnect delays after an abrupt close |
| `TERMINAL_RESTART_RECONNECT_MAX_INTERVAL` | 5 s | Largest gap between restart attempts |
| `TERMINAL_RESTART_RECONNECT_TOTAL` | 60 s | The restart schedule stops after this total time |
| `API_DRAIN_PERIOD_DEFAULT_SECONDS` | 30 | Default Api drain period |
| `API_DRAIN_PERIOD_BOUNDS_SECONDS` | 5 to 300 | Allowed drain period range |
| `API_PRESTOP_DELAY_DEFAULT_SECONDS` | 5 | Default preStop delay before the drain starts |
| `API_PRESTOP_DELAY_BOUNDS_SECONDS` | 0 to 60 | Allowed preStop delay range |
| `LAB_FILE_MAX_COUNT` | 10 | Learner files per save |
| `LAB_FILE_MAX_BYTES` | 64 KiB | Size of one learner file |
| `LAB_WARNING_THRESHOLD` | 2 minutes | Warn the learner this long before expiry |
| `LAB_TASK_MASTERY_GAIN` | 15 | Mastery points per first-passed task per concept (hint multiplier applies) |
| `WORKER_TERMINATION_GRACE_DEFAULT_SECONDS` | 60 s | `terminationGracePeriodSeconds` the Worker assumes when the Chart does not pass one |
| `WORKER_SHUTDOWN_MARGIN_SECONDS` | 5 s | Time before the Worker pod's `terminationGracePeriodSeconds` elapses at which it re-queues every in-flight job and exits |
| `API_TERMINATION_GRACE_MARGIN_SECONDS` | 10 s | Margin added to the drain period plus preStop delay for the Api pod's `terminationGracePeriodSeconds` |
| `K8S_HELPER_CPU_MILLI` | 50 m | CPU request and limit of the `owner` helper container in a Kubernetes Lab_Pod |
| `K8S_HELPER_MEMORY_BYTES` | 16 MiB | Memory request and limit of the `owner` helper container in a Kubernetes Lab_Pod |
| `K8S_GRADER_CPU_MILLI` | 500 m | CPU request and limit of the `grader` container (where Checks run) in a Kubernetes Lab_Pod |
| `K8S_GRADER_MEMORY_BYTES` | 256 MiB | Memory request and limit of the `grader` container in a Kubernetes Lab_Pod |
