import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { describe, expect, it } from 'vitest';

// Regression for api-route-wiring task 14.1. Two Checks of one Seed_Lab can share the same pass
// message (`lab-check.world-writable-entries.basic-entries` and `.edge-entries`), so the Real_Stack
// spec must count each distinct expected message by its number of occurrences in `expected`,
// not require it exactly once. The overall result count and the "passed" count stay asserted.
const SPEC = resolve(process.cwd(), 'e2e/real/seed-labs.e2e.ts');

function checkAndExpectPassBody(source: string): string {
  const start = source.indexOf('async function checkAndExpectPass');
  expect(start).toBeGreaterThanOrEqual(0);
  const end = source.indexOf('\nasync function ', start + 1);
  return source.slice(start, end === -1 ? undefined : end);
}

describe('seed-labs spec pass-message counting', () => {
  const body = checkAndExpectPassBody(readFileSync(SPEC, 'utf8'));

  it('does not require every expected message exactly once', () => {
    expect(body).not.toMatch(/toHaveCount\(1\)/);
  });

  it('counts each distinct message by its occurrences in expected', () => {
    expect(body).toMatch(/new Map|occurrences/);
    expect(body).toMatch(/hasText: message\s*\}\)\)\.toHaveCount\((count|occurrences)\)/);
  });

  it('keeps the overall result count and the passed count assertions', () => {
    expect(body).toMatch(/toHaveCount\(expected\.length,\s*\{\s*timeout: 120_000/);
    expect(body).toMatch(/results\.passed[\s\S]*toHaveCount\(\s*expected\.length/);
  });
});
