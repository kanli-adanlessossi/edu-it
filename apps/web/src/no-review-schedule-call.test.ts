import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, resolve, sep } from 'node:path';

import { describe, expect, it } from 'vitest';

import type { GeneratedClient } from './generated/api-client.js';

// Regression for api-route-wiring task 13.8. GET /v1/reviews/schedule is out of scope (design
// decision 7), so the API answers 404. The web source must never request it; only the mock API
// fixtures under e2e/ may name the path. The generated client is checked too.
const WEB_ROOT = process.cwd();
const SKIP_DIRS = new Set(['node_modules', '.next', 'e2e', 'coverage', 'test-results']);
const SELF = resolve(WEB_ROOT, 'src/no-review-schedule-call.test.ts');

function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return SKIP_DIRS.has(name) ? [] : sources(path);
    return /\.(ts|tsx|js|mjs)$/.test(name) ? [path] : [];
  });
}

describe('web source', () => {
  it('never calls /reviews/schedule outside the mock API fixtures', () => {
    const offenders = sources(resolve(WEB_ROOT, 'src'))
      .filter((file) => file !== SELF)
      .filter((file) => /reviews\/schedule/.test(readFileSync(file, 'utf8')))
      .map((file) => file.slice(WEB_ROOT.length + 1).split(sep).join('/'));
    // learning-api.ts may name the path in the comment explaining why it is not called.
    expect(offenders.filter((f) => f !== 'src/learning-api.ts')).toEqual([]);
  });

  it('getReviewSchedule makes no HTTP request', async () => {
    const { createLearningApi } = await import('./learning-api');
    const calls: string[] = [];
    const client = {
      call: (_method: string, path: string) => {
        calls.push(path);
        return Promise.reject(new Error('unexpected request'));
      },
    } as unknown as GeneratedClient;
    const api = createLearningApi('en', 'ref', client);
    await expect(api.getReviewSchedule()).resolves.toEqual([]);
    expect(calls).toEqual([]);
  });
});

