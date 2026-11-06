// Server-side session resolution for the web app (Req 8.5, 17.4, A4).
//
// The client holds only an opaque session reference (a cookie); the
// authoritative session state lives server-side (A4). To gate the dashboard
// (Req 17.4) the web app must ask the API whether the request's reference
// resolves to a valid, active Session — it never decides locally. The resolve
// call goes through the generated client (Req 16.3); this module adapts the
// raw outcome to the `SessionState` the pure `resolveDashboardAccess` gate
// consumes.
//
// This module depends on `next/headers` and is therefore server-only. The
// server-agnostic `SESSION_COOKIE` constant and `sessionStateFromResponse`
// helper live in `./session-shared.js` so client-reachable modules never pull
// `next/headers` into the client bundle.

import { cookies } from 'next/headers';

import { createApiClient } from './create-api-client.js';
import type { GeneratedClient } from './generated/api-client.js';
import { type SessionState } from './dashboard-access.js';
import { SESSION_COOKIE, sessionStateFromResponse } from './session-shared.js';

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

  const generated = client ?? createApiClient();
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
