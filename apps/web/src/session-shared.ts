// Server-agnostic session helpers for the web app (Req 8.5, 17.4, A4).
//
// These two exports carry no server dependency, so they live apart from the
// `next/headers`-dependent `resolveSessionState` (in `./session.js`). Keeping
// them here lets client-reachable modules (e.g. the path recommendation
// adapter) import the cookie name without transitively pulling `next/headers`
// into the client bundle, which Next.js rejects.

import { type SessionState } from './dashboard-access.js';

/** Cookie name holding the opaque session reference (enumerated in .env.example). */
export const SESSION_COOKIE = 'edu_it_session';

/**
 * Map the API's session-resolution response to a {@link SessionState}. A 2xx
 * means the reference resolves to a valid active Session; a 401 means no active
 * Session (absent, expired, or revoked), which the dashboard gate treats
 * uniformly as "no active session" and redirects to sign-in (Req 17.4).
 */
export function sessionStateFromResponse(status: number): SessionState {
  return status >= 200 && status < 300 ? 'active' : 'none';
}
