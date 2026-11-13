---
inclusion: fileMatch
fileMatchPattern: '**/{auth,accounts,privacy,users}/**|**/*{auth,account,privacy,user,session,oauth,login,identity,signin,signup,sign-in,sign-up,sign_in,sign_up,password,reset,verify,verification,credential,csrf}*'
---

# Authentication and Privacy

This steering file is the single source of truth for the EDU-IT authentication and privacy
rules. It is included conditionally when working on auth, accounts, privacy, and users code (see
the `fileMatchPattern` in the front matter). The authentication and privacy rules below are
transcribed verbatim from the project rules; all numbers and normative wording are preserved
unchanged. Where a rule is not self-explanatory, a one-line reason is added and marked `Reason:`.

## Authentication and Sessions

- `apps/api` is the FastAPI boundary for authentication and sessions, learning endpoints, serves
  MDX source per locale, hosts the authenticated terminal WebSocket, enqueues jobs. Validates
  with Pydantic v2.
- The learner connects to the API over an authenticated WebSocket for the terminal.
  Reason: the terminal TTY is attached only after the learner is authenticated on the WebSocket.
- Session storage uses a server-side session backing.
  Reason: the concrete server-side session backing is recorded as an ADR (session storage).

## Identity Model

The identity entity group includes:

- `user`, `session`, `oauth_link`, `role_assignment` (learner, reviewer with per-track
  expertise, author, admin).

## Session Cookie, CSRF and Cross-Origin Contract
- Session_Cookie attributes: `HttpOnly`, `SameSite=Lax`, `Path=/`, lifetime equal to
  `SESSION_DURATION`. On sign-out the Session_Cookie is expired. It is never set from a response
  that fails with a Problem_Details document.
- Where `EDU_IT_ENV` is `production`, the `Secure` attribute is set on the Session_Cookie and
  on the `csrf_token` cookie. Where `COOKIE_DOMAIN` is configured, it sets `Domain` on both.
- `GET /v1/auth/csrf` sets a `csrf_token` cookie (at least 32 URL-safe characters, not
  `HttpOnly` so scripts can read it, `SameSite=Lax`, `Path=/`, lifetime `SESSION_DURATION`)
  and returns the same token in the body; a well-formed existing cookie value is returned unchanged.
- Every unsafe request requires the CSRF pair (cookie plus `X-CSRF-Token`), including sign-up and
  sign-in. Safe, idempotent methods need no CSRF token. A missing, malformed or invalid token is
  rejected with Problem_Details and no state change (#[[file:.kiro/steering/api-conventions.md]]).
- Cross-origin: origins in `LAB_ALLOWED_WEB_ORIGINS` are answered with credentials allowed, the
  methods of the route table plus `OPTIONS` (including `PUT`), and the headers `Content-Type`,
  `X-CSRF-Token`, `Idempotency-Key` and `Accept-Language`. Other origins get no cross-origin
  allow header.
  Reason: `SameSite=Lax` cookies and the script read of `csrf_token` require Web_App and
  API_Service to be deployed same-site (one registrable domain or `localhost`).
## Sign-in Rate Limiting
- While failed sign-in attempts for an identifier exceed the configured threshold within the
  configured window, further attempts are rejected with a translated Problem_Details response
  without evaluating credentials, and no Session is established even for valid credentials.
- The window elapses by the injected `Clock`; threshold and window are business constants in
  `domain/config.py` (#[[file:.kiro/steering/structure.md]]).
## WebSocket Ownership and Terminal I/O

- If a terminal WebSocket connection drops, the system re-verifies session ownership on
  reconnect, presents a reconnect indication, drains active sockets on graceful shutdown, and
  persists no terminal I/O.
- Graceful shutdown drains terminal WebSockets with a reconnect message.

## Personal Data: Export, Deletion, and Retention

- Every table holding personal data must be covered by export, deletion and retention.
  Reason: each personal-data table in the data model carries an export path, a deletion path, and
  a retention policy.

## Mail and Learner-Facing Messaging

- `Mailer`: console dev, SMTP prod; FR/EN templates.

## Data Residency and LLM Requests

- LLM provider and data residency: provider behind `LLMProvider`, DPA and EU residency.
- Specify what data is allowed in LLM requests.
  Reason: the AI-content design states which data may be included in LLM requests so personal
  data is not sent to the provider beyond what is allowed.

## Learner-Facing Error Privacy

- Learner-facing errors are translated and actionable; details stay in logs.
  Reason: detailed error information is kept server-side in logs rather than exposed to learners.
