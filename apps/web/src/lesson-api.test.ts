import { describe, expect, it } from 'vitest';

import { GeneratedClient } from './generated/api-client.js';
import { fetchLesson, stripLeadingTitle } from './lesson-api.js';
import { isLocale } from './locale.js';
import { parseRouteId } from './learning.js';
import { SESSION_COOKIE } from './session-shared.js';

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

  it('forwards the session reference as the session cookie when one is given', async () => {
    const { calls, client } = clientReturning(lesson);
    await fetchLesson('les-1', 'en', client, 'ref-123');
    const headers = calls[0]?.init?.headers as Record<string, string>;
    expect(headers.cookie).toBe(`${SESSION_COOKIE}=ref-123`);
  });

  it('sends no cookie header without a session reference (browser cookie jar)', async () => {
    const { calls, client } = clientReturning(lesson);
    await fetchLesson('les-1', 'en', client);
    const headers = calls[0]?.init?.headers as Record<string, string>;
    expect(headers.cookie).toBeUndefined();
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

describe('lab titles', () => {
  it('keeps well-formed lab links and drops malformed ones', async () => {
    const body = {
      ...lesson,
      lab_ids: ['lab-1'],
      labs: [{ lab_id: 'lab-1', title: 'Fix it' }, { lab_id: 2 }, null],
    };
    const result = await fetchLesson('les-1', 'en', clientReturning(body).client);
    expect(result?.labs).toEqual([{ lab_id: 'lab-1', title: 'Fix it' }]);
  });

  it('defaults to no lab titles for an API that predates the field', async () => {
    const result = await fetchLesson('les-1', 'en', clientReturning(lesson).client);
    expect(result?.labs).toEqual([]);
  });
});

describe('stripLeadingTitle', () => {
  it('removes an opening heading that repeats the title', () => {
    expect(stripLeadingTitle('# Reading permissions\n\nBody text.', 'Reading permissions')).toBe(
      'Body text.',
    );
  });

  it('removes the API placeholder body for a lesson without MDX', () => {
    expect(stripLeadingTitle('# Reading permissions', 'Reading permissions')).toBe('');
  });

  it('tolerates CRLF line endings, surrounding spaces and a closing hash', () => {
    expect(stripLeadingTitle('  #  Shell basics #\r\nNext', ' Shell basics ')).toBe('Next');
  });

  it('keeps a different opening heading and lower-level headings', () => {
    expect(stripLeadingTitle('# Overview\n\nText', 'Shell basics')).toBe('# Overview\n\nText');
    expect(stripLeadingTitle('## Shell basics\n\nText', 'Shell basics')).toBe(
      '## Shell basics\n\nText',
    );
  });

  it('keeps a body that does not start with a heading', () => {
    expect(stripLeadingTitle('Intro\n# Shell basics', 'Shell basics')).toBe(
      'Intro\n# Shell basics',
    );
  });
});
