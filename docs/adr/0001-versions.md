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
| `sqlalchemy` | 2.1.6 | schema definition / Core for migrations; consumers: `packages/infrastructure`, `packages/content` (`Postgres_ContentStore`, api-route-wiring Requirement 16.4) |
| `pydantic` | 2.14.1 | Pydantic v2; consumers: `apps/api`, `packages/infrastructure`, `packages/content` (content authoring schemas) |
| `alembic` | 1.20.1 | migration runner (apply / rollback / version tracking) |
| `psycopg` | 3.3.6 | PostgreSQL driver (psycopg 3); also the optional `postgres` extra (`psycopg[binary]`) of `packages/content` for `edu-it-content sync --database-url` |
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

The MDX renderer named in the technology steering is introduced by the later task that first uses it
(MDX rendering) and will be pinned here at that time. xterm.js and the Monaco editor are pinned in
the hands-on-labs section below.

### Hands-on labs — dependencies introduced by the `hands-on-labs` spec

These pins are recorded before any manifest change (Requirements 4.12, 4.13). Each is the current
stable release verified against its registry (npm, PyPI, GitHub releases) at the time of pinning and
is pinned **exactly** (no `^`/`~`/`>=` ranges) in the manifest that adds it. The ADR-vs-manifest test
(`tools/tests/test_labs_adr_pins.py`) fails when a manifest or workflow carries one of these at a
different version or without a pin here.

| Dependency | Version | Registry | Manifest / location | Role |
| --- | --- | --- | --- | --- |
| `@xterm/xterm` | 6.0.0 | npm | `apps/web/package.json` | terminal emulator (xterm.js) |
| `@xterm/addon-fit` | 0.11.0 | npm | `apps/web/package.json` | xterm.js fit addon (terminal resize) |
| `monaco-editor` | 0.57.0 | npm | `apps/web/package.json` | code editor; `min/vs` assets self-hosted under `public/monaco` |
| `@monaco-editor/loader` | 1.7.0 | npm | `apps/web/package.json` | Monaco loader, configured to the self-hosted path (no CDN) |
| `websockets` | 17.3 | PyPI | `apps/api/pyproject.toml` | WebSocket protocol implementation used by uvicorn for the terminal route |
| `uvicorn` | 0.54.0 | PyPI | `apps/api/pyproject.toml` | ASGI server (already in the manifest at this version; the draining `uvicorn.Server` subclass depends on it) |
| `docker` | 7.2.0 | PyPI | `packages/infrastructure/pyproject.toml` | Docker SDK for Python, used by the dev-only `DockerSandboxProvider` |
| `opentelemetry-sdk` | 1.45.2 | PyPI | `packages/infrastructure/pyproject.toml` | OpenTelemetry tracing SDK with the bounded `BatchSpanProcessor` (kubernetes-deployment Req 13.1, 13.2) |
| `opentelemetry-exporter-otlp-proto-http` | 1.45.2 | PyPI | `packages/infrastructure/pyproject.toml` | OTLP/HTTP span exporter, built only when a collector endpoint is set (HTTP avoids `grpcio`) |
| `prometheus-client` | 0.26.0 | PyPI | `packages/infrastructure/pyproject.toml` | Prometheus client for the Lab_Metrics collectors served on the dedicated metrics port (kubernetes-deployment Req 13.3, 13.4, 13.12) |
| `trivy` | 0.76.1 | GitHub release (aquasecurity/trivy) | `.github/workflows/lab-images.yml` | vulnerability scanner for Lab_Images, configured to fail on any finding |
| `aquasecurity/trivy-action` | 0.36.0 | GitHub Action | `.github/workflows/lab-images.yml` | runs Trivy in CI; the `version:` input is set to the Trivy pin above |

Notes:

- `websockets` is the library uvicorn selects for WebSocket support. It is pinned directly so the
  version is not chosen by the resolver; `uvicorn` is installed without the `standard` extra so no
  other optional packages enter the tree unpinned.
- The GitHub Action is referenced by the commit SHA of the `v0.36.0` tag with the tag in a comment,
  and Trivy itself is installed at `0.76.1`, so neither floats.
- No other tool is introduced by this spec. The Lab_Image base images are pinned by digest in their
  Dockerfiles (Requirements 4.5 to 4.7), and `docker buildx bake` ships with the Docker installation
  already used by CI, so it adds no separately versioned dependency.

### Security overrides (`apps/web`) — transitive versions forced by `npm audit`

CI runs `npm audit --audit-level=low` and fails on any finding. When an advisory affects a
transitive dependency and no release of the parent that depends on it has the fix yet, the fixed
version is forced with an exact entry under `overrides` in `apps/web/package.json`. Each override
is recorded here first and removed once the parent releases a fixed range.

| Dependency | Version | Registry | Manifest / location | Forced under | Advisories fixed |
| --- | --- | --- | --- | --- | --- |
| `dompurify` | 3.4.17 | npm | `apps/web/package.json` `overrides` | `monaco-editor` 0.57.0 (pins `dompurify` 3.4.15 exactly) | GHSA-p98j-92pf-mc4p, GHSA-6688-9rhm-gjv2, GHSA-cg2g-xh64-v4q7, GHSA-mrv2-3pqr-j5jr, GHSA-c6gr-qvv4-5v3f |
| `minimatch` | 11.0.3 | npm | `apps/web/package.json` `overrides` | `eslint` 10.13.0, `@eslint/config-array` 0.23.5, `@typescript-eslint/typescript-estree` 8.72.1 (all `^10`) | GHSA-g3hv-75wg-m249 |

Notes:

- `minimatch` 11 keeps the 10.x API (`minimatch`, `Minimatch`, CommonJS and ESM entry points) and
  supports Node 18, 20 and 22+. It is used by the lint toolchain only, never shipped to browsers;
  `npm run lint` passes with the override.
- **Residual (not fixed by the override):** `monaco-editor` 0.57.0 ships a prebuilt
  `min/vs/editor-*.js` bundle with DOMPurify 3.4.15 inlined, and that bundle is what
  `scripts/copy-monaco.mjs` self-hosts under `public/monaco`. The override fixes the
  `node_modules/dompurify` package the audit inspects, not the code inside that bundle. Recorded as
  a risk in `docs/RISKS.md` (`R-12`); the fix is to move to the first `monaco-editor` release that
  bundles DOMPurify 3.4.17 or later and drop the `dompurify` override.

## Consequences

Pinning current stable versions makes builds reproducible and gives every downstream spec a
single, consistent set of versions to depend on, which reduces integration surprises between
`apps/web`, `apps/api`, `apps/worker`, and the shared package. The cost is ongoing maintenance:
the pins must be reviewed and bumped deliberately as new stable releases land, and a version bump
becomes a tracked change rather than an incidental one. Because the pins are authoritative, any
later spec that needs a different version must update this ADR rather than diverge silently, which
keeps version decisions visible and auditable.
