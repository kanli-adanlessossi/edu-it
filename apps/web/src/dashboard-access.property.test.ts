import fc from 'fast-check';
import { describe, expect, it } from 'vitest';

import {
  hasActiveSession,
  resolveDashboardAccess,
  signInRedirectPath,
  type SessionState,
} from './dashboard-access.js';
import { LOCALES, type Locale } from './locale.js';

// Property-based test for dashboard session gating (task 24.3).
//
// Feature: foundations, Property 25: The dashboard is reachable exactly with an active session
//
// Property 25 (design.md): *For any* session state, a request for the empty
// dashboard returns the dashboard if and only if a valid active Session is
// present; otherwise it redirects to the sign-in page.
//
// Validates: Requirements 17.4

const SESSION_STATES: readonly SessionState[] = ['active', 'none', 'expired', 'revoked'];

const sessionState: fc.Arbitrary<SessionState> = fc.constantFrom(...SESSION_STATES);
const localeArb: fc.Arbitrary<Locale> = fc.constantFrom(...LOCALES);

describe('Feature: foundations, Property 25: The dashboard is reachable exactly with an active session', () => {
  it('allows the dashboard iff an active session is present, else redirects to sign-in', () => {
    fc.assert(
      fc.property(sessionState, localeArb, (state, locale) => {
        const access = resolveDashboardAccess(state, locale);
        const active = hasActiveSession(state);

        if (active) {
          // Reachable exactly with an active session.
          expect(state).toBe('active');
          expect(access.kind).toBe('allow');
        } else {
          // Every non-active state redirects to the locale's sign-in page.
          expect(access.kind).toBe('redirect');
          if (access.kind === 'redirect') {
            expect(access.to).toBe(signInRedirectPath(locale));
            // The redirect stays inside the visitor's locale route tree.
            expect(access.to.startsWith(`/${locale}/`)).toBe(true);
            expect(access.to.endsWith('/sign-in')).toBe(true);
          }
        }

        // The decision is exactly "allow iff active": no state both allows and
        // is non-active, and none redirects while active.
        return (access.kind === 'allow') === active;
      }),
      { numRuns: 200 },
    );
  });
});
