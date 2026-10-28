// Server-side session resolution for the web app (Req 8.5, 17.4, A4).
//
// The client holds only an opaque session reference (a cookie); the
// authoritative session state lives server-side (A4). To gate the dashboard
// (Req 17.4) the web app must ask the API whether the request's reference
// resolves to a valid, active Session — it never decides locally. The resolve
// call goes through the generated client (Req 16.3); this module adapts the
// raw outcome to the `SessionState` the pure `resolveDashboardAccess` gate
// consumes.

import { cookies } from 'next/headers';

import { GeneratedClient } from './generated/api-client.js';
import { type SessionState } from './dashboard-access.js';

/** Cookie name holding the opaque session reference (enumerated in .env.example). */
export const SESSION_COOKIE = 'edu_it_session';

/** The API base URL the generated client targets, from the environment. */
function apiBaseUrl(): string {
  const fromEnv = process.env.NEXT_PUBLIC_API_BASE_URL;
  return fromEnv && fromEnv.length > 0 ? fromEnv : 'http://localhost:8000/v1';
}

/**
 * Map the API's session-resolution response to a {@link SessionState}. A 2xx
 * means the reference resolves to a valid active Session; a 401 means no active
 * Session (absent, expired, or revoked), which the dashboard gate treats
 * uniformly as "no active session" and redirects to sign-in (Req 17.4).
 */
export function sessionStateFromResponse(status: number): SessionState {
  return status >= 200 && status < 300 ? 'active' : 'none';
}

/**
 * Resolve the current request's session state server-side (Req 17.4).
 *
 * When no session cookie is present the request has no Session at all. When a
 * reference is present it is resolved through the generated client; any failure
 * to reach or satisfy the API is treated conservatively as "no active session"
 * so the dashboard never renders without a confirmed active Session.
 */
export async function resolveSessionState(client?: GeneratedClient): Promise<SessionState> {
  const cookieStore = await cookies();
  const reference = cookieStore.get(SESSION_COOKIE)?.value;
  if (reference === undefined || reference.length === 0) {
    return 'none';
  }

  const generated = client ?? new GeneratedClient({ baseUrl: apiBaseUrl() });
  try {
    const response = await generated.call('GET', '/auth/session', {
      headers: { cookie: `${SESSION_COOKIE}=${reference}` },
    });
    return sessionStateFromResponse(response.status);
  } catch {
    // Unreachable API or network error: fail closed (no active session).
    return 'none';
  }
}
