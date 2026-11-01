import fc from 'fast-check';
import { describe, expect, it } from 'vitest';

import { readLabClientConfig } from '../scripts/lab-config.mjs';
import {
  canCheckWork,
  initialLabViewState,
  labViewReducer,
  reconnectDelayMs,
  reconnectDelays,
  timeRemaining,
  warningActive,
  type CheckResultView,
  type LabClientConfig,
  type LabViewEvent,
  type LabViewState,
  type RunStatus,
} from './lab-state.js';

// Numbers come from the Config source (packages/domain config.py), never literals here.
const config: LabClientConfig = readLabClientConfig();
const NUM_RUNS = 100;

describe('Feature: hands-on-labs, Property 18: Reconnect backoff schedule', () => {
  // **Validates: Requirements 13.4**
  it('each delay is the configured step (last one repeated) for any attempt index', () => {
    const steps = config.reconnectBackoffMs;
    fc.assert(
      fc.property(fc.nat({ max: 10_000 }), (attempt) => {
        const expected = steps[Math.min(attempt, steps.length - 1)];
        expect(reconnectDelayMs(attempt, config)).toBe(expected);
      }),
      { numRuns: NUM_RUNS },
    );
  });

  it('the schedule stops once the cumulative total reaches the window', () => {
    // Vary the window and backoff around the configured values.
    fc.assert(
      fc.property(
        fc.integer({ min: 1, max: 5 * config.reconnectWindowMs }),
        fc.array(fc.integer({ min: 1, max: config.reconnectWindowMs }), {
          minLength: 1,
          maxLength: 8,
        }),
        (windowMs, backoff) => {
          const cfg: LabClientConfig = {
            ...config,
            reconnectWindowMs: windowMs,
            reconnectBackoffMs: backoff,
          };
          const delays = reconnectDelays(cfg);
          const total = delays.reduce((a, b) => a + b, 0);
          expect(total).toBeGreaterThanOrEqual(windowMs);
          expect(total - delays[delays.length - 1]!).toBeLessThan(windowMs);
          delays.forEach((d, i) => expect(d).toBe(reconnectDelayMs(i, cfg)));
        },
      ),
      { numRuns: NUM_RUNS },
    );
  });

  it('the configured schedule itself reaches the configured window', () => {
    const delays = reconnectDelays(config);
    expect(delays.reduce((a, b) => a + b, 0)).toBeGreaterThanOrEqual(config.reconnectWindowMs);
  });
});

const runStatus = fc.constantFrom<RunStatus | null>(
  null,
  'running',
  'passed',
  'failed',
  'could-not-complete',
);
const instant = fc.integer({ min: 0, max: 10_000_000_000 });

describe('Feature: hands-on-labs, Property 19: Lab_View derived state', () => {
  // **Validates: Requirements 15.5, 15.6, 15.8**
  it('Check my work is enabled iff no run is running and more than the minimum interval has passed', () => {
    fc.assert(
      fc.property(
        runStatus,
        fc.option(instant, { nil: null }),
        instant,
        (status, last, now) => {
          const expected =
            status !== 'running' && (last === null || now - last > config.gradingMinIntervalMs);
          expect(canCheckWork({ runStatus: status, lastAcceptedAtMs: last, nowMs: now }, config)).toBe(
            expected,
          );
        },
      ),
      { numRuns: NUM_RUNS },
    );
  });

  it('the boundary is strict around the minimum interval', () => {
    fc.assert(
      fc.property(instant, (last) => {
        const at = (delta: number) =>
          canCheckWork(
            {
              runStatus: null,
              lastAcceptedAtMs: last,
              nowMs: last + config.gradingMinIntervalMs + delta,
            },
            config,
          );
        expect(at(0)).toBe(false);
        expect(at(1)).toBe(true);
      }),
      { numRuns: NUM_RUNS },
    );
  });

  it('timeRemaining is the smaller deadline minus now, floored at 0; warning iff below threshold', () => {
    fc.assert(
      fc.property(instant, instant, instant, (ttl, idle, now) => {
        const remaining = timeRemaining(ttl, idle, now);
        expect(remaining).toBe(Math.max(0, Math.min(ttl - now, idle - now)));
        expect(remaining).toBeGreaterThanOrEqual(0);
        expect(warningActive(remaining, config)).toBe(
          Math.min(ttl - now, idle - now) < config.warningThresholdMs,
        );
      }),
      { numRuns: NUM_RUNS },
    );
  });

  const checkResult: fc.Arbitrary<CheckResultView> = fc.record({
    taskId: fc.constantFrom('t1', 't2'),
    checkId: fc.constantFrom('c1', 'c2'),
    outcome: fc.constantFrom('passed', 'failed', 'could_not_complete'),
    message: fc.string({ maxLength: 10 }),
  });
  const event: fc.Arbitrary<LabViewEvent> = fc.oneof(
    fc.constant<LabViewEvent>({ type: 'grading-requested' }),
    checkResult.map((result): LabViewEvent => ({ type: 'check-result', result })),
    fc
      .constantFrom<'passed' | 'failed' | 'could-not-complete'>(
        'passed',
        'failed',
        'could-not-complete',
      )
      .map((s): LabViewEvent => ({ type: 'grading-finished', runStatus: s })),
    fc.string({ maxLength: 10 }).map((detail): LabViewEvent => ({ type: 'grading-failed', detail })),
    fc.constant<LabViewEvent>({ type: 'connection-lost' }),
    fc.constant<LabViewEvent>({ type: 'reconnect-attempted' }),
    fc.constant<LabViewEvent>({ type: 'connection-restored' }),
    fc.constant<LabViewEvent>({ type: 'reconnect-gave-up' }),
    fc.constant<LabViewEvent>({ type: 'session-ended' }),
    fc.constant<LabViewEvent>({ type: 'dismiss-results' }),
  );

  it('the reducer always yields a valid phase and session-ended is terminal', () => {
    const phases = ['ready', 'grading', 'results', 'error', 'reconnecting'];
    fc.assert(
      fc.property(fc.array(event, { maxLength: 30 }), (events) => {
        let s: LabViewState = initialLabViewState;
        for (const e of events) {
          s = labViewReducer(s, e);
          expect(phases).toContain(s.phase);
        }
        const ended = labViewReducer(s, { type: 'session-ended' });
        expect(ended.phase).toBe('error');
        for (const e of events) {
          const next = labViewReducer(ended, e);
          if (e.type !== 'session-ended') expect(next).toEqual(ended);
        }
      }),
      { numRuns: NUM_RUNS },
    );
  });

  it('a lost connection resumes the exact prior view when restored', () => {
    fc.assert(
      fc.property(fc.array(event, { maxLength: 20 }), (events) => {
        let s: LabViewState = initialLabViewState;
        for (const e of events) s = labViewReducer(s, e);
        if (s.phase === 'error' || s.phase === 'reconnecting') return;
        const lost = labViewReducer(s, { type: 'connection-lost' });
        expect(lost.phase).toBe('reconnecting');
        expect(labViewReducer(lost, { type: 'connection-restored' })).toEqual(s);
      }),
      { numRuns: NUM_RUNS },
    );
  });
});
