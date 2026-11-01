// Regression for api-route-wiring task 13.3 (Req 17.2, 17.3): the server-rendered Lab page
// called GET /labs/{id} with no Session_Cookie, so the API answered 401. `fetchLab` must
// forward the cookie header it is given, like the quiz and project readers do.
import { describe, expect, it } from 'vitest';

import { createApiClient } from './create-api-client.js';
import { fetchLab } from './lab-api.js';

function recordingClient(seen: Headers[]) {
  return createApiClient({
    baseUrl: 'http://api.test/v1',
    browser: false,
    fetch: async (_input, init) => {
      const headers = new Headers(init?.headers);
      seen.push(headers);
      const authed = (headers.get('cookie') ?? '').includes('edu_it_session=abc');
      return authed
        ? new Response(
            JSON.stringify({ lab_id: 'lab.x', kind: 'terminal', title: 'X', locale: 'en', tasks: [] }),
            { status: 200, headers: { 'content-type': 'application/json' } },
          )
        : new Response(JSON.stringify({ detail: 'Sign in required' }), { status: 401 });
    },
  });
}

describe('fetchLab', () => {
  it('forwards the session cookie so the API accepts the request', async () => {
    const seen: Headers[] = [];
    const lab = await fetchLab('lab.x', 'en', 'edu_it_session=abc', recordingClient(seen));
    expect(lab?.labId).toBe('lab.x');
    expect(seen[0]?.get('cookie')).toContain('edu_it_session=abc');
  });

  it('rejects with a 401 LabApiError when no cookie is forwarded', async () => {
    await expect(fetchLab('lab.x', 'en', undefined, recordingClient([]))).rejects.toMatchObject({
      status: 401,
    });
  });
});
