import { describe, expect, it } from 'vitest';

import { GeneratedClient } from './generated/api-client.js';
import { fetchLesson } from './lesson-api.js';
import { isLocale } from './locale.js';
import { parseRouteId } from './learning.js';

function clientReturning(body: unknown, ok = true) {
  const calls: { url: string; init?: RequestInit }[] = [];
  const fetchStub = ((url: string, init?: RequestInit) => {
    calls.push({ url, init });
    return Promise.resolve(new Response(JSON.stringify(body), { status: ok ? 200 : 404 }));
  }) as typeof fetch;
  return {
    calls,
    client: new GeneratedClient({ baseUrl: 'http://api.test/v1', fetch: fetchStub }),
  };
}

const lesson = { stable_id: 'les-1', title: 'T', body: '# hi', locale: 'en' };

// Validates: Requirements 19.3
describe('lesson locale handling', () => {
  it.each(['fr', 'en'])('requests the lesson in the active locale (%s)', async (locale) => {
    const { calls, client } = clientReturning(lesson);
    await fetchLesson('les-1', locale, client);
    expect(calls[0]?.url).toBe(`http://api.test/v1/lessons/les-1?locale=${locale}`);
    expect(calls[0]?.init?.method).toBe('GET');
    expect((calls[0]?.init?.headers as Record<string, string>)['accept-language']).toBe(locale);
  });

  it('returns the served lesson even when the API fell back to English', async () => {
    const { client } = clientReturning(lesson);
    expect((await fetchLesson('les-1', 'fr', client))?.locale).toBe('en');
  });

  it('returns undefined for a missing or malformed lesson', async () => {
    expect(await fetchLesson('x', 'en', clientReturning({}, false).client)).toBeUndefined();
    expect(await fetchLesson('x', 'en', clientReturning({ nope: 1 }).client)).toBeUndefined();
  });

  it('encodes the lesson id and rejects unsupported locales / malformed ids', async () => {
    const { calls, client } = clientReturning(lesson);
    await fetchLesson('a/b', 'en', client);
    expect(calls[0]?.url).toContain('/lessons/a%2Fb?');
    expect(isLocale('de')).toBe(false);
    expect(parseRouteId('a b')).toBeUndefined();
  });
});
