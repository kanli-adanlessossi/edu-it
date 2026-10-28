// Dashboard session gating (Req 17.4).
//
// WHILE a visitor has no active Session, the empty dashboard redirects that
// visitor to the sign-in page, and the dashboard is reachable ONLY by a User
// with an active Session (Req 17.4). The server-side Session is authoritative
// (A4): the client holds only an opaque session reference, and whether that
// reference resolves to a valid, active Session is decided server-side.
//
// The gating decision is a pure function of the resolved session state, so it
// lives here rather than inside the dashboard React component: this lets
// Property 25 ("the dashboard is reachable exactly with an active session")
// drive it directly with arbitrary session states, and lets the page render the
// decision without duplicating the branch. The page performs the actual
// server-side session resolution (through the generated client) and feeds the
// outcome to {@link resolveDashboardAccess}.

import { type Locale } from './locale.js';

/**
 * The resolved state of the opaque session reference a dashboard request
 * carries, as decided server-side (A4):
 *   - 'active'   — a valid, unexpired, unrevoked Session is present.
 *   - 'none'     — no session reference was presented.
 *   - 'expired'  — a reference was presented but the Session has expired.
 *   - 'revoked'  — a reference was presented but the Session was revoked
 *                  (e.g. by sign-out or a completed password reset, Req 28.1).
 *
 * Only 'active' authenticates the request; every other state is treated as
 * having no active Session (Req 8.5, 17.4).
 */
export type SessionState = 'active' | 'none' | 'expired' | 'revoked';

/** The sign-in page path within a locale route tree (without the locale). */
export const SIGN_IN_PATH = '/sign-in';

/** The dashboard access decision. */
export type DashboardAccess =
  { readonly kind: 'allow' } | { readonly kind: 'redirect'; readonly to: string };

/** Returns true exactly when the session state authenticates the request. */
export function hasActiveSession(state: SessionState): boolean {
  return state === 'active';
}

/**
 * Build the locale-prefixed sign-in path a blocked dashboard visitor is
 * redirected to, e.g. `/fr/sign-in` (Req 17.4). The redirect stays within the
 * visitor's current locale route tree so the sign-in page renders in the same
 * language.
 */
export function signInRedirectPath(locale: Locale): string {
  return `/${locale}${SIGN_IN_PATH}`;
}

/**
 * Decide whether a dashboard request is allowed (Req 17.4).
 *
 * The dashboard is returned if and only if the request carries a valid active
 * Session; for every other session state the decision is a redirect to the
 * locale's sign-in page. This is the single source of truth the dashboard page
 * consults, so the "reachable exactly with an active session" invariant holds
 * for every possible session state (Property 25).
 */
export function resolveDashboardAccess(state: SessionState, locale: Locale): DashboardAccess {
  if (hasActiveSession(state)) {
    return { kind: 'allow' };
  }
  return { kind: 'redirect', to: signInRedirectPath(locale) };
}
