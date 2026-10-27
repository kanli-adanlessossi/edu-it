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

## Consequences

Pinning current stable versions makes builds reproducible and gives every downstream spec a
single, consistent set of versions to depend on, which reduces integration surprises between
`apps/web`, `apps/api`, `apps/worker`, and the shared package. The cost is ongoing maintenance:
the pins must be reviewed and bumped deliberately as new stable releases land, and a version bump
becomes a tracked change rather than an incidental one. Because the pins are authoritative, any
later spec that needs a different version must update this ADR rather than diverge silently, which
keeps version decisions visible and auditable.
