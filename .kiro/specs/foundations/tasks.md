# Implementation Plan: Foundations

## Overview

This plan converts the foundations design into incremental, test-driven coding tasks. It follows
the design's milestone order — Milestone A (scaffolding) → Milestone B (accounts) → Milestone C
(bilingual shell) — with the cross-cutting concerns (observability, RFC 9457 error format, CI)
woven in where they are first needed. Tasks honor the inward layering
(`api → application → domain ← infrastructure`): domain ports and config come first, then
infrastructure adapters and their fakes, then application use cases, then the FastAPI boundary,
then the Next.js web app. The full data model is migrated before any account feature; the
OpenAPI→TypeScript client is generated before any web page that calls the API.

Correctness properties from the design are implemented as property-based tests (Python with the
pinned PBT library for domain/application/infrastructure and migrations; TypeScript with the pinned
PBT library for `apps/web` pure logic). Each property test is tagged
`Feature: foundations, Property {n}: {text}`, runs at least 100 iterations, and is implemented by a
single property-based test. Out-of-scope behavior (learning features, labs, AI generation beyond the
`LLMProvider` fake, Kubernetes) is excluded; the data model is migrated in full but the
learning/lab/AI table behavior is not implemented.

Grounding: `.kiro/specs/foundations/requirements.md`, `.kiro/specs/foundations/design.md`, and the
steering and design documents they reference.

## Tasks

- [x] 1. Scaffold the monorepo layout and shared task runner
  - Create the `apps/web`, `apps/api`, `apps/worker`, `packages/domain`, `packages/application`,
    `packages/infrastructure`, and `deploy/compose` directories per the design's monorepo layout.
  - Add a top-level task runner exposing uniform `lint`, `format`, `typecheck`, and `test` entry
    points that CI and pre-commit will invoke.
  - _Requirements: 1.1, 1.2_

- [x] 2. Configure TypeScript and Python tooling with pinned versions
  - [x] 2.1 Configure `apps/web` TypeScript lint, format, typecheck, and test commands
    - Wire the TypeScript lint, format, typecheck, and test tools, pinning framework and tool
      versions per `docs/adr/0001-versions.md`.
    - Ensure each command exits non-zero with the offending file path and rule/location on
      violation, type error, or test failure, and exits zero when clean.
    - _Requirements: 1.1, 1.3, 1.4, 1.5, 1.6, 1.7_

  - [x] 2.2 Configure Python lint, format, typecheck, and test commands for api, worker, and shared package
    - Wire the Python lint, format, typecheck, and test tools for `apps/api`, `apps/worker`,
      `packages/domain`, `packages/application`, and `packages/infrastructure`, pinning versions per
      `docs/adr/0001-versions.md`.
    - Ensure each command exits non-zero with the offending location on violation, type error, or
      test failure, and exits zero when clean.
    - _Requirements: 1.2, 1.3, 1.4, 1.5, 1.6, 1.7_

  - [x] 2.3 Add the domain import-boundary lint check
    - Add a lint/import-boundary rule asserting `packages/domain` imports no web or API framework.
    - _Requirements: 1.8_

  - [x] 2.4 Write unit tests for the import-boundary check and exit-status behavior
    - Add fixtures proving a framework import in the domain fails the boundary check and that lint,
      typecheck, and test commands return the expected exit statuses.
    - _Requirements: 1.4, 1.5, 1.6, 1.7, 1.8_

- [x] 3. Add pre-commit hooks and the environment template
  - [x] 3.1 Configure pre-commit hooks over staged files
    - Add a pre-commit configuration that runs lint, format, and typecheck against only staged
      files, blocks the commit and names the failing hook on non-zero exit, and allows the commit
      when all hooks pass.
    - _Requirements: 2.1, 2.2, 2.3, 2.4_

  - [x] 3.2 Create the `.env.example` template
    - Enumerate every environment variable read by `apps/web`, `apps/api`, and `apps/worker`, one
      per line, each with a non-sensitive placeholder and no real secret or production value.
    - _Requirements: 2.5, 2.6_

- [x] 4. Define domain ports and business config
  - [x] 4.1 Define the `Clock`, `Mailer`, `ObjectStorage`, `TaskQueue`, and `LLMProvider` ports
    - Declare the five framework-free domain ports in `packages/domain` per the port list in tech
      steering.
    - _Requirements: 5.1, 5.5_

  - [x] 4.2 Add `packages/domain/config.py` with business constants
    - Define verification-link TTL (24 h), reset-link TTL (1 h), session duration, OAuth
      link-confirmation window, sign-in rate-limit threshold and window, and the password policy as
      business constants.
    - _Requirements: 7.2, 12.3_

- [x] 5. Implement the Test_Clock and port fakes
  - [x] 5.1 Implement the `Test_Clock`
    - Implement `set(instant)` and `advance(duration)` so time read through the injected `Clock`
      equals the set/advanced instant exactly; reject negative durations and invalid instants,
      leaving the current time unchanged and raising an error; enable only in test/dev.
    - _Requirements: 6.1, 6.2, 6.4, 6.5_

  - [x] 5.2 Write property test for Test_Clock set/advance
    - **Property 36: Test_Clock set and advance read back exactly**
    - **Validates: Requirements 6.1, 6.2, 6.4, 6.5**

  - [x] 5.3 Implement fakes for Mailer, ObjectStorage, TaskQueue, and LLMProvider
    - Implement a console/retained-record `Mailer` fake, in-memory `ObjectStorage` and `TaskQueue`
      fakes (`enqueue`/`consume` per ADR 0003), and an `LLMProvider` fake; each fake implements
      every port method and is substitutable without changing domain code.
    - The console Mailer writes mail to stdout and retains sent mail in a retrievable record for
      test inspection.
    - _Requirements: 5.2, 5.3, 5.4_

  - [x] 5.4 Write unit tests for fake substitutability and clock wiring absence in production
    - Assert each fake implements every port method, the dev Mailer retains sent mail, dev/test use
      the LLMProvider fake with no network call, and no production composition root registers a
      clock-setting interface.
    - _Requirements: 5.2, 5.3, 5.4, 6.3_

- [x] 6. Implement Alembic migrations for the complete data model
  - [x] 6.1 Create migrations for every Data_Model entity
    - Create exactly one table per entity in the `docs/DATA-MODEL.md` entity groups and ER diagram
      (identity, catalog, provenance, progress, mastery & review, AI/review ops, assessments), with
      no listed entity omitted and no extra table.
    - Include each table's internal numeric primary key (unexposed), foreign keys, and the unique
      constraint on each `stable_id`; migrate the full model but implement no learning/lab/AI table
      behavior.
    - Implement apply, reverse-order rollback, mid-migration rollback to last committed version with
      non-zero exit and failing-migration report, and idempotent re-apply to head.
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 20.1, 20.2, 20.3_

  - [x] 6.2 Write property test for idempotent migrate-to-head
    - **Property 32: Migrating to head is idempotent**
    - **Validates: Requirements 4.6**

  - [x] 6.3 Write property test for apply-then-rollback round trip
    - **Property 33: Apply-then-rollback round-trips an empty database to empty**
    - **Validates: Requirements 4.3**

  - [x] 6.4 Write property test for stable_id uniqueness and hidden internal key
    - **Property 34: Every API-addressable resource has a unique stable_id and hides its internal key**
    - **Validates: Requirements 4.4**

  - [x] 6.5 Write unit tests for entity/table set equality and mid-migration failure
    - Assert exactly one table per Data_Model entity (set equality) and that a mid-migration failure
      leaves the database at the last committed version and reports the failing migration.
    - _Requirements: 4.1, 4.2, 4.5_

- [x] 7. Implement infrastructure port adapters
  - [x] 7.1 Implement the production Clock and SMTP Mailer adapters
    - Implement the system-clock `Clock` and the SMTP `Mailer` (FR/EN templates) behind their domain ports, wiring the fakes in the test/dev composition root.
    - _Requirements: 5.1, 5.2_
  - [x] 7.2 Implement the object-storage and Redis-backed TaskQueue adapters
    - Implement the `ObjectStorage` adapter and the Redis-backed `TaskQueue` (ADR 0003) behind their domain ports.
    - _Requirements: 5.1, 5.2_
  - [x] 7.3 Implement the PostgreSQL repositories and Redis session and rate-limit stores
    - Implement the PostgreSQL repositories and the Redis-backed server-side session store and sign-in rate-limit counters (ADR 0004) behind their domain ports.
    - _Requirements: 5.1, 5.2_

- [x] 8. Implement the RFC 9457 error format and structured logging foundation
  - [x] 8.1 Implement Problem_Details serialization and locale rendering
    - Serialize every non-2xx response as `application/problem+json` with at least `type`, `title`,
      `status` where `status` equals the HTTP status code; render learner-facing `detail` in the
      request locale with a default-locale fallback; map Pydantic v2 validation failures to one
      extension entry per rejected field with location and reason; never leak internal
      representations, stack traces, SQL, or provider errors.
    - _Requirements: 19.1, 19.2, 19.3, 19.4, 19.5, 19.6, 7.8_

  - [x] 8.2 Implement structured JSON logging with request IDs
    - Emit structured JSON logs where each request record carries a Request_ID, generating one when
      no inbound Request_ID is present; keep internal diagnostics in logs only.
    - _Requirements: 18.4, 18.5, 18.6_

  - [x] 8.3 Write property test for non-2xx problem documents and status consistency
    - **Property 28: Every non-2xx response is a problem document with a consistent status**
    - **Validates: Requirements 19.1, 19.2**

  - [x] 8.4 Write property test for validation failure entries
    - **Property 29: Validation failures report one problem entry per rejected field**
    - **Validates: Requirements 7.8, 19.5**

  - [x] 8.5 Write property test for locale-aware detail with default fallback
    - **Property 30: Learner-facing detail follows the request locale with default fallback**
    - **Validates: Requirements 19.3, 19.4**

  - [x] 8.6 Write property test for no internal-diagnostic leakage
    - **Property 31: Error responses never leak internal diagnostics**
    - **Validates: Requirements 18.6, 19.6, 27.2**

  - [x] 8.7 Write property test for request log JSON and Request_ID
    - **Property 27: Every request log record is JSON and carries a Request_ID**
    - **Validates: Requirements 18.4, 18.5**

- [x] 9. Implement health and readiness endpoints
  - Expose a liveness Health_Endpoint (independent of external dependencies) and a Readiness_Endpoint
    (ready only when all required dependencies are reachable) on `apps/api` and `apps/worker`;
    readiness reports not-ready when a dependency is unreachable and ready again once PostgreSQL and
    Redis are reachable.
  - _Requirements: 18.1, 18.2, 18.3, 27.1, 27.3_

  - [x] 9.1 Write property test for readiness reflecting dependency reachability
    - **Property 26: Readiness is ready exactly when all required dependencies are reachable**
    - **Validates: Requirements 18.2, 18.3, 27.1, 27.3**

  - [x] 9.2 Write unit test for liveness independence from dependencies
    - Assert the Health_Endpoint returns liveness regardless of dependency reachability.
    - _Requirements: 18.1_

- [x] 10. Implement the Docker Compose stack and aggregate readiness wait
  - Define `deploy/compose/docker-compose.yml` with exactly five services (web, api, worker,
    PostgreSQL, Redis); a single start command brings all to running and a failure reports which
    service did not start while leaving others unchanged; api and worker connect to PostgreSQL and
    Redis.
  - Implement a dedicated aggregate readiness wait script that polls the web, api, and worker
    Readiness_Endpoints and passes only when all three report ready within 120 s, failing the whole
    check on any single timeout and naming the timed-out service.
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 20.4_

  - [x] 10.1 Write property test for aggregate compose readiness
    - **Property 35: Compose readiness passes exactly when all three services are ready in time**
    - **Validates: Requirements 3.4, 3.5, 3.6**

  - [x] 10.2 Write unit test for the five-service definition
    - Assert the compose file defines exactly the five named services and no Kubernetes artifact is
      produced.
    - _Requirements: 3.1, 20.4_

- [x] 11. Checkpoint - scaffolding verified
  - Run the full lint, typecheck, and test suite for the milestone and confirm all checks pass before proceeding; if any check fails, stop and surface the failure.

- [x] 12. Implement sign-up and email verification
  - [x] 12.1 Implement sign-up use case and verification links
    - Create an unverified learner User for a well-formed email (≤254 chars) and policy-compliant
      password and enqueue a single-use verification email expiring 24 h after issuance per the
      Clock; a duplicate-email sign-up creates no second User, returns the same non-disclosing
      response, and notifies the existing owner via the Mailer; validation failures return
      Problem_Details per rejected field.
    - _Requirements: 7.1, 7.2, 7.3, 7.6, 7.7, 7.8, 7.9, 13.3_

  - [x] 12.2 Implement verification-link consumption
    - Verify the email and invalidate the link on a valid, unexpired, unused link; reject an
      expired, used, or unrecognized link, leave verification state unchanged, and return a
      link-invalid/expired response offering to resend; enforce single use.
    - _Requirements: 7.4, 7.5, 25.1, 25.2_

  - [x] 12.3 Handle verification-email delivery failure
    - On Mailer failure during sign-up, keep the User unverified, log the failure, return a
      translated Problem_Details, and allow re-requesting the email.
    - _Requirements: 24.1, 24.3_

  - [x] 12.4 Write property test for valid self-service sign-up
    - **Property 4: Valid self-service sign-up creates one unverified learner and one mail**
    - **Validates: Requirements 7.1, 7.9, 13.3**

  - [x] 12.5 Write property test for duplicate-email non-disclosure
    - **Property 5: Duplicate-email sign-up does not disclose and does not duplicate**
    - **Validates: Requirements 7.6, 7.7**

- [x] 13. Implement sign-in, sign-out, and server-side sessions
  - [x] 13.1 Implement session creation, revocation, and authentication
    - Create a server-side Session (Redis-backed state, durable PostgreSQL record per ADR 0004) for
      valid, verified credentials, expiring after the configured duration per the Clock; sign-out
      revokes the current Session and returns success even when none is present; reject unverified
      accounts, invalid credentials (non-disclosing), and expired/revoked sessions with translated
      Problem_Details.
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5_

  - [x] 13.2 Write property test for session establishment conditions
    - **Property 6: Sign-in establishes a session only for valid, verified credentials**
    - **Validates: Requirements 8.1, 8.3, 8.4**

  - [x] 13.3 Write property test for session authentication validity
    - **Property 7: A session authenticates only while active**
    - **Validates: Requirements 8.5, 28.1**

  - [x] 13.4 Write property test for idempotent sign-out
    - **Property 8: Sign-out revokes the current session and is idempotent**
    - **Validates: Requirements 8.2**

- [x] 14. Implement link expiry and single-use property tests
  - [x] 14.1 Write property test for link TTL boundary
    - **Property 1: Links expire exactly at issuance + TTL as measured by the Clock**
    - **Validates: Requirements 7.3, 9.1**

  - [x] 14.2 Write property test for single-use links
    - **Property 2: Verification and reset links are single-use**
    - **Validates: Requirements 7.4, 9.2, 25.1**

  - [x] 14.3 Write property test for invalid links never changing state
    - **Property 3: Invalid links never change state**
    - **Validates: Requirements 7.5, 9.3**

- [x] 15. Implement password reset
  - [x] 15.1 Implement reset request and reset completion
    - Enqueue a single-use reset email expiring 1 h after issuance for a registered email; return an
      identical response and enqueue no email for an unregistered email; on a valid, unexpired,
      unused link with a policy-compliant password update the password and mark the link used; a
      stale link leaves the password unchanged and offers to restart; a weak password leaves it
      unchanged and reports the policy failure.
    - On success through an unauthenticated reset link, revoke all existing Sessions and establish
      exactly one new Session for the completing device; reject any Session revoked by a completed
      reset as unauthenticated without refresh or reissue; on Mailer failure make no password change,
      log it, return translated Problem_Details, and allow re-request.
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 24.2, 24.3, 25.3, 28.1, 28.2_

  - [x] 15.2 Write property test for post-reset session state
    - **Property 9: Successful reset leaves exactly the new session valid**
    - **Validates: Requirements 9.5, 28.1, 28.2**

  - [x] 15.3 Write property test for weak-password rejection
    - **Property 10: Reset rejects weak passwords without changing state**
    - **Validates: Requirements 9.4**

  - [x] 15.4 Write property test for reset non-disclosure
    - **Property 11: Reset requests do not disclose account existence**
    - **Validates: Requirements 9.6**

  - [x] 15.5 Write property test for mail-delivery failure safety
    - **Property 12: Mail-delivery failure leaves a safe state and allows retry**
    - **Validates: Requirements 24.1, 24.2, 24.3**

- [x] 16. Implement GitHub OAuth sign-in and account linking
  - [x] 16.1 Implement the OAuth callback flow and linking state machine
    - Create a User + `oauth_link` + Session for a verified email matching no User; sign in the
      linked User (no duplicate) when an `oauth_link` exists; for a verified email matching an
      existing User, establish a temporary unauthenticated session and link only after explicit
      in-window confirmation; refuse and leave records unchanged on decline (invalidating the pending
      token immediately), window expiry, or a GitHub-unverified email; create nothing and discard
      partial state on denial, provider failure, incompleteness, or `state` mismatch, returning a
      translated Problem_Details.
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 10.7, 26.1, 26.2, 26.3, 26.4, 26.5, 26.6_

  - [x] 16.2 Write property test for in-window link confirmation
    - **Property 13: GitHub OAuth links an existing user only on explicit in-window confirmation**
    - **Validates: Requirements 10.2, 10.6, 10.7, 26.2, 26.4, 26.5**

  - [x] 16.3 Write property test for new-email account creation and existing-link sign-in
    - **Property 14: New verified GitHub email creates a single linked account**
    - **Validates: Requirements 10.1, 10.4**

  - [x] 16.4 Write property test for failed/denied/unverified/state-mismatch flows
    - **Property 15: Failed, denied, unverified, or state-mismatched OAuth creates nothing**
    - **Validates: Requirements 10.3, 10.5, 26.1, 26.3, 26.6**

- [x] 17. Implement CSRF protection and auth rate limiting
  - [x] 17.1 Implement CSRF protection on unsafe mutations
    - Require a valid CSRF_Token on unsafe methods (per `docs/API-CONVENTIONS.md` Section 5) and
      process such requests only with a valid token; reject missing/malformed/invalid tokens with no
      state change and a Problem_Details response; require no token on safe idempotent methods.
    - _Requirements: 11.1, 11.2, 11.3, 11.4_

  - [x] 17.2 Implement sign-in rate limiting
    - While failed attempts for an identifier exceed the configured threshold within the window,
      reject further attempts with a translated Problem_Details without evaluating credentials and
      establish no Session even for valid credentials; allow attempts again once the window elapses
      per the Clock, using Redis counters and the business constants from `domain/config.py`.
    - _Requirements: 12.1, 12.2, 12.4_

  - [x] 17.3 Write property test for CSRF gating
    - **Property 16: CSRF protection gates exactly the unsafe mutations**
    - **Validates: Requirements 11.1, 11.2, 11.3, 11.4**

  - [x] 17.4 Write property test for rate-limit blocking
    - **Property 17: Rate limiting blocks over-threshold sign-ins without evaluating credentials**
    - **Validates: Requirements 12.1, 12.2, 12.4**

- [x] 18. Implement roles and the privileged-user CLI
  - [x] 18.1 Implement the identity role model and privileged-user CLI
    - Support learner, reviewer (with per-track expertise as the role assignment's track scope),
      author, and admin roles; the CLI creates or updates a User for a syntactically valid email,
      assigns a reviewer or admin role (with track scope for reviewer), and exits zero; it exits
      non-zero with no change and an error on an unknown role, missing/invalid email, or a reviewer
      role without a track scope; self-service paths assign only learner.
    - _Requirements: 13.1, 13.2, 13.4_

  - [x] 18.2 Write property test for the privileged-user CLI
    - **Property 18: The privileged-user CLI assigns exactly the requested privileged role**
    - **Validates: Requirements 13.2, 13.4**

  - [x] 18.3 Write unit test for the role enumeration
    - Assert the identity model supports exactly learner, reviewer, author, and admin.
    - _Requirements: 13.1_

- [x] 19. Implement GDPR export, deletion, and placeholder pages
  - [x] 19.1 Implement data export and account deletion
    - Produce a downloadable export (via the ObjectStorage port) containing every personal-data
      table in `docs/DATA-MODEL.md` Section 13 for the User, empty tables included; a confirmed
      deletion removes personal data across every personal-data table in a single transaction and
      revokes all Sessions, leaving all tables unchanged and returning an error if any table deletion
      fails.
    - _Requirements: 14.1, 14.2, 14.3, 14.4_

  - [x] 19.2 Serve privacy and terms placeholder pages
    - Serve FR and EN placeholder privacy and terms pages per the requested locale.
    - _Requirements: 14.5_

  - [x] 19.3 Write property test for GDPR export coverage
    - **Property 19: GDPR export covers exactly the personal-data tables**
    - **Validates: Requirements 14.1**

  - [x] 19.4 Write property test for deletion completeness and atomicity
    - **Property 20: Account deletion is complete and atomic**
    - **Validates: Requirements 14.2, 14.3, 14.4**

- [x] 20. Implement request-time datastore failure handling
  - When PostgreSQL or Redis becomes unavailable during a request, return a translated
    Problem_Details with a server-error status and log the failure without exposing stack traces,
    connection strings, or host identifiers in the body.
  - _Requirements: 27.2_

- [x] 21. Checkpoint - accounts verified
  - Run the full lint, typecheck, and test suite for the milestone and confirm all checks pass before proceeding; if any check fails, stop and surface the failure.

- [x] 22. Publish the OpenAPI document and generate the TypeScript client
  - Publish exactly one OpenAPI document per major version (path-prefixed `/v{major}`) from
    `apps/api`; generate the typed client from the targeted major version's document during the
    `apps/web` build before compiling application code; fail the build non-zero and report when the
    document is missing, and fail non-zero producing no client and reporting the malformation when it
    is malformed.
  - _Requirements: 16.1, 16.2, 16.3, 16.4, 16.5_

- [x] 23. Implement bilingual routing and language preference
  - [x] 23.1 Implement `/fr` and `/en` routing and the language switcher
    - Serve pages under `/fr` and `/en` route trees supporting exactly `fr` and `en` via next-intl;
      the language switcher moves to the other-locale view preserving path and query parameters; a
      signed-in User's selected locale is saved by the API and rendered on next visit, and a save
      failure leaves the previous preference unchanged and returns an error.
    - _Requirements: 15.1, 15.2, 15.3, 15.4_

  - [x] 23.2 Implement message catalogs, key-parity test, and JSX-literal lint rule
    - Add per-locale next-intl catalogs, a key-parity check that passes only when both catalogs share
      identical key sets and reports the symmetric-difference keys and absent catalog otherwise, and
      a JSX-string-literal lint rule that fails with file path and line on a user-facing literal.
    - _Requirements: 15.5, 15.6, 15.7_

  - [x] 23.3 Write property test for the language switcher URL algebra
    - **Property 21: The language switcher preserves path and query and round-trips**
    - **Validates: Requirements 15.2**

  - [x] 23.4 Write property test for saved locale preference round-trip
    - **Property 22: A saved locale preference round-trips**
    - **Validates: Requirements 15.3, 15.4**

  - [x] 23.5 Write property test for key parity
    - **Property 23: Key parity holds exactly when the catalogs share key sets**
    - **Validates: Requirements 15.5, 15.6**

  - [x] 23.6 Write unit test for the JSX-literal lint rule and routing presence
    - Add fixtures proving the JSX-literal rule flags offending literals and that `/fr` and `/en`
      route trees exist for exactly `fr` and `en`.
    - _Requirements: 15.1, 15.7_

- [x] 24. Implement the application shell and foundational pages
  - [x] 24.1 Build the shell, landing, auth pages, and empty dashboard
    - Provide an application shell, a landing page, the sign-up / sign-in / email-verification /
      password-reset pages, and an empty dashboard, each under `/fr` and `/en`; auth pages submit
      exclusively through the generated client; on a failed submission the page is preserved with
      entered non-password values, password fields cleared, and an error shown; the dashboard
      redirects a visitor with no active Session to sign-in and is reachable only with one.
    - _Requirements: 17.1, 17.2, 17.3, 17.4_

  - [x] 24.2 Write property test for auth-page field preservation
    - **Property 24: Auth-page submission failure preserves non-password fields**
    - **Validates: Requirements 17.3**

  - [x] 24.3 Write property test for dashboard session gating
    - **Property 25: The dashboard is reachable exactly with an active session**
    - **Validates: Requirements 17.4**

  - [x] 24.4 Run axe-core accessibility checks on every page built this phase
    - Scan each page with axe-core and assert no serious or critical violation.
    - _Requirements: 17.5_

- [x] 25. Correct steering fileMatch patterns
  - For each Phase-0 steering file with a `fileMatchPattern`, verify it matches a real path
    introduced this phase and update it where it does not; add a pattern to a Phase-0 steering file
    that covers a new real path but has none; preserve inclusion keys and parseable YAML front matter
    (in particular check `auth-privacy.md` and `api-conventions.md` against the real `apps/api` auth
    paths and `deployment.md` against `deploy/compose/`).
  - _Requirements: 22.1, 22.2, 22.3, 22.4_

  - [x] 25.1 Write property test for steering fileMatch coverage
    - **Property 37: Steering fileMatch patterns cover their intended real paths**
    - **Validates: Requirements 22.1, 22.2, 22.3, 22.4**

- [x] 26. Wire end-to-end authentication tests and the CI pipeline
  - [x] 26.1 Write end-to-end authentication tests
    - Cover sign-up, email verification, sign-in, sign-out, and password reset end to end; run
      GitHub-OAuth tests against a mock OAuth provider with no external call.
    - _Requirements: 21.4, 21.5_

  - [x] 26.2 Configure the CI pipeline
    - Run lint, typecheck, test, dependency-audit, and secret-scan on every push to any branch;
      fail non-zero and report the failing step on any error and succeed zero when all pass; include
      the end-to-end auth and mock-OAuth tests in the test step; produce no Kubernetes artifact.
    - _Requirements: 21.1, 21.2, 21.3, 21.4, 21.5, 20.4_

  - [x] 26.3 Write unit tests for scope-exclusion artifact absence
    - Assert no source or service artifact exists for learning features, labs/sandbox/terminal, AI
      generation beyond the LLMProvider fake, or Kubernetes deployment.
    - _Requirements: 20.1, 20.2, 20.3, 20.4_

- [x] 27. Checkpoint - bilingual shell and CI verified
  - Run the full lint, typecheck, and test suite for the milestone and confirm all checks pass before proceeding; if any check fails, stop and surface the failure.

- [x] 28. Write the phase-1 report
  - Produce `docs/reports/phase-1.md` recording, for each of the Compose readiness check, the
    migration apply-and-rollback check, the authentication end-to-end tests, the key-parity test,
    the axe-core accessibility check, and the CI steps, the exact command invoked and its recorded
    pass-or-fail outcome, recording failing outcomes rather than omitting them; list the manual
    GitHub-OAuth-against-real-GitHub check as a manual verification item.
  - _Requirements: 23.1, 23.2, 23.3, 23.4_

## Notes

- Tasks marked with `*` are optional test sub-tasks and can be skipped for a faster MVP; core
  implementation tasks are never optional.
- Each task references specific requirement sub-clauses for traceability, and every correctness
  property from the design is implemented by exactly one property-based test tagged
  `Feature: foundations, Property {number}: {property_text}` running at least 100 iterations.
- Checkpoints provide incremental validation at milestone boundaries.
- Layering is honored: domain ports/config (4) → fakes/Test_Clock (5) → migrations (6) →
  infrastructure adapters (7) → cross-cutting error/logging/health (8–9) → compose (10) → account
  use cases (12–20) → OpenAPI client (22) → web routing and pages (23–24).
- The full data model is migrated (task 6) but learning/lab/AI table behavior is not implemented,
  per the scope exclusions.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1"] },
    { "id": 1, "tasks": ["2.1", "2.2", "2.3", "3.1", "3.2", "4.1", "4.2"] },
    { "id": 2, "tasks": ["2.4", "5.1", "6.1"] },
    { "id": 3, "tasks": ["5.2", "5.3", "6.2", "6.3", "6.4", "6.5"] },
    { "id": 4, "tasks": ["5.4", "7.1", "7.2", "7.3"] },
    { "id": 5, "tasks": ["8.1", "8.2"] },
    { "id": 6, "tasks": ["8.3", "8.4", "8.5", "8.6", "8.7", "9"] },
    { "id": 7, "tasks": ["9.1", "9.2", "10"] },
    { "id": 8, "tasks": ["10.1", "10.2", "12.1", "13.1", "18.1", "19.1", "19.2"] },
    { "id": 9, "tasks": ["12.2", "12.3", "15.1", "16.1", "17.1", "17.2", "18.2", "18.3", "19.3", "19.4", "20"] },
    { "id": 10, "tasks": ["12.4", "12.5", "13.2", "13.3", "13.4", "14.1", "14.2", "14.3", "15.2", "15.3", "15.4", "15.5", "16.2", "16.3", "16.4", "17.3", "17.4"] },
    { "id": 11, "tasks": ["22"] },
    { "id": 12, "tasks": ["23.1", "23.2"] },
    { "id": 13, "tasks": ["23.3", "23.4", "23.5", "23.6", "24.1"] },
    { "id": 14, "tasks": ["24.2", "24.3", "24.4", "25", "26.1"] },
    { "id": 15, "tasks": ["25.1", "26.2"] },
    { "id": 16, "tasks": ["26.3", "28"] }
  ]
}
```
