# ADR 0001 (A1): Versions — pin current stable versions of all frameworks and test tools

## Status

Accepted.

## Context

EDU-IT is built from a fixed set of frameworks and tooling named in the technology steering:
`apps/web` uses Next.js App Router with next-intl, xterm.js, Monaco editor, MDX with an
allowlist of components, and Zod; `apps/api` uses FastAPI and validates with Pydantic v2;
`apps/worker` is a task-queue consumer; a shared framework-free Python domain package is reused
by api and worker; PostgreSQL is the system of record and Redis backs the task queue and
ephemeral/session state. Without a single authoritative place that pins the version of every
framework and test tool, independent specs and later phase work can drift onto mismatched
versions, which makes builds non-reproducible and makes it hard to reason about behavior across
`apps/web`, `apps/api`, `apps/worker`, and the shared package. The technology steering records
"Versions — pin current stable versions of all frameworks and test tools" as an ADR topic, so
this record is where that pinning decision lives.

This decision is grounded in the technology choices described in
`#[[file:.kiro/steering/tech.md]]` (the components and data-store list) and in the architecture
and layering described in `docs/ARCHITECTURE.md` (D1).

## Decision

Pin the current stable versions of all frameworks and test tools used across the repository, and
treat those pins as the single source of truth for version selection. The frameworks and tools in
scope are exactly those named in the technology steering: Next.js App Router, next-intl, xterm.js,
Monaco editor, the MDX renderer, Zod (for `apps/web`); FastAPI and Pydantic v2 (for `apps/api`);
the task-queue consumer runtime (for `apps/worker`); the shared framework-free Python domain
package; and the test tools used to verify them. Each version is the current stable release at the
time of pinning. The concrete version values are recorded and maintained by this ADR rather than
being chosen ad hoc per spec. This ADR does not relax the rule that the domain layer imports no
framework; version pins for domain-adjacent tooling apply only to the infrastructure and
application layers that are allowed to depend on frameworks.

## Pinned versions

Versions are the current stable releases at the time of pinning and are pinned **exactly** (no
`^`/`~` ranges) in each package manifest. The authoritative source for the concrete values is the
package manifest in each app (`apps/web/package.json` for the TypeScript tooling); this table
records them for auditability and must be updated together with those manifests.

### `apps/web` — TypeScript frameworks and tooling

| Package | Version | Role |
| --- | --- | --- |
| `next` | 16.4.3 | Next.js App Router |
| `next-intl` | 4.14.9 | i18n (`/fr`, `/en`) |
| `react` / `react-dom` | 19.3.1 | React runtime |
| `zod` | 4.6.7 | schema validation |
| `typescript` | 6.0.3 | typecheck (`tsc --noEmit`) |
| `eslint` | 10.13.0 | lint |
| `typescript-eslint` | 8.72.1 | TypeScript ESLint config/parser/plugin |
| `@eslint/js` | 10.0.1 | ESLint recommended JS config |
| `globals` | 17.13.0 | ESLint global definitions |
| `prettier` | 3.9.10 | format |
| `vitest` | 5.0.5 | test runner |
| `fast-check` | 4.10.2 | property-based testing library |
| `axe-core` | 4.14.0 | accessibility scanning engine (page axe-core checks, Req 17.5) |
| `jsdom` | 29.1.1 | DOM for running axe-core over server-rendered page markup in tests |
| `@types/node` | 26.6.5 | Node type definitions |
| `@types/react` / `@types/react-dom` | 19.3.0 | React type definitions |
| `@types/jsdom` | 30.0.0 | type definitions for the jsdom accessibility-scan harness |

Note on the TypeScript pin: the current-stable `typescript-eslint` (8.72.1) declares a `typescript`
peer range of `>=4.8.4 <6.1.0`, so it does not yet support the newest stable TypeScript. To keep a
single, mutually compatible toolchain where lint, typecheck, and typed linting all run, `typescript`
is pinned to `6.0.3` — the newest stable TypeScript within the linter's supported range. Bump it to
a newer stable TypeScript once `typescript-eslint` widens its peer range.

### Python dev toolchain (root `pyproject.toml` `dev` group)

The authoritative source is the root `pyproject.toml` `[dependency-groups] dev` list; this table
records the pins for auditability and must be updated together with it.

| Package | Version | Role |
| --- | --- | --- |
| `ruff` | 0.17.3 | lint (`ruff check`) + format (`ruff format`) |
| `mypy` | 1.20.2 | typecheck |
| `pytest` | 9.1.1 | test runner |
| `hypothesis` | 6.170.12 | property-based testing library |
| `pre-commit` | 4.6.2 | pre-commit hook runner (staged-file lint/format/typecheck) |
| `fakeredis` | 2.40.0 | Redis test double for the Redis-backed `TaskQueue` adapter's unit tests (no live server) |

The `pre-commit` runner invokes the same pinned `ruff`/`mypy` (from this group) and the pinned
`apps/web` TypeScript tooling (above) as `local` hooks against the staged files, so the hooks never
introduce a second, independently versioned toolchain. Its configuration lives in
`.pre-commit-config.yaml` at the repository root.

### `packages/infrastructure` — persistence (migrations and database driver)

The data model is migrated with Alembic over SQLAlchemy against PostgreSQL, so the migration and
database-access stack is pinned here and re-stated in `packages/infrastructure/pyproject.toml`
(its authoritative manifest). These are the current stable releases at the time of pinning and are
pinned **exactly**. Alembic and SQLAlchemy are authored by the same maintainer and are kept on
mutually compatible stable majors (Alembic 1.x targets SQLAlchemy 2.x).

| Package | Version | Role |
| --- | --- | --- |
| `sqlalchemy` | 2.1.6 | schema definition / Core for migrations |
| `alembic` | 1.20.1 | migration runner (apply / rollback / version tracking) |
| `psycopg` | 3.3.6 | PostgreSQL driver (psycopg 3) |
| `redis` | 8.1.0 | Redis client backing the `TaskQueue` adapter (ADR 0003) and, in a later task, the session / rate-limit stores (ADR 0004) |

The `redis` pin (added when the Redis-backed `TaskQueue` adapter was implemented) is re-stated in
`packages/infrastructure/pyproject.toml`. Its test double, `fakeredis` (pinned `2.40.0` in the root
`pyproject.toml` `dev` group), lets the adapter's unit tests run without a live Redis server; it is
a test-only tool and so lives with the other test tools in the dev group below.

These pins apply only to the infrastructure layer; the framework-free domain still imports no
framework or database library.

### `apps/worker` — health/readiness HTTP surface

`apps/worker` is a task-queue consumer runtime, but it exposes a Health_Endpoint and a
Readiness_Endpoint (Req 18) so the orchestrator and the aggregate Compose readiness wait can probe
it the same way they probe the API. That requires a minimal ASGI surface. Rather than add a second
web stack, the worker reuses Starlette — the ASGI toolkit that already backs the API's FastAPI — so
the pin below is the same Starlette resolved under `apps/api`. It is pinned **exactly** and
re-stated in `apps/worker/pyproject.toml` (its authoritative manifest).

| Package | Version | Role |
| --- | --- | --- |
| `starlette` | 1.10.0 | ASGI app for the worker Health_Endpoint / Readiness_Endpoint |

xterm.js, the Monaco editor, and the MDX renderer named in the technology steering are introduced by
the later tasks that first use them (labs/terminal and MDX rendering) and will be pinned here at that
time; this phase pins only the frameworks and tooling the foundations work installs.

## Consequences

Pinning current stable versions makes builds reproducible and gives every downstream spec a
single, consistent set of versions to depend on, which reduces integration surprises between
`apps/web`, `apps/api`, `apps/worker`, and the shared package. The cost is ongoing maintenance:
the pins must be reviewed and bumped deliberately as new stable releases land, and a version bump
becomes a tracked change rather than an incidental one. Because the pins are authoritative, any
later spec that needs a different version must update this ADR rather than diverge silently, which
keeps version decisions visible and auditable.
