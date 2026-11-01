import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { describe, expect, it } from 'vitest';

// Regression for api-route-wiring task 13.10. `terminal-connection` is the live region for the
// reconnect indication (hands-on-labs Req 15.10, 16.4): it is empty while the terminal is
// connected and has text only while it is disconnected. A Real_Stack spec that waits for it to
// become non-empty therefore fails exactly when everything works (session `ready`, WebSocket
// open, prompt received). The spec must wait for evidence of a live shell instead: the prompt in
// the terminal, with no reconnect indication showing.
const SPEC = resolve(process.cwd(), 'e2e/real/learner-journey.e2e.ts');

describe('learner-journey spec terminal wait', () => {
  const source = readFileSync(SPEC, 'utf8');

  it('does not wait for the connection live region to gain text', () => {
    expect(source).not.toMatch(/terminal-connection'\)\)\.not\.toHaveText\(/);
  });

  it('waits for the shell prompt in the terminal and for no reconnect indication', () => {
    expect(source).toMatch(/terminal-container'\)\)\.toContainText\('\/workspace\$'/);
    expect(source).toMatch(/terminal-connection'\)\)\.toHaveText\(''\)/);
  });
});
