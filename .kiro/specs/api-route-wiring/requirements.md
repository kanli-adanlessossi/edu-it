# Requirements Document

## Introduction

This spec closes a wiring gap. The served API (`apps/api/edu_it_api/serve.py`) builds the app with
`create_app(lab_router=labs.router)` only, so at runtime `/v1` exposes the lab routes plus
`/health` and `/ready`. The learning routers exist and are tested but are never mounted. No HTTP
route exists for sign-up, email verification, sign-in, sign-out, password reset, or OAuth, although
the use cases exist in `packages/application/edu_it_application`. The foundations tasks are marked
complete, but they never received HTTP routes. As a result the real-stack acceptance check
`labs-playwright-real` (hands-on-labs Requirement 32.1, `apps/web/e2e/real/learner-journey.e2e.ts`)
fails and hands-on-labs task 24 is halted.

This spec exposes the **existing** application use cases as versioned `/v1` routes, mounts every
router in the served app, connects the web pages to those routes through the generated client, and
proves the result with the real-stack journey. It adds **no new business rule**: every rule is
already implemented in `packages/domain` and `packages/application`. It does add the
infrastructure and boundary pieces that those use cases need in order to run for real, because
investigation showed that several are genuinely missing (see "Investigation findings").

Authoritative rules stay in steering and are referenced, not restated: authentication and privacy
(#[[file:.kiro/steering/auth-privacy.md]]), API conventions
(#[[file:.kiro/steering/api-conventions.md]]), domain rules
(#[[file:.kiro/steering/domain-rules.md]]), labs (#[[file:.kiro/steering/labs.md]]), layering
(#[[file:.kiro/steering/structure.md]]), technology (#[[file:.kiro/steering/tech.md]]), quality
(#[[file:.kiro/steering/quality.md]]), and process (#[[file:.kiro/steering/working-agreements.md]]).
Pinned versions are in `docs/adr/0001-versions.md`; session storage is ADR 0004.

### Milestones

The work is split into two milestones inside this one spec. Milestone 1 is the shortest path that
unblocks hands-on-labs task 24. Milestone 2 follows only after the Milestone 1 gate has passed.

| Milestone | Delivers | Requirements |
|---|---|---|
| Milestone 1 (unblock) | CSRF, CORS, and cookies; sign-up, verify-email and resend, sign-in, sign-out, session probe; `credential` and `verification_link` persistence; the verification link builder; learning and list mounting; quiz and project read routes; pending project reviewer; PostgreSQL catalog sync; OpenAPI export; the web client and the pages these flows need; the `e2e_stack` bootstrap; the real-stack Seed_Lab test; the gate that reruns `run_verification --suite labs` | 1 (except 1.9, 1.10), 2, 3, 4, 7 (except 7.11, 7.12), 8 (except 8.2), 9, 10, 11, 12, 14 (except 14.7, 14.9, 14.12), 15, 16, 17 |
| Milestone 2 (follow-up) | Password reset routes and pages; GitHub OAuth; account export and deletion; the `reset_link` and `pending_oauth_link` tables in a later Alembic revision; their properties and tests | 1.9, 1.10, 5, 6, 7.11, 7.12, 8.2, 13, 14.7, 14.9, 14.12 |

Each requirement heading carries its milestone. In a requirement that spans both milestones, each
criterion states its milestone in a trailing parenthesis.

### Non-goals

These items are out of scope for the whole spec. They are not system requirements.

- The spec adds no new business rule, threshold, or number to the domain beyond those already in
  `packages/domain/edu_it_domain/config.py`. In particular it adds no rate limit for the mail-sending
  endpoints. Reusing the existing `SIGN_IN_RATE_LIMIT_*` values was considered and rejected because
  it would change a business rule. The resulting risk is recorded in `docs/RISKS.md` (Requirement 16.8).
- The spec does not implement `GET /reviews/schedule`; the dashboard keeps tolerating its absence.
- The spec does not implement placement or final-assessment routes.
- The spec does not change the Docker Sandbox provider, the lab domain rules, or the Check bundles.
- The spec does not create `docs/reports/phase-3.md`; hands-on-labs task 24 writes it after the
  Milestone 1 gate passes.
- The spec does not move mail delivery off the request path and does not add an S3 or Azure Blob
  adapter.

### Grounding references

- Foundations: `.kiro/specs/foundations/requirements.md` (Requirements 5 and 7 to 14, 16, 17, 19, 24,
  25, 26, 28)
- Learning core: `.kiro/specs/learning-core/requirements.md` (Requirements 17, 18, 19, 20, 22, 23,
  24, 28)
- Hands-on labs: `.kiro/specs/hands-on-labs/requirements.md` (Requirements 12, 32, 35) and its task 24

### Investigation findings

Every finding below was verified by reading the repository.

**Use case behind each route.** This table maps existing use cases to routes only. It is not the
normative route list: the normative list is the Route_Table in the design, which also contains
`GET /auth/csrf`, `GET /quizzes/{stable_id}`, `GET /projects/{stable_id}`, and every lab route.

| Route (under `/v1`) | Existing use case | Satisfies |
|---|---|---|
| `POST /auth/sign-up` | `SignUpUseCase` (`sign_up.py`) | Foundations 7.1 to 7.3, 7.6 to 7.9, 24.1 |
| `POST /auth/verify-email` | `VerifyEmailUseCase` (`verify_email.py`) | Foundations 7.4, 7.5, 25.1, 25.2 |
| `POST /auth/verify-email/resend` | `ResendVerificationUseCase` (`resend_verification.py`) | Foundations 24.3, 25.2 |
| `POST /auth/sign-in` | `AuthenticateUseCase` (`authenticate.py`) | Foundations 8.1, 8.3, 8.4, 12 |
| `POST /auth/sign-out` | `SignOutUseCase` (`authenticate.py`) | Foundations 8.2 |
| `GET /auth/session` | `AuthenticateSessionUseCase` (`authenticate.py`) | Foundations 8.5 |
| `POST /auth/password-reset` | `RequestPasswordResetUseCase` (`reset_password.py`) | Foundations 9.1, 9.6, 24.2 |
| `POST /auth/password-reset/complete` | `CompletePasswordResetUseCase` (`reset_password.py`) | Foundations 9.2 to 9.5, 25.3 |
| `GET /auth/github/authorize`, `GET /auth/github/callback` | `CompleteOAuthCallbackUseCase` (`oauth_sign_in.py`) | Foundations 10.1, 10.3 to 10.5, 26 |
| `POST /auth/github/confirm`, `POST /auth/github/decline` | `ConfirmOAuthLinkUseCase`, `DeclineOAuthLinkUseCase` | Foundations 10.2, 10.6, 10.7, 26 |
| `POST /account/export`, `POST /account/deletion` | `ExportUserDataUseCase`, `DeleteAccountUseCase` (`gdpr.py`) | Foundations 14.1 to 14.4, Learning core 22 |
| `POST /enrollments` | `EnrollUseCase` (`learning/progress.py`), route in `learning.py` | Learning core 17.1 |
| `GET /enrollments` | `ListEnrollmentsUseCase`, route in `learning_lists.py` | Learning core 17.5 |
| `GET /progress`, `GET /lessons/{stable_id}` | `GetProgressUseCase`, `GetLessonUseCase`, route in `learning.py` | Learning core 17.2 to 17.4 |
| `POST /quiz-attempts`, `GET /quiz-attempts` | `AttemptQuizUseCase`, `ListQuizAttemptsUseCase` | Learning core 18.1, 18.2, 23, 24 |
| `POST /hints` | `RevealHintUseCase` | Learning core 18.3 |
| `GET /reviews`, `POST /reviews` | `StartReviewUseCase`, `SubmitReviewUseCase` | Learning core 18.4, 28 |
| `POST /project-submissions` | `SubmitProjectUseCase` (`learning/submit_project.py`), route in `learning.py` | Learning core 18.5, 20 |
| `/labs/{lab_id}`, `/lab-sessions...`, terminal WebSocket | Lab use cases, already mounted by `composition.build_lab_router` | Hands-on labs (unchanged) |

**Corrections to the problem statement.** Enrollment creation and project submission are **not**
missing use cases or routes. `POST /enrollments` and `POST /project-submissions` are already
implemented in `learning.py` with `EnrollUseCase` and `SubmitProjectUseCase`; they are only
unmounted. No HTTP route exists, and no wiring exists, for the account use cases.

**Genuinely missing pieces** (each is covered by a requirement below):

| # | Missing piece | Evidence | Requirement | Milestone |
|---|---|---|---|---|
| M1 | A production `CredentialStore`, a password hasher, and credential storage at sign-up | Only `In_Memory_CredentialStore` exists. `SignUpUseCase` computes the hash and discards it (`_ = self._password_hasher(...)`), so a signed-up learner could never sign in. `test_e2e_authentication.py` calls `set_password` by hand to compensate. | 7 | 1 |
| M2 | PostgreSQL storage for verification links, reset links, OAuth links, pending OAuth links, and credentials | `persistence/metadata.py` has `user`, `session`, `oauth_link`, `role_assignment` only. Only in-memory fakes implement the link repository ports. | 7 | 1 for `credential` and `verification_link`, 2 for the rest |
| M3 | A web URL inside account emails, and web pages that consume the tokens | Sign-up mail body carries only the raw token, but the journey test expects an `https?://...verif...` URL in the mail log. `verify-email` has only a resend form, `reset-password` has no completion step, and no page handles OAuth. | 8, 14 | 1 for verification, 2 for reset and OAuth |
| M4 | RFC 9457 problem types for account errors | `errors/catalog.py` has only generic types plus CSRF and validation. Sign-in, link, reset, and OAuth failures have no mapping. | 3, 4, 5, 6 | 1 for sign-up, verify, sign-in, 2 for reset and OAuth |
| M5 | Read use cases for quiz content and project content | The web calls `GET /quizzes/{id}` and `GET /projects/{id}`. The API defines neither. `CatalogRepository` exposes structure only, with no localized text. | 10 | 1 |
| M6 | A non-fake `ProjectReviewer` adapter | Only `Fake_ProjectReviewer` exists. | 11 | 1 |
| M7 | A relational catalog in PostgreSQL | `edu-it content sync` writes an in-memory store (`cli.py`, `lab_runtime.py`). `tools/perf/stack.py` seeds only track, module, lesson, concept, lab, lab_task, lab_check. Quiz, question, option, project, rubric, and prerequisite rows are never written, yet quiz attempts and project submissions need them. | 12 | 1 |
| M8 | CSRF token issuance and cross-origin cookie handling | The CSRF middleware exists, but no code issues the `csrf_token` cookie. The web never sends `X-CSRF-Token`. The API has no CORS middleware although the web (port 3000) calls the API (port 8000) from the browser. | 2 | 1 |
| M9 | A GitHub OAuth handshake adapter | The use case takes an already completed `OAuthCallback`. Nothing performs the redirect or the code exchange. | 6 | 2 |
| M10 | A populated OpenAPI document | `openapi_export.py` calls `create_app()` with no routers, so `apps/web/openapi/v1.json` has `paths: {}` and the generated client has no typed operations. | 1, 14 | 1 |
| M11 | A real-stack test for Seed_Labs (hands-on-labs 32.2) | `seed-labs.spec.ts` runs against the mock API. Only `learner-journey.e2e.ts` runs on the real stack. | 17 | 1 |
| M12 | A scripted real-stack bootstrap | `playwright.e2e.config.ts` documents the stack in comments only. | 17 | 1 |
| M13 | A download path for the account export | `ExportUserDataUseCase` writes the export to the `ObjectStorage` port and returns a storage key. No route serves it, and no composition wires an `ObjectStorage` adapter (`FileSystem_ObjectStorage` exists but is unwired). | 13 | 2 |

**Configuration mismatch.** `.env.example` sets `NEXT_PUBLIC_API_BASE_URL=http://localhost:8000`,
while every web adapter defaults to `http://localhost:8000/v1`. Requirement 14 resolves this.

**Other verified facts.** `apps/api/edu_it_api/labs.py` defines a `PUT` route for saving lab files,
so the CORS method list cannot be limited to `GET`, `POST`, and `DELETE`. `packages/content`
depends on Pydantic only and on no EDU-IT package; `packages/infrastructure` depends on
`edu-it-domain` only. The `ContentStore` port lives in `packages/content`. The web wire member names
that exist today are snake_case (`user_id`, `server_now`, `content_version`, `stable_id`), and the
API conventions do not mandate a different casing. `CompletePasswordResetUseCase` already revokes
every prior Session and establishes exactly one new Session (Foundations 9.5, 28.1, 28.2).
Foundations Requirement 14 does not require fresh authentication for account deletion.

## Glossary

- **API_Service**: The `apps/api` FastAPI boundary, started by `python -m edu_it_api.serve`.
- **Served_App**: The FastAPI application returned by `build_served_app()` in `serve.py`.
- **V1_App**: The sub-application mounted at `/v1` by `mount_version_apps`.
- **Route_Table**: The single normative list of every V1_App route, defined in the design section
  "Route table" and mirrored by the constant `ROUTE_TABLE` in `edu_it_api/route_table.py`. Each entry
  states the method, the path, the authentication, the use case, the milestone, and whether the route
  is conditional.
- **Worker_Service**: The `apps/worker` task-queue consumer.
- **Web_App**: The `apps/web` Next.js App Router UI under `/fr` and `/en`.
- **Generated_Client**: The TypeScript client produced by `apps/web/scripts/generate-client.mjs` from the
  V1_App OpenAPI document.
- **Account_Router**: The new router holding the `/auth/*` and `/account/*` routes.
- **Learning_Router**: The router built by `build_learning_router` in `learning.py`.
- **List_Router**: The router built by `build_list_router` in `learning_lists.py`.
- **Lab_Router**: The router built by `build_labs_router`, composed by `composition.build_lab_router`.
- **Content_Router**: The new router holding `GET /quizzes/{stable_id}` and `GET /projects/{stable_id}`.
- **Composition_Root**: The module that chooses the real adapter for each port. The API composition
  lives in `apps/api/edu_it_api/composition.py` and the shared adapter builders in
  `packages/infrastructure/edu_it_infrastructure`.
- **Session**, **Verification_Link**, **Reset_Link**, **CSRF_Token**, **User**, **Role**: As defined in the
  glossary of `.kiro/specs/foundations/requirements.md`.
- **Session_Cookie**: The opaque `edu_it_session` cookie holding the Session `server_side_ref`.
- **Session_Store**: The Redis `SessionStore` (ADR 0004) that resolves the Session_Cookie.
- **CSRF_Pair**: The `csrf_token` cookie and the `X-CSRF-Token` header that the CSRF middleware compares.
- **Problem_Details**: The RFC 9457 error document (`application/problem+json`).
- **Credential_Store**: The `CredentialStore` port that stores and verifies a hashed password.
- **Password_Hasher**: The callable that turns a raw password into the stored secret.
- **Link_Builder**: The collaborator that turns a token and a locale into an absolute Web_App URL.
- **Object_Storage**: The domain `ObjectStorage` port (`packages/domain/edu_it_domain/ports/object_storage.py`).
- **Postgres_ContentStore**: The PostgreSQL implementation of the `ContentStore` port, located in
  `packages/content`.
- **Relational_Catalog**: The PostgreSQL catalog tables (`track`, `module`, `lesson`, `concept`,
  `prerequisite`, `quiz`, `question`, `question_option`, `project`, `rubric`, `rubric_criterion`,
  `lab`, `lab_task`, `lab_check`) read by `Postgres_CatalogRepository`.
- **Lab_Image**: A container image defined under `lab-images/`, as defined in the glossary of
  `.kiro/specs/hands-on-labs/requirements.md`.
- **Check**: One graded assertion of a lab task, as defined in the glossary of
  `.kiro/specs/hands-on-labs/requirements.md`.
- **Seed_Labs**: The two labs "Fix the shared folder" and "World-writable entries", as defined in the
  glossary of `.kiro/specs/hands-on-labs/requirements.md`. A **Seed_Lab** is either one of them.
- **Key_Parity_Test**: The automated test asserting that every translation key exists in both the French
  and English message catalogs, as defined in the glossary of `.kiro/specs/foundations/requirements.md`.
- **Real_Stack**: The Web_App, API_Service, Worker_Service, PostgreSQL, Redis, and the Docker
  Sandbox provider (`SANDBOX_PROVIDER=docker`, `EDU_IT_ENV=development`, `MAILER_BACKEND=console`).
- **Journey_Test**: `apps/web/e2e/real/learner-journey.e2e.ts`, the acceptance check `labs-playwright-real`.
- **Perf_Harness**: `tools/perf`, which mints learners directly in PostgreSQL and Redis.
- **Acceptance_Check**: A check run by `tools/run_verification.py --suite labs`.

## Requirements

### Requirement 1: Every router is mounted in the served app (Milestones 1 and 2)

**User Story:** As a learner, I want every feature the platform advertises to be reachable over HTTP,
so that the web app works against the real API.

#### Acceptance Criteria

1. THE Served_App SHALL mount the Account_Router, the Learning_Router, the List_Router, the Content_Router, and the Lab_Router on the V1_App (Milestone 1).
2. THE Composition_Root SHALL build every router from the real adapters (PostgreSQL, Redis, Mailer, sandbox provider) selected by `CompositionSettings` (Milestone 1).
3. THE API_Service SHALL resolve the authenticated user of every Account_Router, Learning_Router, List_Router, Content_Router, and Lab_Router route through one shared resolver that reads the Session_Cookie and resolves it through the Session_Store (Milestone 1).
4. WHEN a request to a route that requires authentication presents an absent, unknown, expired, or revoked Session_Cookie, THE API_Service SHALL return a translated Problem_Details response with status 401 (Milestone 1).
5. THE V1_App OpenAPI document of a running Served_App SHALL list every route of the Route_Table whose condition is satisfied by the running configuration, and the exported `apps/web/openapi/v1.json` SHALL list every route of the Route_Table, including the conditional routes (Milestone 1).
6. WHEN the OpenAPI export runs, THE export SHALL build the router set without contacting PostgreSQL, Redis, or the Docker daemon (Milestone 1).
7. THE API_Service SHALL serve `/health` and `/ready` unchanged after the routers are mounted (Milestone 1).
8. IF an adapter required by a mounted router cannot be constructed from the environment, THEN THE API_Service SHALL refuse to start and SHALL log the name of the failing adapter (Milestone 1).
9. WHERE none of `GITHUB_OAUTH_CLIENT_ID`, `GITHUB_OAUTH_CLIENT_SECRET`, and `GITHUB_OAUTH_CALLBACK_URL` is set to a non-empty value, THE API_Service SHALL start without mounting the OAuth routes, and a request to a path under `/v1/auth/github` SHALL receive a Problem_Details response with status 404 (Milestone 2).
10. IF some but not all of `GITHUB_OAUTH_CLIENT_ID`, `GITHUB_OAUTH_CLIENT_SECRET`, and `GITHUB_OAUTH_CALLBACK_URL` are set to a non-empty value, THEN THE API_Service SHALL refuse to start and SHALL log the names of the missing variables and never their values (Milestone 2).

### Requirement 2: Session cookie, CSRF, and cross-origin contract (Milestone 1)

**User Story:** As a security-conscious operator, I want the browser to authenticate and pass CSRF checks without weakening either, so that the existing protections work end to end.

#### Acceptance Criteria

1. WHEN a sign-in establishes a Session, THE API_Service SHALL set the Session_Cookie with the `HttpOnly` attribute, `SameSite=Lax`, `Path=/`, and a lifetime equal to `SESSION_DURATION`. The password-reset and OAuth routes apply this criterion through Requirements 5.3, 6.3, and 6.6.
2. WHERE `EDU_IT_ENV` is `production`, THE API_Service SHALL set the `Secure` attribute on the Session_Cookie and on the `csrf_token` cookie.
3. WHERE `COOKIE_DOMAIN` is configured, THE API_Service SHALL set the `Domain` attribute of the Session_Cookie and of the `csrf_token` cookie to that value.
4. WHEN a client requests `GET /v1/auth/csrf`, THE API_Service SHALL set a `csrf_token` cookie holding a token of at least 32 URL-safe characters, without the `HttpOnly` attribute so that scripts can read it, with `SameSite=Lax`, `Path=/`, and a lifetime equal to `SESSION_DURATION`, and SHALL return the same token in the response body.
5. WHEN a client requests `GET /v1/auth/csrf` and presents a well-formed `csrf_token` cookie, THE API_Service SHALL return that existing token.
6. THE API_Service SHALL keep the existing CSRF middleware and SHALL require the CSRF_Pair on every unsafe request, including `POST /auth/sign-up` and `POST /auth/sign-in`.
7. WHEN a sign-out completes, THE API_Service SHALL expire the Session_Cookie in the response.
8. THE API_Service SHALL answer cross-origin requests from the origins in `LAB_ALLOWED_WEB_ORIGINS` with credentials allowed, with the request methods equal to the distinct HTTP methods of the Route_Table plus `OPTIONS` (which includes `PUT` for the lab file save), and with the request headers `Content-Type`, `X-CSRF-Token`, `Idempotency-Key`, and `Accept-Language` allowed.
9. IF a cross-origin request comes from an origin outside `LAB_ALLOWED_WEB_ORIGINS`, THEN THE API_Service SHALL add no cross-origin allow header to the response.
10. THE API_Service SHALL never set the Session_Cookie from a response that fails with a Problem_Details document.
11. THE `.env.example` entry for `COOKIE_DOMAIN` SHALL state that the Web_App and the API_Service must be deployed same-site, sharing one registrable domain or `localhost`, because the `SameSite=Lax` cookies and the script read of the `csrf_token` cookie depend on it.

### Requirement 3: Sign-up and email verification routes (Milestone 1)

**User Story:** As a prospective learner, I want to sign up and verify my email over HTTP, so that I can create a confirmed account.

#### Acceptance Criteria

1. WHEN a client posts a valid email and a policy-compliant password to `POST /v1/auth/sign-up`, THE API_Service SHALL call `SignUpUseCase` and return status 202 with the body `{"accepted": true}`.
2. WHEN a client posts an email that already belongs to a User to `POST /v1/auth/sign-up`, THE API_Service SHALL return a response identical in status and body to the response for a new email (Foundations 7.6, 7.7).
3. IF `SignUpUseCase` raises `SignUpValidationError`, THEN THE API_Service SHALL return a validation Problem_Details response with one `fields` entry per rejected field and status 422 (Foundations 7.8).
4. IF `SignUpUseCase` raises `SignUpMailDeliveryError`, THEN THE API_Service SHALL return a translated Problem_Details response with status 502 and SHALL keep the created User unverified (Foundations 24.1).
5. WHEN a client posts a token to `POST /v1/auth/verify-email` and `VerifyEmailUseCase` returns `VERIFIED`, THE API_Service SHALL return status 200.
6. WHEN `VerifyEmailUseCase` returns `INVALID_OR_EXPIRED`, THE API_Service SHALL return a translated Problem_Details response with status 400 whose extension member `offer_resend` is true (Foundations 7.5, 25.2).
7. WHEN a client posts an email to `POST /v1/auth/verify-email/resend`, THE API_Service SHALL call `ResendVerificationUseCase` and return status 202 with a body identical for registered, unregistered, and already-verified emails.
8. IF `ResendVerificationUseCase` raises `ResendVerificationError`, THEN THE API_Service SHALL return a translated Problem_Details response with status 502.
9. THE API_Service SHALL assign only the learner Role to every User created through these routes (Foundations 7.9, 13.3).
10. THE API_Service SHALL validate every request body with a Pydantic v2 model that rejects unknown fields and that types every `email` and `password` member as an unconstrained string, so that `SignUpValidationError` from the use case is the only validator of email shape, email length, and password policy.
11. WHEN a client posts to `POST /v1/auth/sign-up` or `POST /v1/auth/verify-email/resend`, THE use case SHALL perform one Password_Hasher invocation on sign-up and one token-factory invocation on resend, whether the email is new, registered and unverified, or registered and verified, so that the work done does not disclose account existence (Foundations 7.6, 24.3).

### Requirement 4: Sign-in, sign-out, and session probe routes (Milestone 1)

**User Story:** As a learner, I want to sign in and out and have the web app learn my state, so that protected pages work.

#### Acceptance Criteria

1. WHEN a client posts valid credentials of a verified account to `POST /v1/auth/sign-in`, THE API_Service SHALL call `AuthenticateUseCase`, set the Session_Cookie, and return status 200 with the body `{"redirect_to": "/dashboard"}` (a path without locale prefix) and no session reference.
2. IF `AuthenticateUseCase` raises `SignInError` with `INVALID_CREDENTIALS`, THEN THE API_Service SHALL return a translated Problem_Details response with status 401 and the same body for an unknown account and a wrong password (Foundations 8.4).
3. IF `AuthenticateUseCase` raises `SignInError` with `EMAIL_NOT_VERIFIED`, THEN THE API_Service SHALL return a translated Problem_Details response with status 403 (Foundations 8.3).
4. IF `AuthenticateUseCase` raises `SignInError` with `RATE_LIMITED`, THEN THE API_Service SHALL return a translated Problem_Details response with status 429 and a `Retry-After` header (Foundations 12.1, 12.4).
5. WHEN a client posts to `POST /v1/auth/sign-out`, THE API_Service SHALL call `SignOutUseCase` and return status 204, whether or not an active Session is present (Foundations 8.2).
6. WHEN a client requests `GET /v1/auth/session` with an active Session_Cookie, THE API_Service SHALL call `AuthenticateSessionUseCase` and return status 200 with the body `{"user_id": <stable_id>}`.
7. WHILE a Session is expired or revoked, THE API_Service SHALL return status 401 for `GET /v1/auth/session` (Foundations 8.5).
8. THE API_Service SHALL never include the Session `server_side_ref`, a password, or a password hash in any response body or log record.
9. WHEN a sign-in succeeds, THE API_Service SHALL establish a new Session that differs from any Session reference presented on the request, SHALL NOT reuse or refresh a presented Session reference, and SHALL replace the `csrf_token` cookie with a freshly generated token (Foundations 8.1).

### Requirement 5: Password reset routes (Milestone 2)

**User Story:** As a learner, I want to reset a forgotten password, so that I regain access.

#### Acceptance Criteria

1. WHEN a client posts an email to `POST /v1/auth/password-reset`, THE API_Service SHALL call `RequestPasswordResetUseCase` and return status 202 with a body identical for registered and unregistered emails (Foundations 9.1, 9.6).
2. IF `RequestPasswordResetUseCase` raises `PasswordResetMailDeliveryError`, THEN THE API_Service SHALL return a translated Problem_Details response with status 502 (Foundations 24.2).
3. WHEN a client posts a token and a policy-compliant password to `POST /v1/auth/password-reset/complete` and `CompletePasswordResetUseCase` returns `COMPLETED`, THE API_Service SHALL set the Session_Cookie for the new Session as in Requirement 2.1, SHALL replace the `csrf_token` cookie with a freshly generated token, and SHALL return status 200. The revocation of every other Session of the User is already performed by `CompletePasswordResetUseCase` (Foundations 9.2, 9.5, 28.1, 28.2) and this spec adds no rule for it.
4. WHEN `CompletePasswordResetUseCase` reports an expired, used, or unrecognized link, THE API_Service SHALL return a translated Problem_Details response with status 400 whose extension member `offer_restart` is true, and the User's password SHALL remain unchanged (Foundations 9.3, 25.3).
5. WHEN `CompletePasswordResetUseCase` rejects a password that fails the policy, THE API_Service SHALL return a validation Problem_Details response with status 422, and the User's password SHALL remain unchanged (Foundations 9.4).
6. WHEN a client posts an email to `POST /v1/auth/password-reset`, THE use case SHALL perform one token-factory invocation whether the email is registered or unregistered, so that the work done does not disclose account existence (Foundations 9.6).

### Requirement 6: GitHub OAuth routes (Milestone 2)

**User Story:** As a learner, I want to sign in with GitHub, so that I can use an existing identity.

#### Acceptance Criteria

1. WHEN a client requests `GET /v1/auth/github/authorize`, THE API_Service SHALL generate a random `state` and a random PKCE `code_verifier`, store both in one short-lived `HttpOnly` cookie, and redirect to the GitHub authorization URL built from `GITHUB_OAUTH_CLIENT_ID` and `GITHUB_OAUTH_CALLBACK_URL`, carrying the `state`, a `code_challenge` equal to the unpadded base64url encoding of the SHA-256 digest of the `code_verifier`, and `code_challenge_method=S256`.
2. WHEN GitHub redirects to `GET /v1/auth/github/callback`, THE API_Service SHALL exchange the code through a `GitHubOAuthClient` port together with the stored `code_verifier`, build an `OAuthCallback` whose `state_matches` compares the stored `state` with the returned `state` in constant time, and call `CompleteOAuthCallbackUseCase`.
3. WHEN `CompleteOAuthCallbackUseCase` returns `SIGNED_IN`, THE API_Service SHALL set the Session_Cookie as in Requirement 2.1, replace the `csrf_token` cookie with a freshly generated token, and redirect to the Web_App dashboard (Foundations 10.1, 10.4).
4. WHEN `CompleteOAuthCallbackUseCase` returns `PENDING_CONFIRMATION`, THE API_Service SHALL set only the temporary unauthenticated pending cookie and redirect to the Web_App link-confirmation page (Foundations 10.7).
5. IF `CompleteOAuthCallbackUseCase` raises `OAuthCallbackError`, THEN THE API_Service SHALL redirect to the Web_App sign-in page with a translated error code and SHALL create no User, `oauth_link`, or Session (Foundations 10.3, 10.5).
6. WHEN a client posts to `POST /v1/auth/github/confirm` with the pending cookie, THE API_Service SHALL call `ConfirmOAuthLinkUseCase` and, on `LINKED`, set the Session_Cookie as in Requirement 2.1, replace the `csrf_token` cookie with a freshly generated token, and return status 200 (Foundations 10.2).
7. WHEN a client posts to `POST /v1/auth/github/decline`, THE API_Service SHALL call `DeclineOAuthLinkUseCase`, expire the pending cookie, and return status 204 (Foundations 10.6).
8. THE `GitHubOAuthClient` adapter SHALL use only the Python standard library or a dependency pinned in `docs/adr/0001-versions.md` before the manifest changes.
9. THE automated tests SHALL run the OAuth routes against a mock `GitHubOAuthClient` and SHALL make no network call to GitHub (Foundations 21.5).
10. WHEN an OAuth sign-in or an OAuth link confirmation establishes a Session, THE API_Service SHALL establish a new Session that differs from any Session reference presented on the request.

### Requirement 7: Credential and link persistence (Milestones 1 and 2)

**User Story:** As a learner, I want my password and my links stored safely, so that sign-in works after sign-up and across restarts.

#### Acceptance Criteria

1. THE Password_Hasher SHALL hash a password with a salted, memory-hard function and SHALL embed the salt and the cost parameters in the stored string (Milestone 1).
2. THE Credential_Store `verify_password` operation SHALL compare hashes in constant time and SHALL return false, with identical timing class, for an unknown user and a wrong password (Milestone 1).
3. WHEN `SignUpUseCase` creates a new User, THE use case SHALL store the hashed credential through the Credential_Store before it sends the verification email (Milestone 1).
4. THE PostgreSQL schema SHALL gain the tables `credential` and `verification_link` through the Alembic revision `0003`, and that revision SHALL create no other table (Milestone 1).
5. THE PostgreSQL adapters for `CredentialStore` and `VerificationLinkRepository` SHALL pass the same contract tests as their in-memory fakes (Milestone 1).
6. THE PostgreSQL adapters SHALL store only the SHA-256 digest of a link token, never the raw token (Milestones 1 and 2).
7. THE tables `credential` and `verification_link` SHALL be covered by GDPR export, deletion, and retention in Milestone 1, THE tables `reset_link` and `pending_oauth_link` SHALL be covered in Milestone 2, and `docs/DATA-MODEL.md` Section 13 SHALL be updated in the same change as the revision that creates each table.
8. WHEN `mark_consumed` is called twice concurrently for one link, THE adapter SHALL consume the link exactly once (Milestones 1 and 2).
9. THE Alembic revisions of this spec SHALL each apply to a database at the previous revision and roll back without data loss for the existing tables (Milestones 1 and 2).
10. THE Perf_Harness `mint_learner` function SHALL continue to work without a credential row, because the sign-in route is the only reader of the Credential_Store (Milestone 1).
11. THE PostgreSQL schema SHALL gain the tables `reset_link` and `pending_oauth_link` through a later Alembic revision `0004`, and the existing `oauth_link` table SHALL back an `OAuthLinkRepository` adapter (Milestone 2).
12. THE PostgreSQL adapters for `ResetLinkRepository`, `PendingOAuthLinkRepository`, and `OAuthLinkRepository` SHALL pass the same contract tests as their in-memory fakes (Milestone 2).

### Requirement 8: Account emails carry working Web_App links (Milestones 1 and 2)

**User Story:** As a learner, I want the emails to contain a link that opens the right page, so that I can finish verification and reset.

#### Acceptance Criteria

1. THE verification email SHALL contain the absolute URL `{WEB_BASE_URL}/{locale}/verify-email?token={token}`, where `locale` is the User's locale (Milestone 1).
2. THE reset email SHALL contain the absolute URL `{WEB_BASE_URL}/{locale}/reset-password?token={token}` (Milestone 2).
3. THE application use cases SHALL receive a Link_Builder as an injected collaborator and SHALL NOT read environment variables (Milestone 1).
4. THE emails SHALL remain rendered in French or English according to the User's locale (Milestones 1 and 2).
5. THE console Mailer output SHALL contain each URL on a single line, so that the Journey_Test can extract it from `E2E_MAIL_LOG` (Milestones 1 and 2).
6. IF `WEB_BASE_URL` is not an absolute `http` or `https` URL, THEN THE API_Service SHALL refuse to start (Milestone 1).

### Requirement 9: Learning routes are mounted with real adapters (Milestone 1)

**User Story:** As a learner, I want to enroll, read lessons, take quizzes, reveal hints, review, and submit projects, so that I can progress through a track.

#### Acceptance Criteria

1. THE Composition_Root SHALL build `LearningServices` and `ListServices` from `Postgres_CatalogRepository`, `Postgres_EnrollmentRepository`, `Postgres_ProgressRepository`, `Postgres_MasteryRepository`, `Postgres_ReviewRepository`, `Postgres_AttemptRepository`, and `Postgres_LessonRepository`.
2. THE Composition_Root SHALL back the quiz-attempt idempotency store with `Redis_IdempotencyStore` and the attempt codec (`encode_attempt_result`, `decode_attempt_result`).
3. THE Composition_Root SHALL read time from `System_Clock` and identifiers from `Uuid_IdGenerator`, and SHALL never wire a `Test_Clock`.
4. WHEN an authenticated client posts `{"track_id": ...}` to `POST /v1/enrollments`, THE API_Service SHALL create the enrollment with status 201, or return the existing enrollment with status 200 (Learning core 17.1).
5. WHEN a client posts to `POST /v1/quiz-attempts` without an `Idempotency-Key` header, THE API_Service SHALL return a validation Problem_Details response (Learning core 18.1).
6. WHEN a client repeats a quiz attempt with the same `Idempotency-Key`, THE API_Service SHALL return the stored result and record one `quiz_attempt` only (Learning core 18.2, 23).
7. IF a learning use case raises a `LearningError`, THEN THE API_Service SHALL return the Problem_Details mapping that `install_learning_error_handlers` already defines (Learning core 18.6).
8. THE API_Service SHALL address every resource by `stable_id` and SHALL expose no internal numeric key (Learning core 17.4).
9. WHEN a learner has no enrollment in a track and requests its progress, THE API_Service SHALL return the existing `NotEnrolledError` mapping.

### Requirement 10: Quiz and project read routes (Milestone 1)

**User Story:** As a learner, I want the quiz and project pages to load their content, so that I can answer questions and submit work.

#### Acceptance Criteria

1. WHEN an authenticated client requests `GET /v1/quizzes/{stable_id}?locale=fr|en`, THE Content_Router SHALL return the quiz `stable_id`, `title`, `content_version`, and for each question its `stable_id`, localized `prompt`, options (`option_id`, localized `text`), and localized `hints`.
2. THE quiz response SHALL contain no field that reveals which option is correct.
3. WHEN an authenticated client requests `GET /v1/projects/{stable_id}?locale=fr|en`, THE Content_Router SHALL return the project `stable_id`, `title`, `description`, and for each rubric criterion its `stable_id`, localized `title`, and `max_points`.
4. WHEN a localized text is missing for the requested locale, THE Content_Router SHALL return the English text (Learning core 17.3).
5. IF the quiz or project does not exist or is archived, THEN THE Content_Router SHALL return a translated Problem_Details response with status 404.
6. THE new read use cases SHALL live in `edu_it_application.learning`, depend only on ports, and add no business rule.
7. THE structural data (questions, options, rubric) SHALL come from `CatalogRepository`, and the localized text SHALL come from the synced content store, matched by `stable_id`.

### Requirement 11: Project review adapter (Milestone 1)

**User Story:** As a learner, I want a submitted project to be recorded as pending until reviewed, so that I am not told a result that was never computed.

#### Acceptance Criteria

1. THE infrastructure layer SHALL provide a `ProjectReviewer` adapter that returns `None` for every submission, so the submission stays pending (Learning core 20.4).
2. THE production Composition_Root SHALL wire that adapter and SHALL NOT wire `Fake_ProjectReviewer`.
3. WHEN a complete project submission arrives, THE API_Service SHALL record one `project_submission` and return status 201 with `status` equal to `pending`.
4. IF a submission lacks a rubric criterion, has a score out of range, or has fewer evidence items than `MIN_PROJECT_EVIDENCE_COUNT`, THEN THE API_Service SHALL return a Problem_Details response and record no `project_submission` (Learning core 20.1, 20.2).

### Requirement 12: Relational catalog in PostgreSQL (Milestone 1)

**User Story:** As a platform operator, I want the authored content in PostgreSQL, so that enrollment, quizzes, and projects have the rows their foreign keys need.

#### Acceptance Criteria

1. THE `edu-it-content` package SHALL provide `Postgres_ContentStore`, a PostgreSQL implementation of the `ContentStore` port that `sync_content` writes through, constructed from an injected SQLAlchemy engine, and importing no other EDU-IT package.
2. WHEN `edu-it-content sync --database-url <url>` runs, THE command SHALL build the engine from the URL and SHALL write every catalog table listed in the Glossary entry Relational_Catalog.
3. WHEN `edu-it-content sync` runs twice on unchanged content, THE second run SHALL change no row (Learning core 14).
4. WHEN content changes, THE sync SHALL update rows in place by `stable_id` and SHALL never reset a learner completion (Learning core 14, 25, 26).
5. THE sync SHALL keep refusing `ai` items that carry no reviewer (hands-on-labs 20.12, 23).
6. THE Perf_Harness `seed_database` function SHALL delegate to the same sync, and SHALL remain idempotent.
7. THE Relational_Catalog written for `content/linux-fundamentals` SHALL contain the track `track.linux-fundamentals`, the quiz `quiz.reading-permissions`, the project `project.harden-shared-directory`, and both Seed_Labs.
8. THE `Postgres_ContentStore` SHALL obtain its table definitions by reflection from the migrated database, SHALL declare no table of its own, and SHALL raise an error that names the missing table when the migrations have not been applied.

### Requirement 13: Account export and deletion routes (Milestone 2)

**User Story:** As a learner, I want to export and delete my data over HTTP, so that I control my personal data.

#### Acceptance Criteria

1. WHEN an authenticated client posts to `POST /v1/account/export`, THE API_Service SHALL call `ExportUserDataUseCase` and return status 200 with the body members `download_path` (the path `/v1/account/export/download`) and `size_bytes` (Foundations 14.1).
2. WHEN an authenticated client posts `{"confirm": true}` to `POST /v1/account/deletion`, THE API_Service SHALL call `DeleteAccountUseCase`, expire the Session_Cookie, and return status 204 (Foundations 14.2, 14.3).
3. WHEN an authenticated client posts to `POST /v1/account/deletion` without confirmation, THE API_Service SHALL change no data and return a Problem_Details response.
4. IF `DeleteAccountUseCase` raises `AccountDeletionError`, THEN THE API_Service SHALL return a translated Problem_Details response and leave all personal data unchanged (Foundations 14.4).
5. WHEN an authenticated client requests `GET /v1/account/export/download`, THE API_Service SHALL read the export of that client from Object_Storage under the key derived from the authenticated user, SHALL return it as `application/json` with a `Content-Disposition: attachment` header, and SHALL return a translated Problem_Details response with status 404 when no export exists. THE key SHALL never be read from the request.
6. THE Composition_Root SHALL wire Object_Storage from `OBJECT_STORAGE_BACKEND` (`memory` to `In_Memory_ObjectStorage`, `filesystem` to `FileSystem_ObjectStorage` rooted at `OBJECT_STORAGE_ROOT`) and SHALL refuse to start, naming the adapter, for a backend value that has no adapter.

### Requirement 14: Web app uses the routes through the generated client (Milestones 1 and 2)

**User Story:** As a web developer, I want the pages to call the API only through the generated client, so that web and API stay in sync.

#### Acceptance Criteria

1. THE OpenAPI export SHALL build the V1_App with every router of the Served_App, so `apps/web/openapi/v1.json` contains the paths of the Route_Table (Milestone 1).
2. THE Web_App build SHALL regenerate the Generated_Client from that document before compiling application code (Foundations 16.2) (Milestone 1).
3. THE Generated_Client SHALL attach `credentials: 'include'` to browser calls and SHALL send `X-CSRF-Token` with the value of the `csrf_token` cookie on every unsafe call, requesting `GET /v1/auth/csrf` first when the cookie is absent (Milestone 1).
4. WHEN a Next.js server action calls the API, THE server action SHALL forward the Session_Cookie and SHALL send a freshly generated CSRF_Pair (Milestone 1).
5. THE Web_App sign-up page SHALL, on status 202, redirect to `/{locale}/verify-email` (Milestone 1).
6. THE Web_App verify-email page SHALL, when the query string carries `token`, post it to `POST /v1/auth/verify-email` and show a translated success state or an invalid-or-expired state with the resend form (Milestone 1).
7. THE Web_App reset-password page SHALL show the request form when no `token` is present and the new-password form when `token` is present (Milestone 2).
8. WHEN the sign-in route returns `redirect_to`, THE Web_App sign-in page SHALL navigate to `/{locale}{redirect_to}`, where `locale` is the current locale, because the API returns a locale-free path (Milestone 1).
9. WHERE the Web_App renders a GitHub link-confirmation page, THE page SHALL post to the confirm or decline route (Milestone 2).
10. THE Web_App SHALL read `NEXT_PUBLIC_API_BASE_URL` as the API root that includes `/v1`, and `.env.example` SHALL carry that value (Milestone 1).
11. THE Web_App SHALL add every new user-facing string to both the `fr` and `en` message catalogs, and the Key_Parity_Test SHALL pass (Milestones 1 and 2).
12. WHERE `NEXT_PUBLIC_GITHUB_SIGN_IN_ENABLED` is `true`, THE Web_App sign-in page SHALL offer a GitHub sign-in link to `/v1/auth/github/authorize` (Milestone 2).
13. THE Web_App API adapter SHALL read the sign-in response member `redirect_to` and map it to its internal `redirectTo` property (Milestone 1).

### Requirement 15: Worker composition (Milestone 1)

**User Story:** As an operator, I want the worker to run against the same real adapters, so that lab jobs complete during the journey.

#### Acceptance Criteria

1. THE Worker_Service SHALL keep consuming `lab.provision` and `lab.grade` with the composition it uses today.
2. THE Worker_Service SHALL apply lab mastery through the same PostgreSQL repositories the Learning_Router reads, so a passed Check changes the progress response.
3. THE Worker_Service SHALL require no account route, Session, or Mailer.

### Requirement 16: Layering, compatibility, and recorded risks (Milestones 1 and 2)

**User Story:** As a maintainer, I want the wiring to respect the layering and not break existing tooling, so that earlier phases stay verified.

#### Acceptance Criteria

1. THE new routes SHALL respect the layering `api -> application -> domain <- infrastructure`, and `tools/check_domain_boundary.py` SHALL pass.
2. THE Perf_Harness SHALL keep running unchanged: `mint_learner` SHALL still insert a verified `user` row and create a Session in the Redis Session_Store, and the minted cookie SHALL authenticate against the new shared resolver.
3. THE `tools/perf/stack.py` module docstring SHALL be updated to state that the sign-up and sign-in routes exist and that the harness mints learners directly to keep sign-up out of the measurement.
4. THE new dependencies, if any, SHALL be pinned exactly in `docs/adr/0001-versions.md` before the manifest changes, and `tools/tests/test_labs_adr_pins.py` SHALL pass.
5. THE new business numbers, if any, SHALL live in `packages/domain/edu_it_domain/config.py`.
6. THE API_Service SHALL keep internal diagnostics in logs and SHALL place no stack trace, SQL, or provider error in any response (Foundations 18.6, 19.6).
7. THE workspace package dependency graph SHALL be acyclic, THE `edu-it-domain` and `edu-it-content` packages SHALL depend on no EDU-IT package, THE `edu-it-application` and `edu-it-infrastructure` packages SHALL depend on `edu-it-domain` only, no `packages/*` project SHALL depend on an `apps/*` project, and every third-party dependency of `edu-it-content` SHALL be pinned in `docs/adr/0001-versions.md`. An automated test SHALL assert these properties (Milestone 1).
8. THE repository SHALL record in `docs/RISKS.md` three accepted risks of this spec, each with a named follow-up owner: abuse of the mail-sending endpoints (sign-up, resend, reset request) because no rate limit is added, the residual account-existence timing from mail delivery latency, and account deletion without fresh authentication (Milestone 1).
9. THE repository SHALL record in `docs/API-CONVENTIONS.md` that a failed mail delivery maps to status 502 with the problem slug `mail-delivery-failed`, so that other routes stay consistent (Milestone 1).

### Requirement 17: Real-stack end-to-end proof (Milestone 1)

**User Story:** As a product owner, I want the full learner journey and both Seed_Labs proven on the real stack, so that the platform works as a whole.

#### Acceptance Criteria

1. THE repository SHALL provide a scripted, documented bootstrap that starts the Real_Stack, applies migrations, syncs the catalog into PostgreSQL, builds the Lab_Images with the `ALPINE_MIRROR` environment variable passed through to the build when it is set, and tees the API output to `E2E_MAIL_LOG`.
2. WHEN the Journey_Test runs, THE Real_Stack SHALL let a learner sign up through the Web_App, open the verification link read from `E2E_MAIL_LOG`, sign in, enroll in `track.linux-fundamentals`, pass `quiz.reading-permissions`, open `lab.fix-the-shared-folder`, run a terminal command, pass every Check of the lab, submit `project.harden-shared-directory`, and observe module "Processes" unlock (hands-on-labs 32.1).
3. THE repository SHALL include a real-stack test in which a learner passes the Checks of both Seed_Labs and sees translated feedback in `/fr` and in `/en` (hands-on-labs 32.2, 32.3).
4. THE real-stack tests SHALL use no mock API.
5. THE real-stack tests SHALL run against the Docker Sandbox provider and the real worker, API, and web app (hands-on-labs 32.3).
6. THE default `npm run test:e2e` SHALL keep running against the deterministic mock API only.
7. THE API tests SHALL include an in-process test that drives sign-up, verification, sign-in, enrollment, quiz attempt, project submission, and sign-out through HTTP against the Served_App built with fakes.
