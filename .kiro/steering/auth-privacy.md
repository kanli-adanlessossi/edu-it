---
inclusion: fileMatch
fileMatchPattern: '**/{auth,accounts,privacy,users}/**|**/*{auth,account,privacy,user,session,oauth,login,identity}*'
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
