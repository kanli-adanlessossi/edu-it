import { describe, expect, it } from 'vitest';

import { createAuthApi } from './api-client.js';
import { AuthSubmitError } from './auth-forms.js';
import { createApiClient } from './create-api-client.js';

// Unit tests for the auth adapter over the generated client (api-route-wiring
// Req 14.5, 14.6, 14.8, 14.11): the AUTH_PATHS, the `redirect_to` wire member
// returned as `redirectTo`, and the `verifyEmail` outcomes.

interface Seen {
  url: string;
  method: string;
  headers: Headers;
  body: string | undefined;
}

function adapter(respond: () => Response, locale = 'en') {
  const seen: Seen[] = [];
  const stub: typeof fetch = (input, init) => {
    seen.push({
      url: String(input),
      method: init?.method ?? 'GET',
      headers: new Headers(init?.headers),
      body: typeof init?.body === 'string' ? init.body : undefined,
    });
    return Promise.resolve(respond());
  };
  const client = createApiClient({ baseUrl: 'http://api.test/v1', fetch: stub, browser: false });
  return { seen, api: createAuthApi(locale, client) };
}

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  });

describe('createAuthApi.submit', () => {
  it.each([
    ['sign-up', '/auth/sign-up'],
    ['sign-in', '/auth/sign-in'],
    ['verify-email', '/auth/verify-email/resend'],
    ['reset-password', '/auth/password-reset'],
  ] as const)('posts the %s page to %s', async (page, path) => {
    const { seen, api } = adapter(() => json({}));
    await api.submit(page, { email: 'a@b.test' });
    expect(seen[0]?.method).toBe('POST');
    expect(seen[0]?.url).toBe(`http://api.test/v1${path}`);
    expect(seen[0]?.body).toBe(JSON.stringify({ email: 'a@b.test' }));
  });

  it('maps the wire member redirect_to to redirectTo', async () => {
    const { api } = adapter(() => json({ redirect_to: '/dashboard' }));
    await expect(api.submit('sign-in', { email: 'a@b.test', password: 'x' })).resolves.toEqual({
      redirectTo: '/dashboard',
    });
  });

  it('does not accept the internal name redirectTo from the wire', async () => {
    const { api } = adapter(() => json({ redirectTo: '/dashboard' }));
    await expect(api.submit('sign-in', {})).resolves.toEqual({});
  });

  it('returns no redirect for a body-less success', async () => {
    const { api } = adapter(() => new Response(null, { status: 202 }));
    await expect(api.submit('sign-up', {})).resolves.toEqual({});
  });

  it('sends the locale as accept-language and a fresh CSRF pair on the server', async () => {
    const { seen, api } = adapter(() => json({}), 'fr');
    await api.submit('sign-up', {});
    const headers = seen[0]!.headers;
    expect(headers.get('accept-language')).toBe('fr');
    const token = headers.get('x-csrf-token');
    expect(token).toBeTruthy();
    expect(headers.get('cookie')).toContain(`csrf_token=${token}`);
  });

  it('surfaces only the Problem_Details detail on a non-2xx response', async () => {
    const { api } = adapter(() => json({ detail: 'Adresse invalide', stack: 'secret' }, 422));
    const error: unknown = await api.submit('sign-up', {}).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(AuthSubmitError);
    expect((error as AuthSubmitError).message).toBe('Adresse invalide');
    expect(JSON.stringify(error)).not.toContain('secret');
  });
});

describe('createAuthApi.verifyEmail', () => {
  it('posts the token to /auth/verify-email and reports verified on 2xx', async () => {
    const { seen, api } = adapter(() => new Response(null, { status: 204 }));
    await expect(api.verifyEmail('tok')).resolves.toBe('verified');
    expect(seen[0]?.url).toBe('http://api.test/v1/auth/verify-email');
    expect(seen[0]?.method).toBe('POST');
    expect(seen[0]?.body).toBe(JSON.stringify({ token: 'tok' }));
  });

  it('reports invalid on a 400 invalid-or-expired link', async () => {
    const { api } = adapter(() => json({ detail: 'expired' }, 400));
    await expect(api.verifyEmail('tok')).resolves.toBe('invalid');
  });

  it('throws on any other failure', async () => {
    const { api } = adapter(() => json({ detail: 'boom' }, 500));
    await expect(api.verifyEmail('tok')).rejects.toBeInstanceOf(AuthSubmitError);
  });
});

describe('createAuthApi.completePasswordReset', () => {
  it('posts token and new_password to /auth/password-reset/complete', async () => {
    const { seen, api } = adapter(() => json({ redirect_to: '/dashboard' }));
    await expect(api.completePasswordReset('tok', 'new pass phrase')).resolves.toEqual({
      status: 'completed',
      redirectTo: '/dashboard',
    });
    expect(seen[0]?.method).toBe('POST');
    expect(seen[0]?.url).toBe('http://api.test/v1/auth/password-reset/complete');
    expect(seen[0]?.body).toBe(JSON.stringify({ token: 'tok', new_password: 'new pass phrase' }));
  });

  it('reports completed without a redirect for a body-less success', async () => {
    const { api } = adapter(() => new Response(null, { status: 204 }));
    await expect(api.completePasswordReset('tok', 'x')).resolves.toEqual({ status: 'completed' });
  });

  it('reports invalid on a 400 invalid-or-expired link', async () => {
    const { api } = adapter(() => json({ detail: 'expired' }, 400));
    await expect(api.completePasswordReset('tok', 'x')).resolves.toEqual({ status: 'invalid' });
  });

  it('throws the translated detail on a weak password (422)', async () => {
    const { api } = adapter(() => json({ detail: 'Mot de passe trop court' }, 422), 'fr');
    const error: unknown = await api.completePasswordReset('tok', 'x').catch((e: unknown) => e);
    expect(error).toBeInstanceOf(AuthSubmitError);
    expect((error as AuthSubmitError).message).toBe('Mot de passe trop court');
  });
});

describe('createAuthApi GitHub link confirmation', () => {
  it('confirm posts to /auth/github/confirm and maps redirect_to', async () => {
    const { seen, api } = adapter(() => json({ redirect_to: '/dashboard' }));
    await expect(api.confirmGitHubLink()).resolves.toEqual({ redirectTo: '/dashboard' });
    expect(seen[0]?.method).toBe('POST');
    expect(seen[0]?.url).toBe('http://api.test/v1/auth/github/confirm');
  });

  it('confirm throws on a failure response', async () => {
    const { api } = adapter(() => json({ detail: 'gone' }, 400));
    await expect(api.confirmGitHubLink()).rejects.toBeInstanceOf(AuthSubmitError);
  });

  it('decline posts to /auth/github/decline', async () => {
    const { seen, api } = adapter(() => new Response(null, { status: 204 }));
    await expect(api.declineGitHubLink()).resolves.toBeUndefined();
    expect(seen[0]?.method).toBe('POST');
    expect(seen[0]?.url).toBe('http://api.test/v1/auth/github/decline');
  });

  it('decline throws on a failure response', async () => {
    const { api } = adapter(() => json({ detail: 'gone' }, 400));
    await expect(api.declineGitHubLink()).rejects.toBeInstanceOf(AuthSubmitError);
  });
});
