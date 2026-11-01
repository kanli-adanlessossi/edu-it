import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { describe, expect, it } from 'vitest';

// Regression for api-route-wiring task 13.6. The Enroll button submits a server action that
// enrolls, then redirects to the track page. A Real_Stack spec that calls `page.goto` straight
// after the click cancels that navigation and races the enrollment: the track read can reach the
// API first and answer 409 NotEnrolledError, which the track page renders as a 404 (no
// `li[data-lock-state]`). The spec must wait for the redirect to the track page after the click.
const SPEC = resolve(process.cwd(), 'e2e/real/learner-journey.e2e.ts');

describe('learner-journey spec', () => {
  it('waits for the enroll redirect to the track page before navigating on', () => {
    const lines = readFileSync(SPEC, 'utf8').split(/\r?\n/);
    const click = lines.findIndex((l) => /name:\s*'Enroll'/.test(l) && /\.click\(\)/.test(l));
    expect(click, 'the spec clicks the Enroll button').toBeGreaterThanOrEqual(0);
    const next = lines.slice(click + 1).find((l) => /page\.(goto|waitForURL)\(/.test(l));
    expect(next, 'a navigation step follows the click').toBeDefined();
    expect(next).toMatch(/page\.waitForURL\(.*tracks/);
  });
});
