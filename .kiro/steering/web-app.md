---
inclusion: fileMatch
fileMatchPattern: "apps/web/**|**/*.{tsx,mdx}|**/openapi/**"
---
# Web App Conventions

This steering file is the single source of truth for EDU-IT web app rules: API access through the
generated client, bilingual routing and key parity, and accessibility. The rules are transcribed
from the foundations, api-route-wiring and hands-on-labs specs; numbers and normative wording are
preserved.

## Generated API Client

- The API publishes exactly one OpenAPI document per major version
  (#[[file:.kiro/steering/api-conventions.md]]).
- The Web_App build regenerates the Generated_Client from that document before compiling
  application code. A missing or malformed document fails the build with a non-zero status (and
  produces no client when malformed).
- The Web_App calls the API only through the Generated_Client, never through hand-written request
  code.
- The Generated_Client sends `credentials: 'include'` on browser calls and `X-CSRF-Token` (the
  `csrf_token` cookie value) on every unsafe call, requesting `GET /v1/auth/csrf` first when the
  cookie is absent. A Next.js server action forwards the Session_Cookie and sends a freshly
  generated CSRF pair (#[[file:.kiro/steering/auth-privacy.md]]).
- `NEXT_PUBLIC_API_BASE_URL` is the API root including `/v1`, and `.env.example` carries it.
- The API returns locale-free paths (for example `redirect_to`); the Web_App prefixes the current
  locale before navigating.

## Bilingual Routing

- Pages are served under `/fr` and `/en` only. The language switcher keeps page path and query.
- A signed-in learner's locale preference is saved by the API and rendered on the next visit.
- Every new user-facing string is added to both the `fr` and `en` catalogs. The Key_Parity_Test
  fails, naming the key and the missing catalog, when a key exists in only one catalog.
- A lint rule fails on a user-facing string literal in JSX instead of a translation key.

## Forms and Auth Pages

- A failed auth page submission keeps the learner on the page, preserves entered values except
  passwords, and shows an error indication.
- The dashboard redirects visitors without an active Session to sign-in.

## Accessibility

- Pages are checked with axe-core and report no serious or critical violation.
- The Lab_View has no axe-core violations in `/fr` and `/en`, in the ready, grading, results,
  error and reconnecting states.
- The Lab_View announces Check results, timer warnings and reconnect status through live regions,
  shows a visible keyboard focus indicator on every interactive element, and never traps keyboard
  focus in the terminal or the editor.
- Full WCAG conformance still needs manual testing with assistive technologies and expert review.
