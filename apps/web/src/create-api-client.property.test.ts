import fc from 'fast-check';
import { describe, expect, it } from 'vitest';

import { createApiClient } from './create-api-client.js';

interface Call {
  url: string;
  method: string;
  credentials: RequestCredentials | undefined;
  headers: Headers;
}

function recorder(): { calls: Call[]; fetch: typeof fetch } {
  const calls: Call[] = [];
  const stub: typeof fetch = (input, init) => {
    calls.push({
      url: String(input),
      method: init?.method ?? 'GET',
      credentials: init?.credentials,
      headers: new Headers(init?.headers),
    });
    return Promise.resolve(new Response('{}', { status: 200 }));
  };
  return { calls, fetch: stub };
}

const unsafeMethod = fc.constantFrom('POST', 'PUT', 'PATCH', 'DELETE');
const token = fc.stringMatching(/^[A-Za-z0-9_-]{16,64}$/);

describe('createApiClient', () => {
  // Feature: api-route-wiring, Property 19: The generated client sends the CSRF pair and credentials
  // **Validates: Requirements 14.3**
  it('sends credentials and X-CSRF-Token equal to the cookie on unsafe calls', async () => {
    await fc.assert(
      fc.asyncProperty(unsafeMethod, token, async (method, value) => {
        const { calls, fetch } = recorder();
        const client = createApiClient({
          baseUrl: 'http://api.test/v1',
          fetch,
          browser: true,
          readCsrfCookie: () => value,
        });
        await client.call(method, '/anything', { body: '{}' });
        expect(calls).toHaveLength(1);
        expect(calls[0]?.credentials).toBe('include');
        expect(calls[0]?.headers.get('x-csrf-token')).toBe(value);
      }),
      { numRuns: 100 },
    );
  });

  // Feature: api-route-wiring, Property 19: The generated client sends the CSRF pair and credentials
  // **Validates: Requirements 14.3**
  it('requests GET /auth/csrf exactly once when the cookie is absent', async () => {
    await fc.assert(
      fc.asyncProperty(
        fc.array(unsafeMethod, { minLength: 1, maxLength: 5 }),
        token,
        async (methods, value) => {
          const { calls, fetch } = recorder();
          let cookie: string | undefined;
          const wrapped: typeof fetch = async (input, init) => {
            if (String(input).endsWith('/auth/csrf')) cookie = value;
            return fetch(input, init);
          };
          const client = createApiClient({
            baseUrl: 'http://api.test/v1',
            fetch: wrapped,
            browser: true,
            readCsrfCookie: () => cookie,
          });
          for (const method of methods) await client.call(method, '/x');
          const issuing = calls.filter((c) => c.url === 'http://api.test/v1/auth/csrf');
          expect(issuing).toHaveLength(1);
          expect(issuing[0]?.method).toBe('GET');
          expect(issuing[0]?.credentials).toBe('include');
          expect(calls.indexOf(issuing[0] as Call)).toBe(0);
          for (const call of calls.filter((c) => !c.url.endsWith('/auth/csrf'))) {
            expect(call.headers.get('x-csrf-token')).toBe(value);
            expect(call.credentials).toBe('include');
          }
        },
      ),
      { numRuns: 100 },
    );
  });

  it('does not add a CSRF header on safe calls', async () => {
    const { calls, fetch } = recorder();
    const client = createApiClient({
      baseUrl: 'http://api.test/v1',
      fetch,
      browser: true,
      readCsrfCookie: () => 'a'.repeat(32),
    });
    await client.call('GET', '/x');
    expect(calls[0]?.headers.get('x-csrf-token')).toBeNull();
    expect(calls[0]?.credentials).toBe('include');
  });

  it('on the server forwards the cookie and generates a matching fresh pair', async () => {
    await fc.assert(
      fc.asyncProperty(unsafeMethod, async (method) => {
        const { calls, fetch } = recorder();
        const client = createApiClient({
          baseUrl: 'http://api.test/v1',
          fetch,
          browser: false,
          cookie: 'edu_it_session=abc',
        });
        await client.call(method, '/x');
        const header = calls[0]?.headers.get('x-csrf-token') ?? '';
        expect(header.length).toBeGreaterThanOrEqual(16);
        expect(calls[0]?.headers.get('cookie')).toBe(`edu_it_session=abc; csrf_token=${header}`);
      }),
      { numRuns: 100 },
    );
  });
});
