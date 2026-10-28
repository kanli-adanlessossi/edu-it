# Requirements Document

## Introduction

This spec defines the **foundations** of EDU-IT: the scaffolding, account system, and bilingual
application shell on which every later learning feature is built. It is a new-feature,
requirements-first spec. The deliverable of this phase is working application code across the
monorepo (`apps/web`, `apps/api`, `apps/worker`, the shared Python package, `deploy/` for local
Docker Compose, and CI configuration), together with a phase report.

The authoritative product scope, technology choices, repository layering, API conventions,
authentication and privacy rules, deployment rules, and quality bars live in the steering files
and design documents already in the repository. This document does **not** restate those rules; it
references them and expresses the foundations work as testable requirements. The downstream
`design.md` and `tasks.md` will link steering through `#[[file:.kiro/steering/<file>.md]]`
includes (per `.kiro/steering/working-agreements.md`) rather than copying rule text.

### Grounding references

- Product scope and principles: `.kiro/steering/product.md`
- Technology choices and domain ports: `.kiro/steering/tech.md`
- Repository structure and layering: `.kiro/steering/structure.md`
- Quality bars and testing: `.kiro/steering/quality.md`
- Working agreements and process: `.kiro/steering/working-agreements.md`
- Authentication and privacy: `.kiro/steering/auth-privacy.md`
- API conventions (RFC 9457, pagination, versioning, stable IDs, idempotency):
  `.kiro/steering/api-conventions.md` and `docs/API-CONVENTIONS.md`
- Deployment and Kubernetes topology (for later phases): `.kiro/steering/deployment.md`
- Architecture: `docs/ARCHITECTURE.md`
- Data model (schema this phase migrates): `docs/DATA-MODEL.md`
- Pinned versions and other decisions: `docs/adr/0001-versions.md` … `docs/adr/0008-reserved.md`
- Phase-0 acceptance report: `docs/reports/phase-0.md`

### Scope boundary

This phase builds **only** the foundations. The following are explicitly **out of scope** and are
captured as an exclusion requirement (Requirement 20): learning features (tracks, modules,
lessons, quizzes, mastery, review, decay, placement, final assessment), hands-on labs and the
sandbox/terminal, AI content generation and the review pipeline, and Kubernetes deployment
(Helm charts, manifests, network policies, node pools). The data model is **migrated** in full in
this phase, but the behavior of the learning, lab, and AI tables is not implemented here.

## Glossary

- **EDU-IT**: The bilingual (French/English) re-learning platform described in
  `.kiro/steering/product.md`.
- **Monorepo**: The single repository described in `.kiro/steering/structure.md`, containing
  `apps/web`, `apps/api`, `apps/worker`, the shared Python package, and supporting directories.
- **Web_App**: The `apps/web` Next.js App Router UI with `/fr` and `/en` routes.
- **API_Service**: The `apps/api` FastAPI boundary for authentication, sessions, and HTTP
  endpoints. Validates with Pydantic v2.
- **Worker_Service**: The `apps/worker` task-queue consumer.
- **Shared_Package**: The framework-free shared Python package holding domain and application code
  reused by API_Service and Worker_Service.
- **Compose_Stack**: The local-development Docker Compose definition that runs Web_App,
  API_Service, Worker_Service, PostgreSQL, and Redis.
- **PostgreSQL**: The system of record, per `.kiro/steering/tech.md`.
- **Redis**: The task-queue backing and ephemeral/session store, per `.kiro/steering/tech.md`.
- **Migration_Runner**: The Alembic migration tooling that applies and rolls back schema changes
  against PostgreSQL.
- **Data_Model**: The complete approved schema defined in `docs/DATA-MODEL.md` (D3).
- **Clock**: The injected time port defined in the domain (`.kiro/steering/tech.md`).
- **Test_Clock**: A controllable Clock implementation whose time can be set or advanced, enabled
  only in test and development environments.
- **Mailer**: The outbound-mail port; the console backend prints mail in development, per
  `.kiro/steering/auth-privacy.md` and `.kiro/steering/tech.md`.
- **ObjectStorage**: The object-storage port, per `.kiro/steering/tech.md`.
- **TaskQueue**: The task-queue port, per `.kiro/steering/tech.md`.
- **LLMProvider**: The LLM port; only a fake implementation is provided in this phase, per
  `.kiro/steering/tech.md`.
- **Fake_Implementation**: An in-memory or console implementation of a port used in tests and
  local development, per `.kiro/steering/quality.md`.
- **Health_Endpoint**: An HTTP endpoint reporting that a service process is alive (liveness).
- **Readiness_Endpoint**: An HTTP endpoint reporting that a service and its dependencies are ready
  to serve (readiness).
- **Problem_Details**: The RFC 9457 error response format defined in `docs/API-CONVENTIONS.md`
  (D2) and `.kiro/steering/api-conventions.md`.
- **Request_ID**: A unique identifier attached to each inbound request and emitted in structured
  logs.
- **User**: An account in the identity model (`.kiro/steering/auth-privacy.md`), with a
  `stable_id`, email, display name, and locale.
- **Session**: The server-side session backing a signed-in User, per
  `.kiro/steering/auth-privacy.md` and ADR 0004.
- **Role**: One of learner, reviewer (with per-track expertise), author, or admin, per the
  identity model in `.kiro/steering/auth-privacy.md`.
- **Password_Policy**: The configured password-strength policy — a minimum length of 12 and a
  maximum length of 128 characters — recorded as a business constant per
  `.kiro/steering/structure.md`.
- **Verification_Link**: A single-use, time-limited link emailed to confirm a User's email during
  sign-up.
- **Reset_Link**: A single-use, time-limited link emailed to begin a password reset.
- **OAuth_Provider**: GitHub, used as the external identity provider in this phase.
- **CSRF_Token**: The anti-cross-site-request-forgery token required on unsafe mutations.
- **Locale**: The language of learner-facing content and UI, either French (`fr`) or English
  (`en`).
- **Key_Parity_Test**: An automated test asserting that every translation key exists in both the
  French and English message catalogs.
- **OpenAPI_Document**: The per-major-version OpenAPI specification published by API_Service, per
  `docs/API-CONVENTIONS.md` Section 3.
- **Generated_Client**: The TypeScript client generated from OpenAPI_Document during the Web_App
  build.
- **CI_Pipeline**: The continuous-integration pipeline that runs on every push.
- **Phase_Report**: `docs/reports/phase-1.md`, the acceptance artifact for this phase.

---

## Requirements

<!-- =====================  MILESTONE A — SCAFFOLDING  ===================== -->

### Requirement 1: Monorepo tooling for both languages

**User Story:** As a developer, I want lint, format, typecheck, and test tooling for the TypeScript and Python code, so that every contributor gets consistent, enforced code quality.

#### Acceptance Criteria

1. THE Monorepo SHALL provide a lint command, a format command, a typecheck command, and a test command for the TypeScript code in Web_App.
2. THE Monorepo SHALL provide a lint command, a format command, a typecheck command, and a test command for the Python code in API_Service, Worker_Service, and Shared_Package.
3. THE Monorepo SHALL pin the framework and test-tool versions recorded in `docs/adr/0001-versions.md`.
4. IF a developer runs the lint command and a file violates a configured lint rule, THEN THE Monorepo SHALL exit with a non-zero status and report the violating file path and the violated rule.
5. IF a developer runs the typecheck command and a file contains a type error, THEN THE Monorepo SHALL exit with a non-zero status and report the file path and location of each type error.
6. IF a developer runs the test command and any test fails, THEN THE Monorepo SHALL exit with a non-zero status and identify the failing tests.
7. WHEN a developer runs the lint, format, typecheck, or test command and no violation, type error, or test failure is present, THE Monorepo SHALL exit with a zero status.
8. THE Shared_Package SHALL contain no import of a web or API framework, consistent with the framework-free domain rule in `.kiro/steering/structure.md`.

### Requirement 2: Pre-commit hooks and environment template

**User Story:** As a developer, I want pre-commit hooks and an environment template, so that problems are caught before commit and local setup is reproducible.

#### Acceptance Criteria

1. THE Monorepo SHALL provide a pre-commit hook configuration that runs the lint, format, and typecheck commands against only the files staged for the pending commit.
2. WHEN a developer initiates a commit, THE Monorepo SHALL run the configured pre-commit hooks before the commit is recorded.
3. IF any pre-commit hook returns a non-zero exit status, THEN THE Monorepo SHALL block the commit, leave the staged changes uncommitted, and emit a message identifying which hook failed.
4. WHEN all pre-commit hooks return a zero exit status, THE Monorepo SHALL allow the commit to complete.
5. THE Monorepo SHALL provide a `.env.example` file at the repository root that enumerates every environment variable read by Web_App, API_Service, and Worker_Service, with one variable per line.
6. THE `.env.example` file SHALL assign each variable a non-sensitive placeholder value and SHALL contain no real secret, credential, token, or production value.

### Requirement 3: Local development Docker Compose stack

**User Story:** As a developer, I want a single command to run the whole stack locally, so that I can develop against all services without manual setup.

#### Acceptance Criteria

1. THE Compose_Stack SHALL define exactly five services: Web_App, API_Service, Worker_Service, PostgreSQL, and Redis.
2. WHEN a developer runs the Compose_Stack start command on a machine with no prior state, THE Compose_Stack SHALL start all five services, each reaching the running state as reported by the Compose_Stack status command.
3. IF any of the five services fails to reach the running state, THEN THE Compose_Stack SHALL report a failure indicating which service did not start and SHALL leave the remaining services' state unchanged.
4. WHEN all five services have started, THE Readiness_Endpoint of Web_App, API_Service, and Worker_Service SHALL each report ready within 120 seconds of the start command.
5. IF the Readiness_Endpoint of Web_App, API_Service, or Worker_Service does not report ready within 120 seconds of the start command, THEN THE Compose_Stack SHALL report a readiness-timeout failure indicating which service did not become ready.
6. THE Compose_Stack readiness check SHALL be considered passed only when the Readiness_Endpoints of Web_App, API_Service, and Worker_Service all report ready within 120 seconds of the start command, and the failure of any one Readiness_Endpoint to report ready within that time SHALL fail the whole readiness check regardless of the other Readiness_Endpoints.
7. WHILE the Compose_Stack is running, API_Service and Worker_Service SHALL each establish a connection to PostgreSQL and to Redis, consistent with the connectivity described in `.kiro/steering/deployment.md`.

### Requirement 4: Alembic migrations for the complete data model

**User Story:** As a developer, I want migrations that create the entire approved schema, so that later features build on a stable, versioned database.

#### Acceptance Criteria

1. THE Migration_Runner SHALL define migrations that create exactly one table for each entity listed in the Data_Model entity groups and entity-relationship diagram of `docs/DATA-MODEL.md`, with no listed entity omitted and no table beyond those entities.
2. WHEN the Migration_Runner applies all migrations to an empty database, THE Migration_Runner SHALL create the complete schema including each table's primary key, foreign keys, and the unique constraint on each `stable_id`, and SHALL exit with a success status.
3. WHEN the Migration_Runner rolls back all applied migrations, THE Migration_Runner SHALL remove the Data_Model tables in reverse order until no Data_Model table remains, and SHALL exit with a success status.
4. THE Data_Model schema SHALL expose an immutable, unique `stable_id` string on every API-addressable resource and SHALL keep internal numeric primary keys unexposed, consistent with `docs/API-CONVENTIONS.md` Section 4 and `docs/DATA-MODEL.md` Section 1.
5. IF a migration fails partway through application, THEN THE Migration_Runner SHALL roll back the failing migration, leave the database at the last successfully committed migration version, exit with a non-zero status, and report the failing migration.
6. WHEN the Migration_Runner is run against a database already at the latest migration version, THE Migration_Runner SHALL make no schema change and SHALL exit with a success status.

### Requirement 5: Infrastructure ports with fakes

**User Story:** As a developer, I want each external system behind a port with a fake, so that the domain stays testable and local development needs no real external services.

#### Acceptance Criteria

1. THE Shared_Package SHALL define the Clock, Mailer, ObjectStorage, TaskQueue, and LLMProvider ports, consistent with the port list in `.kiro/steering/tech.md`.
2. THE Shared_Package SHALL provide a Fake_Implementation for each of Clock, Mailer, ObjectStorage, TaskQueue, and LLMProvider, and each Fake_Implementation SHALL implement every method of its port so that it is substitutable for the production implementation without changing domain code, per `.kiro/steering/quality.md`.
3. WHERE the environment is local development, WHEN the Mailer sends outbound mail, THE Mailer SHALL write that mail to standard output and SHALL retain the sent mail in a retrievable record for test inspection, per `.kiro/steering/auth-privacy.md`.
4. WHERE the environment is local development or test, THE API_Service and Worker_Service SHALL use the Fake_Implementation of LLMProvider and SHALL make no network call to a real LLM provider.
5. WHEN domain code needs the current time, THE domain SHALL read it from the injected Clock and SHALL NOT read the wall clock directly, per `.kiro/steering/structure.md`.

### Requirement 6: Test clock control

**User Story:** As a developer, I want to control time in tests and development, so that time-dependent behavior such as link expiry is deterministic.

#### Acceptance Criteria

1. WHERE the environment is test or development, WHEN a caller sets the Test_Clock to an absolute instant, THE Test_Clock SHALL make that instant the current time read through the injected Clock.
2. WHERE the environment is test or development, WHEN a caller advances the Test_Clock by a non-negative duration, THE Test_Clock SHALL make the current time read through the injected Clock equal the prior current time plus that duration.
3. WHERE the environment is production, THE API_Service and Worker_Service SHALL NOT expose any interface that sets or advances the Clock.
4. WHEN the Test_Clock time is set or advanced, THE time subsequently read by domain code through the injected Clock SHALL equal the set or advanced instant exactly, consistent with the Clock port in `.kiro/steering/tech.md`.
5. IF a caller advances the Test_Clock by a negative duration or sets it to a value that is not a valid instant, THEN THE Test_Clock SHALL reject the operation, SHALL leave the current time unchanged, and SHALL raise an error indicating the invalid input.

<!-- =====================  MILESTONE B — ACCOUNTS  ===================== -->

### Requirement 7: Sign up and email verification

**User Story:** As a prospective learner, I want to sign up and verify my email, so that I can create a confirmed account.

#### Acceptance Criteria

1. WHEN a visitor submits the sign-up form with a well-formed email no longer than 254 characters and a password meeting the configured policy, THE API_Service SHALL create a User with an unverified email and SHALL enqueue a verification email through the Mailer.
2. THE configured password policy SHALL require a password of at least 12 and at most 128 characters, defined as a business constant consistent with the business-numbers rule in `.kiro/steering/structure.md`.
3. THE verification email SHALL contain a single-use Verification_Link that expires 24 hours after issuance as measured by the Clock.
4. WHEN a User opens a valid, unexpired, unused Verification_Link, THE API_Service SHALL mark the User's email as verified and SHALL invalidate that Verification_Link.
5. IF a User opens a Verification_Link that is expired, already used, or unrecognized, THEN THE API_Service SHALL reject the request, SHALL leave the User's email verification state unchanged, and SHALL return a response indicating the link is invalid or expired.
6. IF a visitor submits the sign-up form with an email that already belongs to an existing User, THEN THE API_Service SHALL reject the request, SHALL NOT create a duplicate User, and SHALL return a response that does not disclose whether the email is already registered.
7. WHEN a visitor submits the sign-up form with an email that already belongs to an existing User, THE API_Service SHALL send a notification email through the Mailer to the existing User's email address informing the account owner that a sign-up was attempted with their email.
8. WHEN sign-up input fails Pydantic v2 validation, THE API_Service SHALL return a Problem_Details response identifying each invalid field and the reason it was rejected, without leaking internal representations, per `.kiro/steering/api-conventions.md`.
9. THE API_Service SHALL assign the learner Role to every newly created self-service User, per the identity model in `.kiro/steering/auth-privacy.md`.

### Requirement 8: Sign in, sign out, and server-side sessions

**User Story:** As a learner, I want to sign in and out using a secure session, so that my authenticated state is managed server-side.

#### Acceptance Criteria

1. WHEN a User submits valid credentials for a verified account, THE API_Service SHALL create a server-side Session, SHALL associate the Session with the User, and SHALL set the Session to expire after a configured duration measured by the Clock, where that duration is a business constant consistent with the business-numbers rule in `.kiro/steering/structure.md`, per `.kiro/steering/auth-privacy.md` and ADR 0004.
2. WHEN a signed-in User signs out, THE API_Service SHALL revoke the current Session so that it can no longer authenticate a request and SHALL return a success response even when no active Session is present.
3. IF a User submits credentials for an account whose email is not verified, THEN THE API_Service SHALL create no Session and SHALL return a translated Problem_Details response indicating that the email is not verified, as defined by the error-rendering rules in Requirement 19.
4. IF a User submits credentials that do not match any verified account, THEN THE API_Service SHALL create no Session and SHALL return a translated Problem_Details response indicating that the credentials are invalid, without disclosing whether the account exists.
5. WHILE a Session is expired or revoked, THE API_Service SHALL reject each request that presents that Session as unauthenticated and SHALL return a translated Problem_Details response.

### Requirement 9: Password reset

**User Story:** As a learner, I want to reset my password, so that I can regain access if I forget it.

#### Acceptance Criteria

1. WHEN a User requests a password reset for a registered email, THE API_Service SHALL enqueue a reset email through the Mailer containing a single-use Reset_Link that expires 1 hour after issuance as measured by the Clock.
2. WHEN a User opens an unauthenticated Reset_Link that is unexpired, previously unused, and matches an issued link, AND submits a new password that satisfies the configured password policy, THE API_Service SHALL update the User's password and SHALL mark that Reset_Link as used.
3. IF a User submits a new password through a Reset_Link that is expired, already used, or unrecognized, THEN THE API_Service SHALL reject the request, SHALL leave the User's existing password unchanged, and SHALL return an error response indicating the link is invalid or expired.
4. IF a User submits a new password that does not satisfy the configured password policy, THEN THE API_Service SHALL reject the request, SHALL leave the User's existing password unchanged, and SHALL return an error response indicating the password requirements were not met.
5. WHEN a password reset completes successfully through an unauthenticated Reset_Link, THE API_Service SHALL revoke all existing Sessions of that User and SHALL establish a new Session for the device completing the reset, so that the only valid Session afterward is the newly established Session.
6. WHEN a User requests a password reset for an email that is not registered, THE API_Service SHALL return a response identical in content and status to the response returned for a registered email, AND SHALL NOT enqueue a reset email, so that account existence is not disclosed.

### Requirement 10: GitHub OAuth sign-in and account linking

**User Story:** As a learner, I want to sign in with GitHub, so that I can use an existing identity provider.

#### Acceptance Criteria

1. WHEN a visitor completes the GitHub OAuth flow and the returned verified email matches no existing User, THE API_Service SHALL create a User, record the GitHub `oauth_link`, and establish a Session.
2. WHEN a visitor completes the GitHub OAuth flow and the returned verified email matches an existing User, THE API_Service SHALL present a link-confirmation step and SHALL link the GitHub `oauth_link` to that existing User only after the User explicitly confirms the link within a configured confirmation window measured by the Clock.
3. IF the GitHub OAuth flow returns an email that is not verified at GitHub, THEN THE API_Service SHALL NOT link the GitHub identity to an existing User by that email and SHALL return a translated Problem_Details response indicating the email is unverified, per `.kiro/steering/api-conventions.md`.
4. WHERE a GitHub `oauth_link` already exists for a returning visitor, THE API_Service SHALL establish a Session for the linked User without creating a duplicate account.
5. IF the GitHub OAuth flow fails, is denied by the visitor, or does not complete, THEN THE API_Service SHALL NOT create a User, record an `oauth_link`, or establish a Session, and SHALL return a translated Problem_Details response indicating the sign-in did not complete, per `.kiro/steering/api-conventions.md`.
6. IF the User declines the link confirmation or the confirmation window expires before the User confirms, THEN THE API_Service SHALL NOT link the GitHub `oauth_link` to the existing User and SHALL leave the existing User and its `oauth_link` records unchanged.
7. WHILE the link-confirmation step is pending for a returning verified email that matches an existing User, THE API_Service SHALL establish a temporary, unauthenticated session that allows the visitor to proceed with the in-app link confirmation, and SHALL NOT grant authenticated access to the existing User until the link is confirmed.

### Requirement 11: CSRF protection

**User Story:** As a security-conscious operator, I want unsafe mutations protected against CSRF, so that forged cross-site requests cannot act on a User's behalf.

#### Acceptance Criteria

1. WHEN a client submits an unsafe mutation with a valid CSRF_Token, THE API_Service SHALL process the request.
2. IF a client submits an unsafe mutation with a missing, malformed, or invalid CSRF_Token, THEN THE API_Service SHALL reject the request, make no state change, and return a Problem_Details response, per `.kiro/steering/api-conventions.md`.
3. WHERE a request uses a safe, idempotent method as defined in `docs/API-CONVENTIONS.md` Section 5, THE API_Service SHALL NOT require a CSRF_Token.
4. WHERE a request uses an unsafe method as defined in `docs/API-CONVENTIONS.md` Section 5, THE API_Service SHALL require a valid CSRF_Token.

### Requirement 12: Rate limiting of authentication attempts

**User Story:** As a security-conscious operator, I want repeated failed sign-ins rate limited, so that credential-stuffing and brute-force attempts are slowed.

#### Acceptance Criteria

1. WHILE failed sign-in attempts for an identifier exceed the configured threshold within the configured window, THE API_Service SHALL reject further sign-in attempts for that identifier with a translated Problem_Details response, without evaluating the submitted credentials.
2. WHEN the configured rate-limit window elapses as measured by the Clock, THE API_Service SHALL allow sign-in attempts for that identifier again.
3. THE configured sign-in rate-limit threshold and window SHALL be defined as business constants, consistent with the business-numbers rule in `.kiro/steering/structure.md`.
4. WHILE sign-in attempts for an identifier are rate limited, THE API_Service SHALL establish no Session for that identifier even when the submitted credentials are valid.

### Requirement 13: Roles and privileged-user CLI

**User Story:** As an operator, I want to create reviewer and admin users from the command line, so that privileged accounts are provisioned without a self-service path.

#### Acceptance Criteria

1. THE identity model SHALL support the learner, reviewer, author, and admin Roles, per `.kiro/steering/auth-privacy.md`, with the reviewer Role carrying per-track expertise recorded as the role assignment's track scope.
2. WHEN an operator runs the privileged-user CLI command with a syntactically valid target email, a Role of reviewer or admin, and a track scope for a reviewer Role, THE CLI SHALL create the User if none exists for that email or update the existing User, assign the specified Role with the track scope for a reviewer Role, and exit with a zero status.
3. THE self-service sign-up and GitHub OAuth paths SHALL assign only the learner Role and SHALL NOT grant the reviewer, author, or admin Role.
4. IF the privileged-user CLI command is run with an unrecognized Role, a missing or syntactically invalid target email, or a reviewer Role without a track scope, THEN THE CLI SHALL exit with a non-zero status, make no change to any User, and emit an error indication identifying the invalid input.

### Requirement 14: GDPR data export and account deletion

**User Story:** As a learner, I want to export my data and delete my account, so that I control my personal data as required by GDPR.

#### Acceptance Criteria

1. WHEN a signed-in User requests a data export, THE API_Service SHALL produce a downloadable export containing all data held for that User across every personal-data table listed in `docs/DATA-MODEL.md` Section 13, including tables for which that User has no rows represented as empty.
2. WHEN a signed-in User requests account deletion and confirms the deletion in a distinct confirmation step, THE API_Service SHALL remove the User's personal data across every personal-data table as defined by the deletion coverage in `.kiro/steering/auth-privacy.md` and `docs/DATA-MODEL.md` Section 13.
3. WHEN account deletion completes, THE API_Service SHALL revoke all Sessions of that User.
4. IF account deletion fails to remove personal data from any personal-data table, THEN THE API_Service SHALL leave the User's personal data unchanged across all personal-data tables and return an error indication that the deletion did not complete.
5. WHEN a User requests the privacy page or the terms page, THE API_Service SHALL serve a placeholder page for the requested page in French and in English according to the requested locale.

<!-- =====================  MILESTONE C — BILINGUAL SHELL  ===================== -->

### Requirement 15: Bilingual routing and language preference

**User Story:** As a bilingual learner, I want the app in French or English with a persistent preference, so that I can use EDU-IT in my language.

#### Acceptance Criteria

1. THE Web_App SHALL serve its pages under a `/fr` route tree and an `/en` route tree and SHALL support exactly the French (`fr`) and English (`en`) Locales, per `.kiro/steering/product.md`.
2. WHEN a User activates the language switcher, THE Web_App SHALL move the User to the other-Locale view of the current page, preserving the page path and query parameters.
3. WHEN a signed-in User selects a Locale, THE API_Service SHALL save that Locale as the User's preference, and THE Web_App SHALL render the saved Locale on the User's next visit.
4. IF saving a signed-in User's Locale preference fails, THEN THE API_Service SHALL leave the previously saved preference unchanged and return an error indication.
5. THE Key_Parity_Test SHALL pass only when every translation key present in either the French or the English message catalog exists in both catalogs.
6. IF a translation key exists in only one of the French and English message catalogs, THEN THE Key_Parity_Test SHALL fail and SHALL report the missing key and the catalog in which it is absent.
7. IF a JSX element contains a user-facing string literal instead of a translation key, THEN THE configured lint rule SHALL fail and SHALL report the file path and line of the offending literal.

### Requirement 16: OpenAPI-to-TypeScript client generation

**User Story:** As a web developer, I want a typed client generated from the API's OpenAPI document, so that the web and API stay in sync.

#### Acceptance Criteria

1. THE API_Service SHALL publish exactly one OpenAPI_Document per major version, per `docs/API-CONVENTIONS.md` Section 3.
2. WHEN the Web_App build runs, THE build SHALL generate the Generated_Client from the OpenAPI_Document of the targeted major version before compiling application code.
3. THE Web_App SHALL call the API_Service exclusively through the Generated_Client rather than through hand-written request code, per `docs/API-CONVENTIONS.md`.
4. IF the OpenAPI_Document is missing when the Web_App build runs, THEN THE build SHALL exit with a non-zero status and report that the OpenAPI_Document was not found.
5. IF the OpenAPI_Document is malformed when the Web_App build runs, THEN THE build SHALL exit with a non-zero status, produce no Generated_Client, and report the malformation.

### Requirement 17: Application shell and foundational pages

**User Story:** As a visitor, I want a landing page, auth pages, and a dashboard shell, so that the foundations are usable end to end.

#### Acceptance Criteria

1. THE Web_App SHALL provide an application shell, a landing page, the sign-up, sign-in, email verification, and password-reset pages, and an empty dashboard, each available under both `/fr` and `/en`.
2. WHEN a User submits an auth page, THE Web_App SHALL send the submission to API_Service through the Generated_Client.
3. IF an auth page submission to API_Service fails or returns an error, THEN THE Web_App SHALL remain on the submitted page, preserve the User-entered field values except password fields, and display an error indication describing the failure.
4. WHILE a visitor has no active Session, THE empty dashboard SHALL redirect that visitor to the sign-in page, and THE empty dashboard SHALL be reachable only by a User with an active Session.
5. WHERE a page built in this phase is evaluated with axe-core, THE page SHALL report no serious or critical accessibility violation.

<!-- =====================  CROSS-CUTTING  ===================== -->

### Requirement 18: Observability — health, readiness, and structured logging

**User Story:** As an operator, I want health and readiness endpoints and structured logs with request IDs, so that I can observe and orchestrate the services.

#### Acceptance Criteria

1. WHEN a Health_Endpoint of API_Service or Worker_Service receives a request, THE respective service SHALL return a liveness response that is determined independent of external dependencies.
2. WHEN a Readiness_Endpoint of API_Service or Worker_Service receives a request, THE respective service SHALL return a ready response only when all of that service's required dependencies are reachable.
3. IF one or more required dependencies of a service are unreachable when its Readiness_Endpoint is requested, THEN THE respective service SHALL return a not-ready response indicating the service is not ready.
4. THE API_Service SHALL emit structured JSON logs, and each log record for a request SHALL include a Request_ID.
5. WHEN API_Service receives a request without an inbound Request_ID, THE API_Service SHALL generate a Request_ID for that request.
6. THE API_Service SHALL keep internal diagnostics in logs and SHALL NOT place stack traces, SQL, or provider errors in responses, per `.kiro/steering/quality.md`.

### Requirement 19: API error format

**User Story:** As an API consumer, I want consistent, translated errors, so that clients can handle failures uniformly and learners see actionable messages.

#### Acceptance Criteria

1. THE API_Service SHALL return every non-2xx response as an `application/problem+json` Problem_Details document carrying at least `type`, `title`, and `status`, per `docs/API-CONVENTIONS.md` Section 1.
2. THE `status` member of a Problem_Details response SHALL equal the HTTP status code of that same response.
3. WHEN a response carries a learner-facing `detail`, THE API_Service SHALL render that `detail` in the request Locale, per `.kiro/steering/api-conventions.md`.
4. IF the request Locale is absent or unsupported, THEN THE API_Service SHALL render the learner-facing `detail` in a configured default Locale.
5. WHEN Pydantic v2 validation fails, THE API_Service SHALL return a Problem_Details response reporting one entry per rejected field with that field's location and reason in a problem extension member.
6. THE API_Service SHALL NOT place internal representations, stack traces, SQL, or provider errors in any member of a Problem_Details response.

### Requirement 20: Scope exclusions

**User Story:** As a maintainer, I want the foundations scope bounded, so that out-of-scope work is not pulled in prematurely.

#### Acceptance Criteria

1. THE foundations phase SHALL NOT implement learning-feature behavior for tracks, modules, lessons, quizzes, mastery, review, decay, placement, or final assessment, and SHALL produce no source or service artifact for those features beyond migrating their Data_Model tables.
2. THE foundations phase SHALL NOT implement hands-on labs, the sandbox, or the terminal WebSocket runtime, and SHALL produce no source or service artifact for them.
3. THE foundations phase SHALL NOT implement AI content generation or the content-review pipeline, and SHALL produce no source or service artifact for them beyond providing the LLMProvider Fake_Implementation.
4. THE foundations phase SHALL produce no Kubernetes deployment artifact, including Helm charts, manifests, network policies, and node-pool configuration.

### Requirement 21: Continuous integration

**User Story:** As a maintainer, I want CI to run on every push, so that defects and security issues are caught before merge.

#### Acceptance Criteria

1. WHEN a push is made to any branch of the repository, THE CI_Pipeline SHALL run the lint, typecheck, test, dependency-audit, and secret-scan steps.
2. IF any of the lint, typecheck, test, dependency-audit, or secret-scan steps reports an error, THEN THE CI_Pipeline SHALL fail with a non-zero status and report which step failed.
3. WHEN every CI_Pipeline step completes without error, THE CI_Pipeline SHALL succeed with a zero status.
4. WHEN the CI_Pipeline runs the test step, THE test step SHALL execute the end-to-end authentication tests covering sign-up, email verification, sign-in, sign-out, and password reset.
5. WHEN the CI_Pipeline runs the GitHub-OAuth tests, THE tests SHALL run against a mock OAuth_Provider and SHALL make no call to an external OAuth provider.

### Requirement 22: Steering fileMatch path correction

**User Story:** As a maintainer, I want steering fileMatch patterns to match the real paths created in this phase, so that conditional steering is included when the matching code is edited.

#### Acceptance Criteria

1. THE foundations phase SHALL verify, for each steering file created in Phase 0 that carries a `fileMatchPattern`, whether that pattern matches a real path introduced in this phase.
2. IF a steering `fileMatchPattern` does not match the real path it is intended to cover, THEN THE foundations phase SHALL update that `fileMatchPattern` to match the real path.
3. WHEN a steering `fileMatchPattern` is updated, THE update SHALL preserve the inclusion keys and SHALL retain parseable YAML front matter, per `.kiro/steering/working-agreements.md`.
4. IF a steering file created in Phase 0 has no `fileMatchPattern` but covers a real path introduced in this phase, THEN THE foundations phase SHALL add a `fileMatchPattern` to that steering file so that the pattern covers the real path, preserving the inclusion keys and parseable YAML front matter, per `.kiro/steering/working-agreements.md`.

### Requirement 23: Phase report

**User Story:** As a reviewer, I want a phase report, so that the foundations phase has an acceptance artifact listing what was built, how it is verified, and any manual checks.

#### Acceptance Criteria

1. THE foundations phase SHALL produce the Phase_Report at `docs/reports/phase-1.md`.
2. THE Phase_Report SHALL record, for each of the Compose_Stack readiness check, the migration apply-and-rollback check, the authentication end-to-end tests, the Key_Parity_Test, the axe-core accessibility check, and the CI_Pipeline steps, the command invoked and its recorded pass-or-fail outcome.
3. IF any recorded verification outcome is a failure, THEN THE Phase_Report SHALL record that failing outcome rather than omit it.
4. THE Phase_Report SHALL list the manual check of GitHub OAuth against the real GitHub provider as a manual verification item.

<!-- =====================  FAILURE SCENARIOS  ===================== -->

### Requirement 24: Failure — email delivery fails

**User Story:** As a learner, I want sign-up and password reset to behave safely when email cannot be delivered, so that I am not left in an inconsistent state.

#### Acceptance Criteria

1. IF the Mailer fails to deliver a verification email during sign-up, THEN THE API_Service SHALL keep the User in the unverified state, SHALL record the delivery failure in the logs, and SHALL return a translated Problem_Details response, per `.kiro/steering/quality.md`.
2. IF the Mailer fails to deliver a reset email during a password reset, THEN THE API_Service SHALL make no change to the User's password, SHALL record the delivery failure in the logs, and SHALL return a translated Problem_Details response.
3. WHERE a verification or reset email failed to send, THE API_Service SHALL allow the User to request the email again.

### Requirement 25: Failure — expired or already-used verification or reset link

**User Story:** As a learner, I want a clear outcome when a link is stale, so that I know to request a fresh one.

#### Acceptance Criteria

1. WHEN a Verification_Link or a Reset_Link is used successfully, THE API_Service SHALL invalidate that link so that a second use is treated as already used.
2. IF a User opens a Verification_Link that is expired as measured by the Clock or already used, THEN THE API_Service SHALL NOT verify the email and SHALL return a translated Problem_Details response offering to resend verification.
3. IF a User opens a Reset_Link that is expired as measured by the Clock or already used, THEN THE API_Service SHALL NOT change the password and SHALL return a translated Problem_Details response offering to restart the reset.

### Requirement 26: Failure — GitHub OAuth denied, failed, or email conflict

**User Story:** As a learner, I want GitHub sign-in failures handled safely, so that a denied or conflicting flow never produces an insecure link.

#### Acceptance Criteria

1. IF the GitHub OAuth flow is denied by the visitor or fails at the provider, THEN THE API_Service SHALL create no Session and no User, SHALL discard any partial authentication state, and SHALL return the visitor to the sign-in page with a translated message.
2. WHEN the GitHub OAuth flow returns a verified email that matches an existing User, THE API_Service SHALL link the GitHub identity to that User only after the User confirms the link within the configured confirmation window.
3. IF the GitHub OAuth flow returns an email that is unverified at GitHub, THEN THE API_Service SHALL refuse the link and SHALL leave the existing User's records unchanged.
4. IF the User actively declines the proposed GitHub identity link before the confirmation window expires, THEN THE API_Service SHALL refuse the link immediately and SHALL invalidate the pending confirmation token and state at once.
5. IF the confirmation window expires before the User confirms the proposed GitHub identity link, THEN THE API_Service SHALL refuse the link and SHALL leave the existing User's records unchanged.
6. IF the GitHub OAuth flow returns a state parameter that does not match the one issued, THEN THE API_Service SHALL reject the callback, create no Session, and discard any partial authentication state.

### Requirement 27: Failure — PostgreSQL or Redis unavailable

**User Story:** As an operator, I want the services to fail safely when a datastore is down, so that readiness reflects reality and requests do not leak internals.

#### Acceptance Criteria

1. IF PostgreSQL or Redis is unavailable when a service starts, THEN the service's Readiness_Endpoint SHALL report not-ready while its Health_Endpoint continues to report liveness.
2. IF PostgreSQL or Redis becomes unavailable during a request, THEN THE API_Service SHALL return a translated Problem_Details response with a server-error status and SHALL record the failure in the logs without exposing stack traces, connection strings, or host identifiers in the response body, per `.kiro/steering/quality.md`.
3. WHEN PostgreSQL and Redis become reachable again, THE Readiness_Endpoint SHALL report ready again.

### Requirement 28: Failure — session used after a password reset

**User Story:** As a security-conscious learner, I want other sessions invalidated after a reset, so that a compromised session cannot persist.

#### Acceptance Criteria

1. IF a request presents a Session that was revoked by a completed password reset (Requirement 9), THEN THE API_Service SHALL reject the request as unauthenticated, SHALL NOT refresh or reissue that Session, and SHALL return a translated Problem_Details response.
2. WHERE a password reset has completed, THE only Session that remains valid SHALL be the new Session established for the device completing the reset (Requirement 9).
