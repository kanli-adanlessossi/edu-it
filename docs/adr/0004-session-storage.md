# ADR 0004 (A4): Session storage — server-side session backing

## Status

Accepted.

## Context

`apps/api` is the FastAPI boundary responsible for authentication and sessions, and it hosts the
authenticated terminal WebSocket, so a learner's session identity must be available both on
ordinary HTTP endpoints and when attaching a terminal to a lab sandbox. The labs design requires
that session ownership is re-verified on reconnect after a WebSocket drop, which means session
state must be readable server-side rather than trusted purely from client-held data. The
technology steering records "Session storage — server-side session backing" as an ADR topic and
states that Redis holds ephemeral/session state. The data model also includes a `session` entity
under the identity group. This record pins where and how server-side session state is kept so
that authentication, WebSocket ownership checks, and the identity schema line up.

This decision is grounded in the technology choices in `#[[file:.kiro/steering/tech.md]]` (Redis
holds ephemeral/session state; session storage is an ADR topic) and in
`#[[file:.kiro/steering/auth-privacy.md]]`, with the identity schema in `docs/DATA-MODEL.md` (D3)
and the WebSocket ownership-recheck behavior in `docs/LABS-DESIGN.md` (D4).

## Decision

Use server-side session backing: the client holds only an opaque session reference (not
authoritative session state), and the authoritative session state is kept server-side in Redis,
which the technology steering already designates for ephemeral/session state. `apps/api` resolves
the session on each request and on each WebSocket attach/reconnect by reading server-side state,
so ownership can be re-verified on reconnect rather than inferred from client-supplied claims. The
durable identity record (`session` in the data model's identity group) remains the system-of-record
reference for a session, while the fast ephemeral session state lives in Redis. Because the state is
server-side, sessions can be invalidated and ownership checks are authoritative at the server.

## Consequences

Server-side session backing makes session invalidation and ownership re-verification
authoritative: on WebSocket reconnect the server reads current session state and can reject a
request whose ownership no longer holds, satisfying the labs reconnect requirement. Keeping
ephemeral session state in Redis reuses an already-required dependency and keeps session reads
fast, at the cost of making Redis availability part of the authentication path, so session
behavior must account for Redis failure and for the shared use of Redis as both queue backing and
session store. Because the client holds only an opaque reference, tampering with client-held data
cannot grant access, but the server must perform a lookup on each request and each terminal
attach. The durable `session` record in PostgreSQL remains the identity system of record, so export
and deletion of personal data continue to cover sessions.
