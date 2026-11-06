import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import fc from 'fast-check';
import { describe, expect, it } from 'vitest';

import { closeOutcome, reconnectSchedule } from './reconnect-schedule.js';
import { shouldReconnect } from './terminal-logic.js';

interface Vectors {
  drain: {
    delays_seconds: number[];
    max_attempts: number;
    max_delay_seconds: number;
    first_within_seconds: number;
  };
  restart: { delays_seconds: number[]; max_delay_seconds: number; total_limit_seconds: number };
}

const vectors = JSON.parse(
  readFileSync(resolve(process.cwd(), '../../tests/shared/reconnect-vectors.json'), 'utf8'),
) as Vectors;

const seconds = (ms: number[]) => ms.map((d) => d / 1000);

// Validates: Requirements 14.5, 20.1
describe('reconnectSchedule against the shared vectors', () => {
  it.each(['drain', 'restart'] as const)('%s matches the vector delays', (kind) => {
    expect(seconds(reconnectSchedule(kind))).toEqual(vectors[kind].delays_seconds);
  });

  it('drain: at most 10 attempts, none above 30 s, first within 2 s, non-decreasing', () => {
    const delays = seconds(reconnectSchedule('drain'));
    expect(delays.length).toBeLessThanOrEqual(vectors.drain.max_attempts);
    expect(Math.max(...delays)).toBeLessThanOrEqual(vectors.drain.max_delay_seconds);
    expect(delays[0]).toBeLessThanOrEqual(vectors.drain.first_within_seconds);
    fc.assert(
      fc.property(fc.integer({ min: 1, max: delays.length - 1 }), (i) => {
        expect(delays[i]).toBeGreaterThanOrEqual(delays[i - 1] as number);
      }),
    );
  });

  it('restart: gaps of at most 5 s that total at most 60 s', () => {
    const delays = seconds(reconnectSchedule('restart'));
    expect(Math.max(...delays)).toBeLessThanOrEqual(vectors.restart.max_delay_seconds);
    expect(delays.reduce((a, b) => a + b, 0)).toBeLessThanOrEqual(
      vectors.restart.total_limit_seconds,
    );
  });

  it('rejects an unknown schedule', () => {
    expect(() => reconnectSchedule('other' as never)).toThrow();
  });
});

// Validates: Requirement 14.8
describe('closeOutcome', () => {
  it('asks for re-authentication on ownership and session refusals', () => {
    expect(closeOutcome(4401)).toBe('reauth');
    expect(closeOutcome(4404)).toBe('reauth');
  });

  it('stops quietly on normal end, origin refusal and replacement', () => {
    for (const code of [1000, 4403, 4409]) expect(closeOutcome(code)).toBe('stop');
  });

  it('retries drops, drain closes and heartbeat closes', () => {
    for (const code of [1001, 1006, 1012, 4408]) expect(closeOutcome(code)).toBe('retry');
  });

  it('agrees with shouldReconnect for every code', () => {
    fc.assert(
      fc.property(fc.integer({ min: 1000, max: 4999 }), (code) => {
        const retry = closeOutcome(code) === 'retry';
        expect(retry).toBe(shouldReconnect(code) && code !== 4409);
      }),
    );
  });
});

// Claims: 14.6, 20.1, 20.2
describe('schedule exhaustion', () => {
  it('drain ends after 10 attempts, the first within 2 s, delays capped at 30 s', () => {
    const delays = reconnectSchedule('drain');
    expect(delays).toHaveLength(10);
    expect(delays[0]).toBeLessThanOrEqual(2_000);
    expect(Math.max(...delays)).toBeLessThanOrEqual(30_000);
  });

  it('restart repeats the last gap and stops before exceeding 60 s in total', () => {
    const delays = reconnectSchedule('restart');
    const total = delays.reduce((a, b) => a + b, 0);
    expect(delays.slice(3).every((d) => d === 5_000)).toBe(true);
    expect(total).toBeLessThanOrEqual(60_000);
    // One more 5 s gap would pass the limit, so the schedule is exhausted.
    expect(total + 5_000).toBeGreaterThan(60_000);
  });

  it('returns a fresh array so callers cannot corrupt the schedule', () => {
    const first = reconnectSchedule('restart');
    first.length = 0;
    expect(reconnectSchedule('restart').length).toBeGreaterThan(0);
  });
});

// Claims: 14.8, 20.4
describe('ownership failure is never retried', () => {
  it('maps every refusal code to reauth or stop, never retry', () => {
    for (const code of [4401, 4403, 4404, 4409]) expect(closeOutcome(code)).not.toBe('retry');
  });
});
