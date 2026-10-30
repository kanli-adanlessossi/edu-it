# Implementation Plan: API Route Wiring

## Overview

This plan exposes the existing application use cases as `/v1` routes, mounts every router in the
served API, connects the web pages through the generated client, and proves the result on the real
stack. It honors the layering `api → application → domain ← infrastructure`, the injected `Clock`,
and the rule that every business number lives in `packages/domain/edu_it_domain/config.py`. It adds
no business rule.

The plan has two milestones. Milestone 1 is the shortest path that unblocks hands-on-labs task 24 and
ends with the gate task, which reruns `python tools/run_verification.py --suite labs`. Milestone 2
starts only after that gate has passed.

- **Milestone 1 (unblock), tasks 1 to 14:** configuration and the password hasher; `credential` and
  `verification_link` persistence (revision `0003`); the verification link builder and the equal-work
  changes to sign-up and resend; session, cookie, CSRF, CORS, and route-table primitives; sign-up,
  verify-email, resend, sign-in, sign-out, and session routes; quiz and project read routes; the
  pending project reviewer; the PostgreSQL catalog sync in `packages/content`; the package
  dependency-graph check; the composition root, `create_app`, serving, and the OpenAPI export; the
  recorded risks and the 502 convention; the web client and its pages; the `e2e_stack` bootstrap; the
  real-stack Seed_Lab test; and the gate.
- **Milestone 2 (follow-up), tasks 15 to 21:** the `reset_link` and `pending_oauth_link` tables
  (revision `0004`) and adapters; password reset routes; GitHub OAuth with PKCE; account export,
  download, and deletion; their pages; and a final regression run.

No new dependency is planned except `sqlalchemy` in `packages/content` (task 7.2), which is already
pinned at `2.1.6` in `docs/adr/0001-versions.md` for `packages/infrastructure` and gets its ADR row
extended first. Password hashing uses `hashlib.scrypt` and the GitHub client uses `urllib.request`.
If a task needs another package, it first pins it in `docs/adr/0001-versions.md` and the
ADR-vs-manifest test (`tools/tests/test_labs_adr_pins.py`) must pass before the manifest changes.

Property-based tests use Hypothesis (Python) and fast-check (web), run a minimum of 100 iterations,
are driven by `Test_Clock` and the in-memory fakes, and carry a comment of the form
`Feature: api-route-wiring, Property {n}: {property text}`. No test contacts GitHub, a real SMTP
server, or the Docker daemon, except the real-stack tests in tasks 12 and 14.

Languages: Python for `packages/*`, `apps/api`, `apps/worker`, and `tools/`; TypeScript with the
Next.js App Router for `apps/web`.

## Tasks

### Milestone 1 (unblock hands-on-labs task 24)

- [ ] 1. Contracts, configuration, and the password hasher
  - [ ] 1.1 Add configuration values and settings
    - In `packages/domain/edu_it_domain/config.py` add `PASSWORD_HASH_SCRYPT_N`, `PASSWORD_HASH_SCRYPT_R`, `PASSWORD_HASH_SCRYPT_P`, `PASSWORD_HASH_SALT_BYTES`, and `PASSWORD_HASH_KEY_BYTES`, and add each name, in sorted position, to the module's `__all__` list. Do not register them in `validate_lab_config` (`edu_it_domain/labs/config_rules.py`): that function checks only `LAB_CONFIG_KEYS`, which are lab keys
    - Extend `CompositionSettings` with `web_base_url`, `cookie_domain` (env `COOKIE_DOMAIN`, applied to the session cookie and the `csrf_token` cookie), and `allowed_web_origins` (default `LAB_ALLOWED_WEB_ORIGINS`); reject a `web_base_url` that is not an absolute `http` or `https` URL
    - Add `WEB_BASE_URL` and `COOKIE_DOMAIN=` to `.env.example`, with a comment on `COOKIE_DOMAIN` stating that the Web_App and the API_Service must be deployed same-site (one registrable domain, or `localhost`); set `NEXT_PUBLIC_API_BASE_URL=http://localhost:8000/v1`
    - _Requirements: 2.3, 2.11, 8.6, 14.10, 16.5_
  - [ ] 1.2 Define the new domain ports
    - `ports/link_builder.py`: `LinkBuilder` with `verification_url(token, locale)` and `reset_url(token, locale)`
    - `ports/content_text.py`: `ContentTextSource` with `quiz_text` and `project_text`
    - `@runtime_checkable` Protocol pattern; no framework import in the domain
    - _Requirements: 8.3, 10.7, 16.1_
  - [ ] 1.3 Implement `Scrypt_PasswordHasher`
    - `edu_it_infrastructure/password_hasher.py` with `hash(raw) -> str` in the format `scrypt$N$r$p$salt$hash`, `verify(raw, stored) -> bool` using `hmac.compare_digest`, parameters read from the stored string, and a dummy-hash path for a missing credential
    - _Requirements: 7.1, 7.2_
  - [ ] 1.4 Write property test for the password hasher
    - **Property 1: Password hash round trip**
    - **Validates: Requirements 7.1, 7.2**

- [ ] 2. Persistence for sign-up and verification (revision `0003`)
  - [ ] 2.1 Add Alembic revision `0003_accounts_runtime` and update `metadata.py`
    - Create only `credential` and `verification_link` as specified in the design, with unique `token_digest` and unique `credential.user_id`; no other table
    - Provide a working downgrade that drops only these two tables
    - _Requirements: 7.4, 7.9_
  - [ ] 2.2 Implement the PostgreSQL adapters for credentials and verification links
    - `Postgres_CredentialStore` (takes the verifier from task 1.3) and `Postgres_VerificationLinkRepository` in `persistence/account_repositories.py`
    - Store only the SHA-256 hex digest of each token; `mark_consumed` is a single conditional `UPDATE` returning whether one row changed
    - _Requirements: 7.2, 7.5, 7.6, 7.8_
  - [ ] 2.3 Write contract and migration tests
    - One parametrized suite per port that runs against the in-memory fake and the SQL adapter (SQLite in unit tests, PostgreSQL when `DATABASE_URL` is set); migration up then down leaves the existing tables intact
    - The concurrent `mark_consumed` case (2 to 20 consumers) is marked PostgreSQL-only: it is skipped without `DATABASE_URL` and always runs in CI against a PostgreSQL service container; on SQLite keep only the digest and the sequential single-consume cases
    - **Property 11: Link tokens are stored as digests and consumed once**
    - **Validates: Requirements 7.5, 7.6, 7.8, 7.9**
  - [ ] 2.4 Extend GDPR coverage and the data-model document for `credential` and `verification_link`
    - Add the two tables to the personal-data gateway export, deletion, and retention paths, and to `docs/DATA-MODEL.md` Section 13 in the same change; extend the GDPR coverage test
    - _Requirements: 7.7_

- [ ] 3. Use-case changes for credentials, verification links, and equal work
  - [ ] 3.1 Implement `Web_LinkBuilder` and update the sign-up and resend use cases
    - `edu_it_infrastructure/link_builder.py` builds `{WEB_BASE_URL}/{locale}/verify-email?token=...` with a URL-encoded token (the reset URL is added in task 16.1)
    - `SignUpUseCase` takes `credentials: CredentialStore` and `link_builder: LinkBuilder` and stores the hashed credential before it sends mail; `ResendVerificationUseCase` takes `link_builder`; the verification mail puts the URL on a single line in fr and en
    - Equal work: in `SignUpUseCase` the already-registered branch calls `password_hasher(request.password)` once and discards the result; in `ResendVerificationUseCase` the unknown or already-verified branch calls `token_factory()` once and discards the result
    - The application package reads no environment variable
    - _Requirements: 3.11, 7.3, 8.1, 8.3, 8.4, 8.5_
  - [ ] 3.2 Update existing fixtures and tests for the new constructor arguments
    - Update `test_e2e_authentication.py` and the sign-up and resend tests; remove the manual `set_password` workaround from the end-to-end test and assert that sign-in succeeds after sign-up alone
    - _Requirements: 7.3, 16.1_
  - [ ] 3.3 Write property test for the verification link URL
    - **Property 10: Account link URLs round trip** (verification URL; the reset URL is added in task 16.4)
    - **Validates: Requirements 8.1, 8.4, 8.5**

- [ ] 4. API boundary primitives
  - [ ] 4.1 Add the shared resolver, cookie, CSRF token, CORS, and route-table modules
    - `identity.py` (`SessionUserResolver`, `NotAuthenticated`), `session_cookie.py` (`CookieSettings`, `set_session_cookie`, `expire_session_cookie`, `set_csrf_cookie`; the `csrf_token` cookie is not `HttpOnly`, is `SameSite=Lax` with `Path=/`, is `Secure` in production, and carries the same `Domain` as the session cookie), `csrf_tokens.py` (`new_csrf_token`, `ensure_csrf_cookie`)
    - `route_table.py` with `ROUTE_TABLE` holding every Milestone 1 entry of the design's Route_Table (method, path, authentication, use case, milestone, condition); later tasks append their own entries
    - `cors.py` with `install_cors(app, origins, route_table)`, deriving `allow_methods` from `ROUTE_TABLE` plus `OPTIONS` (so `PUT` is included); never hand-list the methods
    - _Requirements: 1.3, 1.4, 2.1, 2.2, 2.3, 2.4, 2.5, 2.7, 2.8, 2.9_
  - [ ] 4.2 Add account problem types and handlers for Milestone 1
    - `errors/accounts.py` with the Milestone 1 rows of the design's error table (sign-up validation, mail delivery, verification link invalid with `offer_resend`, sign-in 401, 403, 429 with `Retry-After`, `NotAuthenticated`), fr and en `detail`; call `install_account_error_handlers` from `install_error_handlers`
    - _Requirements: 1.4, 3.3, 3.4, 3.6, 3.8, 4.2, 4.3, 4.4, 16.6_
  - [ ] 4.3 Write property tests for cookie attributes and CORS
    - **Property 6: Cookie attributes follow configuration** (session cookie and `csrf_token` cookie)
    - **Validates: Requirements 2.1, 2.2, 2.3, 2.4**
    - **Property 8: CORS follows the allowlist and the route table** (every non-WebSocket `ROUTE_TABLE` method, including `PUT`, passes preflight from an allowed origin)
    - **Validates: Requirements 2.8, 2.9**

- [ ] 5. Account routes for sign-up, verification, and sign-in
  - [ ] 5.1 Implement schemas and the Milestone 1 account routes
    - `accounts_schemas.py` with strict request models (`email` and `password` typed as plain `str`, no length or pattern constraint) and frozen response models; `accounts.py` with `GET /auth/csrf`, `POST /auth/sign-up`, `POST /auth/verify-email`, `POST /auth/verify-email/resend`, `POST /auth/sign-in`, `POST /auth/sign-out`, `GET /auth/session`; each handler calls one use case and one cookie helper
    - Sign-in returns `{"redirect_to": "/dashboard"}` (snake_case, locale-free) and never the session reference; it sets a new Session_Cookie from the reference the use case created, never from the request cookie, and replaces the `csrf_token` cookie with `new_csrf_token()`
    - _Requirements: 2.1, 2.4, 2.5, 2.7, 3.1, 3.5, 3.7, 3.9, 3.10, 4.1, 4.5, 4.6, 4.8, 4.9_
  - [ ] 5.2 Write property tests for non-disclosure, indistinguishable failures, and invalid input
    - **Property 3: Accepted responses never disclose account existence** (Milestone 1 part: sign-up and resend; a counting fake hasher and a counting token factory assert equal work for new, unverified, and verified emails; no wall-clock timing)
    - **Validates: Requirements 3.2, 3.7, 3.11**
    - **Property 4: Sign-in failures are indistinguishable**
    - **Validates: Requirements 4.2**
    - **Property 12: Invalid input and invalid links leave state unchanged** (Milestone 1 part; includes a 255-character email and an unknown extra field, both returning the same `fields` shape)
    - **Validates: Requirements 3.3, 3.6, 3.10**
  - [ ] 5.3 Write property tests for the sign-up flow, secret hygiene, and rotation, plus example tests
    - **Property 2: Sign-up, verification, and sign-in compose**
    - **Validates: Requirements 3.1, 3.5, 3.9, 4.1, 7.3**
    - **Property 9: Failures and logs never leak secrets or set sessions**
    - **Validates: Requirements 2.10, 4.8, 16.6**
    - **Property 20: Credential-establishing routes rotate the session and the CSRF token** (sign-in part)
    - **Validates: Requirements 4.9**
    - Example tests: mail failure maps to 502 and keeps the user unverified, unverified sign-in is 403, rate limit boundary with `Test_Clock` returns 429 with `Retry-After` and reopens after the window
    - _Requirements: 3.4, 3.8, 4.3, 4.4_

- [ ] 6. Quiz and project read routes
  - [ ] 6.1 Implement the read use cases and the text source
    - `GetQuizUseCase` and `GetProjectUseCase` in `edu_it_application/learning/`, returning views with no correctness flag; `Content_StoreTextSource` reads the synced content store through a structural Protocol (as `Content_LabCatalog` does, so `edu-it-infrastructure` does not import `edu-it-content`) and falls back to English per field; unknown or archived items raise `NotFoundError`
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 10.7_
  - [ ] 6.2 Implement the Content_Router
    - `content_routes.py` with `GET /quizzes/{stable_id}` and `GET /projects/{stable_id}`, a `locale` query parameter (`fr` or `en`), frozen response models
    - _Requirements: 10.1, 10.3, 10.5_
  - [ ] 6.3 Write property test for content reads
    - **Property 14: Content reads are complete, safe, and localized**
    - **Validates: Requirements 10.1, 10.2, 10.3, 10.4, 10.7**

- [ ] 7. Catalog persistence, project reviewer, and the dependency-graph check
  - [ ] 7.1 Implement `Pending_ProjectReviewer`
    - Returns `None` for every submission; production composition wires it and never wires `Fake_ProjectReviewer`
    - _Requirements: 11.1, 11.2_
  - [ ] 7.2 Implement `Postgres_ContentStore` in `packages/content` and the sync database option
    - First extend `docs/adr/0001-versions.md` so the `sqlalchemy` row (2.1.6) lists `packages/content` as a consumer, then add `sqlalchemy==2.1.6` to `packages/content/pyproject.toml` and an optional extra `postgres` with `psycopg[binary]==3.3.6`; update the manifest comment that says the package depends on Pydantic only; run `tools/tests/test_labs_adr_pins.py`
    - `edu_it_content/postgres_store.py`: `Postgres_ContentStore(engine)` implements the `ContentStore` port, imports no other EDU-IT package, reflects the Relational_Catalog tables from the migrated database with `MetaData.reflect(only=...)`, declares no table, and raises an error naming the missing table when migrations are absent; it writes every table including quiz, question, option, project, rubric, rubric criterion, and prerequisite rows
    - Review, Golden_Set, and orphan state follow design decision 10 (no new table)
    - Add `--database-url` to `edu-it-content sync` (the CLI builds the engine); reuse `sync_content` unchanged; keep refusing unreviewed `ai` items
    - _Requirements: 12.1, 12.2, 12.5, 12.7, 12.8, 16.4_
  - [ ] 7.3 Write the store contract test and update the perf seeding
    - In `apps/api/tests` (the only package depending on both content and infrastructure), create the schema from the infrastructure metadata, then run the shared `ContentStore` contract suite against `In_Memory_ContentStore` and `Postgres_ContentStore` (SQLite, and PostgreSQL when `DATABASE_URL` is set)
    - **Property 18: Content store contract and idempotent sync**
    - **Validates: Requirements 12.1, 12.3, 12.4**
    - Make `tools/perf/stack.py` `seed_database` delegate to the same sync, keep it idempotent, and update the module docstring to say the sign-up and sign-in routes exist and the harness mints learners directly
    - _Requirements: 12.3, 12.4, 12.6, 16.3_
  - [ ] 7.4 Add the package dependency-graph test
    - `tools/tests/test_package_dependency_graph.py` parses every workspace `pyproject.toml` and asserts: the EDU-IT dependency graph is acyclic; `edu-it-domain` and `edu-it-content` list no `edu-it-*` dependency; `edu-it-application` and `edu-it-infrastructure` list only `edu-it-domain`; no `packages/*` project depends on an `apps/*` project; every third-party dependency of `edu-it-content` appears in `docs/adr/0001-versions.md` at the same exact version
    - Run `tools/check_domain_boundary.py` next to it; it inspects only domain imports and is not extended
    - _Requirements: 16.1, 16.4, 16.7_

- [ ] 8. Composition and serving
  - [ ] 8.1 Implement `build_adapters`
    - `build_adapters(settings, *, fakes)` in `composition.py` returns one frozen `ApiAdapters` dataclass. With `fakes=False` it builds the PostgreSQL repositories, `Redis_IdempotencyStore`, the Redis session store, `System_Clock`, `Uuid_IdGenerator`, `Pending_ProjectReviewer`, `Scrypt_PasswordHasher`, `Web_LinkBuilder`, the configured Mailer, and the PostgreSQL credential and verification-link adapters. With `fakes=True` it uses exactly the in-memory fakes listed in the design's Composition section and builds no Redis or PostgreSQL client
    - _Requirements: 1.2, 1.8, 9.1, 9.2, 9.3, 11.2_
  - [ ] 8.2 Implement `build_api_routers` and wire the shared resolver
    - `build_api_routers(adapters, *, content_root, config)` builds `AccountServices` (Milestone 1 use cases), `LearningServices`, `ListServices`, the Content_Router, and the Lab_Router, and passes the same `SessionUserResolver` instance to the lab, learning, list, content, and account routers; it reads no environment variable
    - _Requirements: 1.1, 1.3, 9.1, 9.2_
  - [ ] 8.3 Update `create_app`: signature, CORS order, and startup refusal
    - `create_app` accepts `v1_routers: Sequence[APIRouter]` and keeps `learning_router` and `lab_router` as aliases; middleware order is CORS outermost, then request logging, then CSRF; `install_cors` is called with `ROUTE_TABLE`
    - Fail startup, logging the adapter name and never a secret, when a required adapter cannot be built or `WEB_BASE_URL` is not an absolute `http` or `https` URL
    - _Requirements: 1.8, 2.8, 8.6_
  - [ ] 8.4 Update `serve.py` and the OpenAPI export
    - `build_served_app` calls the new builders; `openapi_export.py` builds the V1_App from the fake-backed router set (forcing conditional routes on when they exist) and contacts no datastore; regenerate `apps/web/openapi/v1.json` so it lists every entry in `ROUTE_TABLE`
    - _Requirements: 1.1, 1.5, 1.6, 1.7, 14.1_
  - [ ] 8.5 Write property tests over the mounted app
    - **Property 5: Authenticated routes reject missing or invalid sessions**
    - **Validates: Requirements 1.3, 1.4, 4.7**
    - **Property 7: CSRF issuance and enforcement**
    - **Validates: Requirements 2.4, 2.5, 2.6**
    - **Property 15: Repeated mutations are idempotent**
    - **Validates: Requirements 4.5, 9.4, 9.6**
    - **Property 16: Project submissions are pending or rejected whole**
    - **Validates: Requirements 11.1, 11.3, 11.4**
    - **Property 17: Learning responses expose stable identifiers only**
    - **Validates: Requirements 9.8**
  - [ ] 8.6 Write integration tests for the mounted app
    - OpenAPI document paths and methods equal `ROUTE_TABLE`; the lab entries of `ROUTE_TABLE` equal the routes of the real `Lab_Router`; export runs with sockets forbidden; `/health` and `/ready` unchanged; startup refusal for a bad `WEB_BASE_URL` and for an adapter that cannot be built; in-process HTTP journey (sign-up, verify from the console mail, sign-in, enroll, quiz attempt, project submission, sign-out) on the Served_App built with fakes; a `mint_learner` cookie authenticates against the Served_App; after the worker records a passed Check, `GET /progress` shows the completion
    - _Requirements: 1.5, 1.6, 1.7, 1.8, 8.6, 9.9, 15.2, 15.3, 16.1, 16.2, 17.7_

- [ ] 9. Record the accepted risks and the mail-failure convention
  - [ ] 9.1 Add the accepted risks to `docs/RISKS.md`
    - Add entries `R-09` (abuse of the mail-sending endpoints sign-up, resend, and reset request, because Requirement 3 adds no rate limit and the spec adds no number; note that reusing the existing `SIGN_IN_RATE_LIMIT_*` values was considered and rejected because it would change a business rule), `R-10` (residual account-existence timing from mail delivery latency, which the equal-work changes do not cover), and `R-11` (account deletion without fresh authentication, the open decision 9 of the design, default behavior `confirm: true` only), each in the file's existing entry format with Statement, Likelihood / Impact, Recommendation, and a named follow-up Owner / Resolution path; add the three rows to the Section 1 ranking table
    - _Requirements: 16.8_
  - [ ] 9.2 Record the mail-failure mapping in `docs/API-CONVENTIONS.md`
    - Add a short entry stating that a failed mail delivery maps to status 502 with the problem slug `mail-delivery-failed` and a translated detail, so other routes that send mail stay consistent
    - _Requirements: 16.9_

- [ ] 10. Checkpoint: Milestone 1 API complete
  - Ensure all tests pass, ask the user if questions arise.
  - Run the regression gates: foundations, learning-core, and hands-on-labs suites, the Worker_Service tests, `tools/tests/test_package_dependency_graph.py`, and `tools/check_domain_boundary.py`.

- [ ] 11. Web integration for Milestone 1
  - [ ] 11.1 Add the client factory with credentials and CSRF, and regenerate the client
    - One `createApiClient({ cookie? })` factory used by `api-client.ts`, `learning-api.ts`, `practice-api.ts`, `lab-api.ts`, `lesson-api.ts`, and `session.ts`; in the browser it sets `credentials: 'include'`, reads the `csrf_token` cookie, requests `GET /auth/csrf` once when absent, and sends `X-CSRF-Token` on unsafe calls; on the server it forwards the request cookie and generates a fresh pair; run `npm run generate:client` and commit nothing under `src/generated` by hand
    - **Property 19: The generated client sends the CSRF pair and credentials**
    - **Validates: Requirements 14.3**
    - _Requirements: 14.2, 14.3, 14.4_
  - [ ] 11.2 Connect the Milestone 1 pages and add messages
    - Fix `AUTH_PATHS` for sign-up, sign-in, and verify-email resend; add `verifyEmail(token)`; in `api-client.ts` read the wire member `redirect_to` and return it as the internal `redirectTo` (the other web files keep the internal name); sign-up redirects to `/{locale}/verify-email`; verify-email reads `token` and shows success or the invalid-or-expired state with the resend form; sign-in navigates to `/{locale}{redirectTo}`; add every string to the fr and en catalogs; update `enroll-action.ts` to use the server client
    - _Requirements: 14.5, 14.6, 14.8, 14.10, 14.11, 14.13_
  - [ ] 11.3 Write web tests
    - Unit and component tests for each changed page and adapter, including the `redirect_to` to `redirectTo` mapping and the locale prefix; mock-API Playwright updates so `labs-playwright`, `labs-axe`, and the learner accessibility specs still pass for the changed pages in `/fr` and `/en`; the Key_Parity_Test
    - _Requirements: 14.5, 14.6, 14.8, 14.11, 14.13, 17.6_

- [ ] 12. Real-stack bootstrap and Seed_Lab test
  - [ ] 12.1 Implement `tools/e2e_stack.py` and its dry-run test
    - Subcommands `up`, `down`, and `env`; `up` starts PostgreSQL and Redis, runs migrations, runs `edu-it-content sync --database-url`, builds the Lab_Images with `docker buildx bake` against `lab-images/docker-bake.hcl` and passes `ALPINE_MIRROR` through when it is set, starts worker, API (`SANDBOX_PROVIDER=docker EDU_IT_ENV=development MAILER_BACKEND=console`, output tee'd to `E2E_MAIL_LOG`), and web; the script does not run, install, or require `trivy`; `--dry-run` prints the plan; the test asserts the plan, the environment, the `ALPINE_MIRROR` pass-through, and that no `trivy` step exists
    - _Requirements: 17.1, 17.4, 17.5_
  - [ ] 12.2 Write the real-stack Seed_Lab test
    - `apps/web/e2e/real/seed-labs.e2e.ts` signs up through the web app, passes the Checks of both Seed_Labs, and asserts translated feedback in `/fr` and in `/en`; no mock API; picked up by the existing `npm run test:e2e:real` glob
    - _Requirements: 17.3, 17.4, 17.5_

- [ ] 13. Checkpoint: ready for the real stack
  - Ensure all tests pass, ask the user if questions arise.
  - Run the regression gates: every existing web unit, property, and accessibility test, and the foundations, learning-core, and API suites.

- [ ] 14. Milestone 1 gate: rerun the labs verification suite and hand back
  - With the Real_Stack up (`tools/e2e_stack.py up`) and `E2E_MAIL_LOG` set, run `python tools/run_verification.py --suite labs` and confirm that `labs-playwright-real` ran and passed, covering the Journey_Test (hands-on-labs Requirement 32.1) and the real-stack Seed_Lab test (Requirement 32.2); report the status of every other labs check
  - The image checks of that suite need `trivy` on `PATH` at the ADR 0001 pin (`0.76.1`); a container-based `trivy` invocation is an acceptable way to provide it. If `trivy` is missing, report that check as blocked by the environment and do not install a different version
  - Also run the foundations, learning-core, and API test suites and report their outcome
  - If `labs-playwright-real` fails or cannot run, HALT, report the failing check, and leave this task incomplete; do not write `docs/reports/phase-3.md`
  - If every labs check passed, state that hands-on-labs task 24 can now be re-dispatched; Milestone 2 may start only after this statement
  - _Requirements: 17.2, 17.3_

### Milestone 2 (follow-up, after the Milestone 1 gate)

- [ ] 15. Persistence for reset and OAuth links (revision `0004`)
  - [ ] 15.1 Add Alembic revision `0004_accounts_oauth_reset` and update `metadata.py`
    - Create `reset_link` and `pending_oauth_link` as specified in the design, with unique `token_digest`; downgrade drops only these two tables
    - _Requirements: 7.9, 7.11_
  - [ ] 15.2 Implement the remaining PostgreSQL account adapters
    - `Postgres_ResetLinkRepository`, `Postgres_PendingOAuthLinkRepository`, and `Postgres_OAuthLinkRepository` (backed by the existing `oauth_link` table) in `persistence/account_repositories.py`; digest-only storage and conditional `UPDATE` for `mark_consumed`
    - _Requirements: 7.6, 7.8, 7.11, 7.12_
  - [ ] 15.3 Write contract and migration tests
    - Extend the parametrized suites to the three new adapters; migration `0004` up then down; the concurrent case stays PostgreSQL-only
    - **Property 11: Link tokens are stored as digests and consumed once** (extended to reset and pending OAuth links)
    - **Validates: Requirements 7.6, 7.8, 7.9, 7.12**
  - [ ] 15.4 Extend GDPR coverage and the data-model document for `reset_link` and `pending_oauth_link`
    - Add the two tables to the personal-data gateway export, deletion, and retention paths, and to `docs/DATA-MODEL.md` Section 13 in the same change; extend the GDPR coverage test
    - _Requirements: 7.7_

- [ ] 16. Password reset
  - [ ] 16.1 Add the reset URL and the reset-request changes
    - Add `reset_url` to `Web_LinkBuilder`; `RequestPasswordResetUseCase` takes `link_builder` and, for an unregistered email, calls `token_factory()` once and discards the result; the reset mail puts the URL on a single line in fr and en; update the reset fixtures and tests
    - _Requirements: 5.6, 8.2, 8.3, 8.4, 8.5_
  - [ ] 16.2 Add the Milestone 2 reset problem types
    - In `errors/accounts.py`: reset link invalid (400, `offer_restart`), reset password policy (422), and `PasswordResetMailDeliveryError` (502, `mail-delivery-failed`)
    - _Requirements: 5.2, 5.4, 5.5_
  - [ ] 16.3 Implement the reset routes
    - In `accounts.py` add `POST /auth/password-reset` and `POST /auth/password-reset/complete`; completion sets the Session_Cookie for the new Session as in Requirement 2.1 and replaces the `csrf_token` cookie with a fresh token; do not add session revocation, because `CompletePasswordResetUseCase` already revokes every prior Session and establishes one new Session (Foundations 9.5, 28.1, 28.2); append both entries to `ROUTE_TABLE` and wire `request_reset` and `complete_reset` into `AccountServices` and `build_api_routers`
    - _Requirements: 5.1, 5.3, 5.4, 5.5_
  - [ ] 16.4 Write the reset tests
    - **Property 3: Accepted responses never disclose account existence** (extended to reset request with the counting token factory)
    - **Validates: Requirements 5.1, 5.6**
    - **Property 10: Account link URLs round trip** (extended to the reset URL)
    - **Validates: Requirements 8.2, 8.4, 8.5**
    - **Property 12: Invalid input and invalid links leave state unchanged** (extended to reset tokens and weak passwords)
    - **Validates: Requirements 5.4, 5.5**
    - **Property 20: Credential-establishing routes rotate the session and the CSRF token** (reset completion, and every other Session no longer authenticates)
    - **Validates: Requirements 5.3**
    - Example tests: reset mail failure maps to 502
    - _Requirements: 5.2_

- [ ] 17. GitHub OAuth with PKCE
  - [ ] 17.1 Implement the port, the PKCE helpers, the adapter, and the fake
    - `ports/github_oauth.py` (`GitHubOAuthClient` with `authorize_url(state, code_challenge)` and `exchange(code, code_verifier)`, and `GitHubIdentity`); `OAUTH_STATE_TTL` (10 minutes) in `config.py` and its `__all__`; `new_pkce_verifier` and `pkce_challenge` in `oauth.py`; `Urllib_GitHubOAuthClient` (10 s timeout, `urllib.request` only); `Fake_GitHubOAuthClient`, which asserts that the received `code_verifier` hashes to the `code_challenge` it was given; extend `CompositionSettings` with `github_client_id`, `github_client_secret`, `github_callback_url`; add the variables to `.env.example`
    - _Requirements: 6.1, 6.2, 6.8_
  - [ ] 17.2 Implement conditional mounting and the startup checks
    - In composition: when none of the three GitHub variables is set, do not mount the OAuth routes (requests under `/v1/auth/github` get the generic 404 Problem_Details); when only some are set, refuse to start and log the missing variable names, never values; the OpenAPI export forces the OAuth routes on with `Fake_GitHubOAuthClient`
    - _Requirements: 1.5, 1.9, 1.10_
  - [ ] 17.3 Implement the OAuth routes
    - `oauth.py` with `GET /auth/github/authorize` (state and verifier in the `edu_it_oauth_state` cookie: `HttpOnly`, `SameSite=Lax`, `Path=/v1/auth/github`, `Secure` in production, `Max-Age` `OAUTH_STATE_TTL`; 302 with `code_challenge` and `code_challenge_method=S256`), `GET /auth/github/callback` (constant-time state check, exchange with the stored verifier, cookie deleted in every outcome, cookies and redirect per outcome), `POST /auth/github/confirm`, `POST /auth/github/decline`; a new Session and a fresh `csrf_token` on `SIGNED_IN` and `LINKED`; append the four entries to `ROUTE_TABLE` marked conditional
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7, 6.10_
  - [ ] 17.4 Write the OAuth tests
    - **Property 13: OAuth state and PKCE derivation**
    - **Validates: Requirements 6.1, 6.2**
    - **Property 20: Credential-establishing routes rotate the session and the CSRF token** (OAuth sign-in and confirmation)
    - **Validates: Requirements 6.3, 6.6, 6.10**
    - Example tests with the fake client and a `no_network` guard: new verified email signs in, matching existing email goes to pending confirmation without a Session, unverified email links nothing, denied or failed flow creates nothing, confirm links and signs in, decline links nothing, unconfigured start omits the routes, partial configuration refuses to start
    - _Requirements: 1.9, 1.10, 6.4, 6.5, 6.7, 6.9_

- [ ] 18. Account export, download, and deletion
  - [ ] 18.1 Wire Object_Storage
    - Extend `CompositionSettings` with `object_storage_backend` and `object_storage_root`; `memory` builds `In_Memory_ObjectStorage`, `filesystem` builds the existing `FileSystem_ObjectStorage` at `OBJECT_STORAGE_ROOT`; any other value refuses to start naming the adapter; there is no `export_storage_dir`; update `.env.example` (backend list and `OBJECT_STORAGE_ROOT=`)
    - _Requirements: 13.6_
  - [ ] 18.2 Implement the export, download, and deletion routes
    - In `accounts.py` add `POST /account/export` (200 `{download_path, size_bytes}`), `GET /account/export/download` (reads `exports/{authenticated user}.json` from Object_Storage, key never taken from the request, JSON attachment, 404 `export-not-found` when absent), and `POST /account/deletion` (requires `confirm: true`, expires the cookie, behavior otherwise unchanged; fresh authentication is the open decision 9 and is not implemented); add the `deletion-incomplete` and `export-not-found` problem types; append the three entries to `ROUTE_TABLE`
    - _Requirements: 13.1, 13.2, 13.3, 13.4, 13.5_
  - [ ] 18.3 Write the account data tests
    - Example tests: export returns a download path and the download returns the same document, another user's key is unreachable, deletion without confirmation changes nothing, deletion failure keeps all data, startup refusal for an unsupported `OBJECT_STORAGE_BACKEND`
    - _Requirements: 13.1, 13.3, 13.4, 13.5, 13.6_

- [ ] 19. Web integration for Milestone 2
  - [ ] 19.1 Connect the reset and OAuth pages
    - Add `completePasswordReset(token, newPassword)` and the reset path to `AUTH_PATHS`; the reset-password page switches between the request form and the new-password form; the sign-in page shows the GitHub link only when `NEXT_PUBLIC_GITHUB_SIGN_IN_ENABLED=true` (add the variable, default `false`, to `.env.example`); add `auth/github/confirm/page.tsx` with confirm and decline buttons; regenerate the client; add every string to the fr and en catalogs
    - _Requirements: 14.7, 14.9, 14.11, 14.12_
  - [ ] 19.2 Write web tests
    - Unit and component tests for the new pages, mock-API Playwright updates so `labs-axe` and the learner accessibility specs cover the new pages in `/fr` and `/en`, and the Key_Parity_Test
    - _Requirements: 14.7, 14.9, 14.11, 14.12_

- [ ] 20. Checkpoint: Milestone 2 complete
  - Ensure all tests pass, ask the user if questions arise.
  - Run the regression gates: every existing web unit, property, and accessibility test, the foundations, learning-core, hands-on-labs, and API suites, the Worker_Service tests, `tools/tests/test_package_dependency_graph.py`, and `tools/check_domain_boundary.py`.

- [ ] 21. Final task: Milestone 2 regression run
  - With the Real_Stack up and `E2E_MAIL_LOG` set, rerun `python tools/run_verification.py --suite labs` and the web accessibility suite after the Milestone 2 changes; confirm `labs-playwright-real` still passes and report the status of every other labs check (the same `trivy` note as task 14 applies)
  - Also run the foundations, learning-core, and API test suites and report their outcome
  - If any check regresses, HALT, report the failing check, and leave this task incomplete; do not write `docs/reports/phase-3.md`
  - _Requirements: 17.2, 17.3_

## Notes

- Test sub-tasks are not postfixed with `*`, matching the learning-core and hands-on-labs plans: the final gates and the traceability suites treat every mapped test as an acceptance check, so none may be skipped.
- Each task references specific requirements; property tasks also name their Property number (1 to 20) from design.md.
- Milestone 1 ends at the gate task 14, which states that hands-on-labs task 24 can be re-dispatched. Milestone 2 tasks start only after that statement. Every Milestone 1 task is in an earlier dependency wave than any Milestone 2 task.
- This plan, not the requirements, owns the final verification process. The gate task 14 and the final task 21 report results; they write no report. Hands-on-labs task 24 writes `docs/reports/phase-3.md` after it is re-dispatched.
- Regression gates (the existing suites and the web accessibility tests keep passing) run at the checkpoint tasks 10, 13, and 20 and in the tasks 14 and 21. They are described in the design's Testing Strategy and are not system requirements.
- The image checks of `run_verification --suite labs` need `trivy` on `PATH` at the ADR 0001 pin (`0.76.1`). A container-based `trivy` invocation is acceptable. `tools/e2e_stack.py` neither installs nor invokes it.
- Traceability to earlier specs: Requirements 3 to 6 and 13 satisfy Foundations 7 to 10, 12, 14, 24 to 26; Requirements 9 to 12 satisfy Learning core 17, 18, 20, 22 to 24, 28; Requirement 17 and the gate tasks satisfy Hands-on labs 32 and unblock its task 24. The full table is in design.md.
- Corrections to the problem statement: `POST /enrollments` and `POST /project-submissions` already exist in `learning.py`. They are mounted, not built. The genuinely missing pieces are listed in the requirements' Investigation findings (M1 to M13).
- Existing use cases change only in their constructor arguments (credential store and link builder) and in the decoy calls that make the work equal across email classes. No rule, threshold, or outcome changes.
- Open decisions that the tasks do not settle: fresh authentication for account deletion (design decision 9, recorded as `R-11`), and persistence of Golden_Set and orphan state in `Postgres_ContentStore` (design decision 10).
- Out of scope, listed as Non-goals in the requirements: `GET /reviews/schedule`, placement and final-assessment routes, a human or AI project reviewer, the Docker provider, lab domain rules, Check bundles, a mail-endpoint rate limit, asynchronous mail delivery, and an S3 or Azure Blob adapter.
- Checkpoint tasks (10, 13, 20) and the gate and final tasks (14, 21) are outside the dependency graph.
- The workflow produces planning artifacts only; begin execution from the task list once reviewed.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2", "2.1", "4.2", "7.1", "9.1", "9.2"] },
    { "id": 1, "tasks": ["1.3", "2.4", "3.1", "4.1", "6.1", "7.2"] },
    { "id": 2, "tasks": ["1.4", "2.2", "3.2", "3.3", "4.3", "5.1", "6.2", "7.3", "7.4"] },
    { "id": 3, "tasks": ["2.3", "5.2", "5.3", "6.3", "8.1"] },
    { "id": 4, "tasks": ["8.2"] },
    { "id": 5, "tasks": ["8.3"] },
    { "id": 6, "tasks": ["8.4"] },
    { "id": 7, "tasks": ["8.5", "8.6", "11.1", "12.1"] },
    { "id": 8, "tasks": ["11.2"] },
    { "id": 9, "tasks": ["11.3", "12.2"] },
    { "id": 10, "tasks": ["15.1", "16.1", "17.1"] },
    { "id": 11, "tasks": ["15.2", "15.4", "16.2", "18.1"] },
    { "id": 12, "tasks": ["15.3", "16.3", "17.2"] },
    { "id": 13, "tasks": ["16.4", "17.3"] },
    { "id": 14, "tasks": ["17.4", "18.2"] },
    { "id": 15, "tasks": ["18.3", "19.1"] },
    { "id": 16, "tasks": ["19.2"] }
  ]
}
```
