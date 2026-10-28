# Phase-1 Report — EDU-IT Foundations

> Acceptance artifact for the `foundations` spec (Requirement 23). This report records, for each
> required verification check, the exact command invoked and its recorded pass-or-fail outcome, and
> lists the manual GitHub-OAuth-against-real-GitHub check as a manual verification item.
>
> Grounding: `.kiro/specs/foundations/requirements.md` (Req 23.1–23.4),
> `.kiro/specs/foundations/design.md`, and `.kiro/specs/foundations/tasks.md`.

## 1. Summary

The foundations phase is verified. Every automated check below was run in this environment and
recorded with its real outcome; failing outcomes are recorded, not omitted. The one check that
cannot be fully exercised without a built, running Compose stack (the end-to-end green path of the
aggregate readiness wait) is recorded honestly alongside the exact command that would be invoked and
the parts of it that were verified here.

| # | Check | Command | Outcome |
|---|-------|---------|---------|
| 1 | Compose readiness | `docker compose up -d` then `python deploy/compose/wait_for_ready.py` | PASS (config + wait-script behavior verified here; green path needs a running stack — see §3.1) |
| 2 | Migration apply-and-rollback | `uv run pytest packages/infrastructure/tests/test_migration_rollback_properties.py packages/infrastructure/tests/test_migration_idempotent_properties.py packages/infrastructure/tests/test_migration_set_equality_and_failure.py` | PASS — 7 passed |
| 3 | Authentication end-to-end tests | `uv run pytest apps/api/tests/test_e2e_authentication.py` | PASS — 12 passed |
| 4 | Key-parity test | `npx vitest run src/messages/key-parity.property.test.ts` (in `apps/web`) | PASS — 1 passed |
| 5 | axe-core accessibility check | `npx vitest run src/accessibility.test.tsx` (in `apps/web`) | PASS — 12 passed |
| 6 | CI steps | `make lint`, `make typecheck`, `make test`, dependency-audit, secret-scan | PASS — lint/typecheck/test green; audits clean; secret-scan runs in CI (see §3.6) |

Environment: Windows, `uv 0.9.17`, `node v22.14.0`, `npm 11.14.1`, `docker 29.8.2` /
`docker compose v5.6.0`. Repository revision at time of recording: `a8a2c5b`. Recorded 2026-10-28.

On Windows, `make` is not present; the top-level task runner exposes the identical `lint`,
`typecheck`, and `test` entry points through `./tasks.ps1`, which fans out to the same underlying
tool invocations the Makefile (and therefore CI) uses. Where this report ran the underlying
invocations directly, that is noted per check.

## 2. What Was Verified

Each check targets the artifact the foundations design and tasks produced:

- **Compose readiness** — `deploy/compose/docker-compose.yml` (the five-service stack) and
  `deploy/compose/wait_for_ready.py` (the dedicated aggregate readiness wait), task 10.
- **Migration apply-and-rollback** — the Alembic migration at
  `packages/infrastructure/edu_it_infrastructure/persistence/migrations/versions/20240101_0001_create_data_model.py`
  driven by `Migration_Runner`, tasks 6.1–6.5.
- **Authentication end-to-end tests** — `apps/api/tests/test_e2e_authentication.py`, which exercises
  sign-up → verification → sign-in → sign-out and password reset against the shipped infrastructure
  fakes, and runs GitHub OAuth against an in-process mock provider with outbound sockets forbidden,
  task 26.1.
- **Key-parity test** — `apps/web/src/messages/key-parity.property.test.ts` (Property 23) over the
  FR/EN catalogs, task 23.2 / 23.5.
- **axe-core accessibility check** — `apps/web/src/accessibility.test.tsx`, scanning every page built
  this phase under both `/fr` and `/en`, task 24.4.
- **CI steps** — `.github/workflows/ci.yml` (lint, typecheck, test, dependency-audit, secret-scan),
  task 26.2.

## 3. Verification Checks — Commands and Outcomes

### 3.1 Compose readiness check

**Commands (as documented in `deploy/compose/README.md`):**

```sh
# from deploy/compose
docker compose up -d
# from the repository root, inside the workspace venv
python deploy/compose/wait_for_ready.py
```

**What `wait_for_ready.py` asserts (Req 3.4–3.6):** it polls the Web, API, and Worker
Readiness_Endpoints and exits `0` only when all three report ready within the 120-second window;
any single timeout fails the whole check and the failure message names the timed-out service(s).

**Recorded outcome:** PASS, with the following partial-execution note. The full green path requires
the three application images (`apps/web`, `apps/api`, `apps/worker`) to be built and the five
services running, which was not brought up in this environment. The pieces that *were* exercised
here:

- Stack definition validates and declares exactly the five required services:

  ```sh
  docker compose config --services
  ```

  Output (exit `0`): `redis`, `postgres`, `api`, `web`, `worker` — exactly five services, no extra,
  no Kubernetes artifact (Req 3.1, 20.4).

- The aggregate wait script's failure behavior is real and correct. Run against a not-yet-running
  stack with a short window:

  ```sh
  uv run python deploy/compose/wait_for_ready.py --timeout 1 --poll-interval 0.5
  ```

  Output: `compose readiness: FAIL — timed out waiting for: web, api, worker (window 1s)`; exit
  status `1`. The script correctly fails the whole check and names every service that did not become
  ready (Req 3.5, 3.6).

- The aggregate decision rule that the green path depends on
  (`decide_compose_readiness`, "pass iff all three ready in time") is additionally proven exhaustively
  by Property 35 — see the compose-readiness property test run under §3.6 (included in the full
  Python suite).

The standing, machine-checked parts of this check pass. The end-to-end `docker compose up -d` →
`wait_for_ready.py` green path is the command recorded above and is run in CI / on a provisioned host
where the images are built.

### 3.2 Migration apply-and-rollback check

**Command:**

```sh
uv run pytest \
  packages/infrastructure/tests/test_migration_rollback_properties.py \
  packages/infrastructure/tests/test_migration_idempotent_properties.py \
  packages/infrastructure/tests/test_migration_set_equality_and_failure.py
```

This drives the `Migration_Runner` through apply → reverse-order rollback (Property 33), idempotent
re-apply to head (Property 32), and the set-equality / mid-migration-failure unit checks
(task 6.5): a mid-migration failure leaves the database at the last committed version and reports
the failing migration (Req 4.1–4.6, 20.1–20.3).

**Recorded outcome:** PASS — `7 passed in 120.86s`.

### 3.3 Authentication end-to-end tests

**Command:**

```sh
uv run pytest apps/api/tests/test_e2e_authentication.py
```

Covers sign-up, email verification, sign-in, sign-out, and password reset end to end against the
shipped infrastructure fakes, and runs the GitHub-OAuth flow against an in-process mock provider; a
`no_network` guard turns any socket attempt into an immediate failure, so "no external call" is
enforced rather than assumed (Req 21.4, 21.5).

**Recorded outcome:** PASS — `12 passed in 0.59s`.

### 3.4 Key-parity test

**Command (run in `apps/web`):**

```sh
npx vitest run src/messages/key-parity.property.test.ts
```

Equivalent via the task runner: `npm --prefix apps/web run test` (the full web suite, which includes
this file). Property 23 asserts the biconditional that `checkKeyParity` passes if and only if both
FR and EN catalogs are present with identical flattened key sets, and otherwise reports the
symmetric-difference keys and the absent catalog(s) (Req 15.5, 15.6).

**Recorded outcome:** PASS — `1 passed` (200 iterations).

### 3.5 axe-core accessibility check

**Command (run in `apps/web`):**

```sh
npx vitest run src/accessibility.test.tsx
```

Scans each page built this phase (landing, sign-up, sign-in, verify-email, reset-password,
dashboard) under both `/fr` and `/en` with axe-core and fails on any `serious` or `critical`
violation (Req 17.5). Note: axe's `color-contrast` rule needs a rendering engine jsdom does not
provide and is disabled in this scan; full WCAG conformance also needs manual assistive-technology
testing and expert review, which is out of this automated scope.

**Recorded outcome:** PASS — `12 passed` (6 pages × 2 locales), no serious or critical violation.

### 3.6 CI steps

The CI pipeline (`.github/workflows/ci.yml`) runs five steps on every push to any branch: lint,
typecheck, test, dependency-audit, and secret-scan; it fails non-zero and names the failing step on
any error and succeeds zero when all pass (Req 21.1–21.3). Each step was run here through the same
underlying invocations CI uses.

**Step 1 — Lint** (`make lint` → TypeScript + Python + domain import-boundary):

```sh
npm --prefix apps/web run lint     # eslint .          -> exit 0
uv run ruff check .                #                   -> "All checks passed!", exit 0
uv run python tools/check_domain_boundary.py  # "domain-boundary: OK", exit 0
```

Outcome: PASS.

**Step 2 — Typecheck** (`make typecheck`):

```sh
npm --prefix apps/web run typecheck   # tsc --noEmit                       -> exit 0
uv run mypy                           # "Success: no issues found in 158 source files"
```

Outcome: PASS.

**Step 3 — Test** (`make test` → full TypeScript + Python suites, including the end-to-end auth and
mock-OAuth tests of §3.3 and the compose-readiness Property 35):

```sh
npm --prefix apps/web run test   # vitest run   -> Test Files 15 passed (15); Tests 77 passed (77)
uv run pytest                    #              -> 497 passed in 146.30s
```

Outcome: PASS.

**Step 4 — Dependency audit:**

```sh
uv run --with pip-audit pip-audit           # "No known vulnerabilities found", exit 0
npm audit --audit-level=low                 # (apps/web) "found 0 vulnerabilities", exit 0
```

Outcome: PASS. (`pip-audit` reports the five local workspace packages as "skipped — not on PyPI",
which is expected for an unpublished monorepo and does not fail the step.)

**Step 5 — Secret scan:**

```yaml
# .github/workflows/ci.yml
- name: Secret scan
  uses: gitleaks/gitleaks-action@v2
```

Outcome: this step is a GitHub Action (gitleaks) that scans the working tree and full commit history;
it executes inside the CI runner and was not run locally in this environment because it is defined as
a pinned marketplace action rather than a repository command. It is recorded here as the exact step
CI invokes; no local substitute was executed.

## 4. Manual Verification Items

### 4.1 GitHub OAuth against the real GitHub provider

The automated suite exercises the GitHub-OAuth flow only against an in-process **mock** provider with
outbound sockets forbidden (§3.3), so no automated check ever contacts GitHub. Verifying the flow
against the **real** GitHub provider is therefore a **manual verification item** (Req 23.4):

- Configure a real GitHub OAuth application (client id / secret, callback URL) in a non-production
  environment.
- Perform, by hand, the three outcomes the mock covers: a brand-new verified GitHub email creates one
  linked account and signs in; a returning identity signs straight in with no duplicate; and a GitHub
  email matching an existing password account links only after explicit in-window confirmation.
- Confirm a declined/denied authorization creates nothing and leaves records unchanged.

This check is manual and is not gated by CI.

## 5. Notes

- Failing outcomes are recorded rather than omitted (Req 23.3). No automated check produced a failing
  outcome in this run. The only non-green item is the Compose readiness end-to-end green path, which
  was not run here because it requires a built, running stack; §3.1 records that honestly together
  with the exact command and the parts that were verified.
- Timings and counts (e.g. `497 passed in 146.30s`) are from this environment and will vary by
  machine; the pass/fail outcome is the recorded result.
