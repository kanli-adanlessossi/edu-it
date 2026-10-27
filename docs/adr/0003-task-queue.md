# ADR 0003 (A3): Task queue — concrete choice behind `TaskQueue`

## Status

Accepted.

## Context

`apps/worker` is a task-queue consumer responsible for lab provisioning and grading
orchestration, AI generation jobs, variant-pool refill, spaced-review and decay computation, and
the reaper for expired sessions. The domain defines a `TaskQueue` port with the signatures
`enqueue(job_name, payload, dedupe_key)` and `consume(job_name, handler)`, and the technology
steering states that Redis backs the task queue and holds ephemeral/session state, while
explicitly recording "the final queue choice is an ADR." This record is where that concrete
backing is pinned. Keeping the queue behind the `TaskQueue` port means the application and domain
layers depend only on the interface, but a concrete backing still has to be chosen so that
enqueue/consume semantics, the `dedupe_key` behavior relied on by idempotent operations, and
operational concerns (retries, visibility, at-least-once delivery) are well defined.

This decision is grounded in the technology choices in `#[[file:.kiro/steering/tech.md]]` (Redis
backs the task queue and the queue choice is an ADR) and the architecture and ports described in
`docs/ARCHITECTURE.md` (D1).

## Decision

Back the `TaskQueue` port with Redis, consistent with the technology steering which already names
Redis as the task-queue backing. The `TaskQueue` interface — `enqueue(job_name, payload,
dedupe_key)` and `consume(job_name, handler)` — is implemented over Redis, with the `dedupe_key`
used to deduplicate enqueued jobs so that duplicate submissions and retried enqueues collapse to a
single job. Redis remains the backing for both the task queue and ephemeral/session state as the
steering describes. The domain and application layers continue to depend only on the `TaskQueue`
port; the Redis-backed implementation lives in the infrastructure layer and is accompanied by a
fake for tests, as required for every external-system port.

## Consequences

Choosing Redis reuses an already-required dependency rather than introducing a separate message
broker, which keeps the deployment topology simpler: the same Redis that holds ephemeral/session
state also backs the queue, and network policy already permits api and worker to reach it. Because
`enqueue` carries a `dedupe_key`, idempotent behaviors (such as deduped quiz submissions and
retried generation jobs) have a defined collapse point at the queue level. The trade-off is that
Redis-based queuing requires explicit handling of delivery and retry semantics in the
infrastructure implementation rather than inheriting them from a dedicated broker, and the queue
shares a backing store with session/ephemeral state, so capacity and failure planning must account
for both uses. Keeping the concrete choice behind the `TaskQueue` port means a future change of
backing updates this ADR and the infrastructure implementation without touching the domain.
