import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// The enroll server action uses the server client (api-route-wiring Req 14.13):
// it forwards the session cookie, generates a fresh CSRF pair, and posts the
// enrollment; it redirects to the locale-prefixed track on success.

vi.mock('next/navigation', () => ({
  redirect: (to: string) => {
    throw new Error(`REDIRECT:${to}`);
  },
}));
vi.mock('next/headers', () => ({
  cookies: () => Promise.resolve({ get: () => ({ value: 'sess-ref' }) }),
}));

const { enrollAction } = await import('./app/[locale]/dashboard/enroll-action.js');

interface Seen {
  url: string;
  method: string;
  headers: Headers;
}

let seen: Seen[];
let enrollStatus: number;

beforeEach(() => {
  seen = [];
  enrollStatus = 201;
  vi.stubGlobal('fetch', (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    seen.push({ url, method: init?.method ?? 'GET', headers: new Headers(init?.headers) });
    if (url.endsWith('/auth/session')) return Promise.resolve(new Response('{}', { status: 200 }));
    return Promise.resolve(
      new Response(
        JSON.stringify({
          stable_id: 'enr-1',
          track_id: 'track-1',
          enrolled_at: '2025-01-01T00:00:00Z',
        }),
        { status: enrollStatus, headers: { 'content-type': 'application/json' } },
      ),
    );
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function form(fields: Record<string, string>): FormData {
  const data = new FormData();
  for (const [k, v] of Object.entries(fields)) data.set(k, v);
  return data;
}

describe('enrollAction', () => {
  it('posts with the session cookie and a matching CSRF cookie/header, then redirects', async () => {
    await expect(enrollAction(form({ locale: 'en', trackId: 'track-1' }))).rejects.toThrow(
      'REDIRECT:/en/tracks/track-1',
    );
    const post = seen.find((s) => s.method === 'POST')!;
    expect(post.url).toMatch(/\/enrollments$/);
    const cookie = post.headers.get('cookie') ?? '';
    const token = post.headers.get('x-csrf-token');
    expect(cookie).toContain('edu_it_session=sess-ref');
    expect(token).toBeTruthy();
    expect(cookie).toContain(`csrf_token=${token}`);
  });

  it('redirects to the enroll error when the API refuses', async () => {
    enrollStatus = 403;
    await expect(enrollAction(form({ locale: 'fr', trackId: 'track-1' }))).rejects.toThrow(
      'REDIRECT:/fr/dashboard?error=enroll',
    );
  });

  it('redirects to the default dashboard for an unknown locale without calling the API', async () => {
    await expect(enrollAction(form({ locale: 'de', trackId: 'track-1' }))).rejects.toThrow(
      'REDIRECT:/fr/dashboard',
    );
    expect(seen).toHaveLength(0);
  });
});
