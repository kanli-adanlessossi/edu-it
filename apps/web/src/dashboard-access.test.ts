import { describe, expect, it } from 'vitest';

import {
  hasActiveSession,
  resolveDashboardAccess,
  SIGN_IN_PATH,
  signInRedirectPath,
} from './dashboard-access.js';
import { sessionStateFromResponse } from './session.js';

// Targeted unit tests for the pure dashboard session-gating logic (task 24.1),
// covering the specific session states behind Req 17.4.

describe('hasActiveSession', () => {
  it('is true only for an active session', () => {
    expect(hasActiveSession('active')).toBe(true);
    expect(hasActiveSession('none')).toBe(false);
    expect(hasActiveSession('expired')).toBe(false);
    expect(hasActiveSession('revoked')).toBe(false);
  });
});

describe('signInRedirectPath', () => {
  it('builds the locale-prefixed sign-in path', () => {
    expect(signInRedirectPath('fr')).toBe(`/fr${SIGN_IN_PATH}`);
    expect(signInRedirectPath('en')).toBe('/en/sign-in');
  });
});

describe('resolveDashboardAccess', () => {
  it('allows an active session', () => {
    expect(resolveDashboardAccess('active', 'fr')).toEqual({ kind: 'allow' });
  });

  it('redirects a visitor with no session to the locale sign-in page', () => {
    expect(resolveDashboardAccess('none', 'en')).toEqual({
      kind: 'redirect',
      to: '/en/sign-in',
    });
  });

  it('redirects expired and revoked sessions to sign-in', () => {
    expect(resolveDashboardAccess('expired', 'fr')).toEqual({
      kind: 'redirect',
      to: '/fr/sign-in',
    });
    expect(resolveDashboardAccess('revoked', 'fr')).toEqual({
      kind: 'redirect',
      to: '/fr/sign-in',
    });
  });
});

describe('sessionStateFromResponse', () => {
  it('maps a 2xx to active and anything else to no active session', () => {
    expect(sessionStateFromResponse(200)).toBe('active');
    expect(sessionStateFromResponse(204)).toBe('active');
    expect(sessionStateFromResponse(401)).toBe('none');
    expect(sessionStateFromResponse(500)).toBe('none');
  });
});
