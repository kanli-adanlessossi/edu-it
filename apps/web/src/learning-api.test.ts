import { describe, expect, it } from 'vitest';
import { GeneratedClient } from './generated/api-client.js';
import { createLearningApi } from './learning-api.js';

function clientReturning(body: unknown) {
  const calls: { url: string; init?: RequestInit }[] = [];
  const fetchStub = ((url: string, init?: RequestInit) => {
    calls.push({ url, init });
    return Promise.resolve(new Response(JSON.stringify(body), { status: 200 }));
  }) as typeof fetch;
  return {
    calls,
    client: new GeneratedClient({ baseUrl: 'http://api.test/v1', fetch: fetchStub }),
  };
}

const progress = {
  enrollment_id: 'enr-1',
  track_id: 'track-1',
  modules: [
    {
      module_id: 'module-1',
      ordinal: 1,
      state: 'unlocked',
      tested_out: false,
      completions: [],
      lessons: [{ lesson_id: 'les-1', title: 'Perms', quiz_id: 'quiz-1', lab_ids: ['lab-1'] }],
    },
    { module_id: 'module-2', ordinal: 2, state: 'locked', tested_out: false, completions: [] },
  ],
  mastery: [],
};

describe('createLearningApi.getProgress', () => {
  it.each(['fr', 'en'])('asks for lesson titles in the active locale (%s)', async (locale) => {
    const { calls, client } = clientReturning(progress);
    await createLearningApi(locale, 'ref', client).getProgress('track 1');
    expect(calls[0]?.url).toBe(`http://api.test/v1/progress?track_id=track%201&locale=${locale}`);
  });

  it('exposes each module lesson with its quiz and labs', async () => {
    const { client } = clientReturning(progress);
    const result = await createLearningApi('en', 'ref', client).getProgress('track-1');
    expect(result.modules[0]?.lessons).toEqual([
      { lesson_id: 'les-1', title: 'Perms', quiz_id: 'quiz-1', lab_ids: ['lab-1'] },
    ]);
  });

  it('reads the track and module titles', async () => {
    const titled = {
      ...progress,
      track_title: 'Linux',
      modules: [{ ...progress.modules[0], title: 'Permissions' }],
    };
    const result = await createLearningApi('en', 'ref', clientReturning(titled).client).getProgress(
      'track-1',
    );
    expect(result.track_title).toBe('Linux');
    expect(result.modules[0]?.title).toBe('Permissions');
  });

  it('accepts a module from an API that predates the lessons field', async () => {
    const { client } = clientReturning(progress);
    const result = await createLearningApi('en', 'ref', client).getProgress('track-1');
    expect(result.modules[1]?.lessons).toBeUndefined();
  });
});

describe('createLearningApi.listTracks', () => {
  it.each(['fr', 'en'])(
    'lists the catalog tracks titled in the active locale (%s)',
    async (locale) => {
      const page = { items: [{ track_id: 'track-1', title: 'Linux' }], next_cursor: null };
      const { calls, client } = clientReturning(page);
      const tracks = await createLearningApi(locale, 'ref', client).listTracks();
      expect(calls[0]?.url).toBe(`http://api.test/v1/tracks?locale=${locale}&limit=50`);
      expect(tracks).toEqual([{ track_id: 'track-1', title: 'Linux' }]);
    },
  );

  it('rejects a malformed track page', async () => {
    const { client } = clientReturning({ items: [{ title: 'no id' }], next_cursor: null });
    await expect(createLearningApi('en', 'ref', client).listTracks()).rejects.toThrow();
  });
});
