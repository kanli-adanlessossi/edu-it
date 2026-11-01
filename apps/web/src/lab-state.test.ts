import { describe, expect, it } from 'vitest';

import { readLabClientConfig } from '../scripts/lab-config.mjs';
import {
  canCheckWork,
  clockOffsetMs,
  initialLabViewState,
  labViewReducer,
  reconnectDelays,
  serverTimeMs,
  timeRemaining,
  warningActive,
  type CheckResultView,
  type LabClientConfig,
  type LabViewState,
} from './lab-state.js';

// Numbers come from the Config source, never from literals in this file.
const config: LabClientConfig = readLabClientConfig();

describe('canCheckWork', () => {
  const last = 1_000_000;
  it('is disabled while a run is running and at exactly the minimum interval', () => {
    expect(
      canCheckWork({ runStatus: 'running', lastAcceptedAtMs: null, nowMs: last }, config),
    ).toBe(false);
    expect(
      canCheckWork(
        { runStatus: 'passed', lastAcceptedAtMs: last, nowMs: last + config.gradingMinIntervalMs },
        config,
      ),
    ).toBe(false);
  });
  it('is enabled just past the interval or with no earlier request', () => {
    expect(
      canCheckWork(
        {
          runStatus: 'failed',
          lastAcceptedAtMs: last,
          nowMs: last + config.gradingMinIntervalMs + 1,
        },
        config,
      ),
    ).toBe(true);
    expect(canCheckWork({ runStatus: null, lastAcceptedAtMs: null, nowMs: last }, config)).toBe(
      true,
    );
  });
});

describe('timer', () => {
  it('uses the earlier deadline and warns strictly below the threshold', () => {
    const now = 5_000;
    const remaining = timeRemaining(
      now + 10 * config.warningThresholdMs,
      now + config.warningThresholdMs,
      now,
    );
    expect(remaining).toBe(config.warningThresholdMs);
    expect(warningActive(remaining, config)).toBe(false);
    expect(warningActive(remaining - 1, config)).toBe(true);
    expect(timeRemaining(now - 1, now - 1, now)).toBe(0);
  });
  it('is independent of client clock skew through the server offset', () => {
    const offset = clockOffsetMs(1_000_000, 400);
    expect(serverTimeMs(400, offset)).toBe(1_000_000);
  });
});

describe('reconnectDelays', () => {
  it('follows the configured backoff and stops once the window is reached', () => {
    const delays = reconnectDelays(config);
    const total = delays.reduce((a, b) => a + b, 0);
    expect(total).toBeGreaterThanOrEqual(config.reconnectWindowMs);
    expect(total - delays[delays.length - 1]!).toBeLessThan(config.reconnectWindowMs);
    config.reconnectBackoffMs.forEach((d, i) => expect(delays[i]).toBe(d));
  });
});

describe('labViewReducer', () => {
  const result: CheckResultView = {
    taskId: 't1',
    checkId: 'c1',
    outcome: 'passed',
    message: 'ok',
  };
  it('moves ready -> grading -> results', () => {
    let s: LabViewState = initialLabViewState;
    s = labViewReducer(s, { type: 'grading-requested' });
    s = labViewReducer(s, { type: 'check-result', result });
    s = labViewReducer(s, { type: 'check-result', result });
    expect(s).toEqual({ phase: 'grading', results: [result] });
    s = labViewReducer(s, { type: 'grading-finished', runStatus: 'passed' });
    expect(s.phase).toBe('results');
  });
  it('resumes the previous view after reconnecting', () => {
    let s = labViewReducer(initialLabViewState, { type: 'grading-requested' });
    s = labViewReducer(s, { type: 'connection-lost' });
    expect(s.phase).toBe('reconnecting');
    s = labViewReducer(s, { type: 'check-result', result });
    s = labViewReducer(s, { type: 'connection-restored' });
    expect(s).toEqual({ phase: 'grading', results: [result] });
  });
  it('ends in error when the session ends or reconnecting gives up', () => {
    const lost = labViewReducer(labViewReducer(initialLabViewState, { type: 'connection-lost' }), {
      type: 'reconnect-gave-up',
    });
    expect(lost).toEqual({ phase: 'error', error: { kind: 'connection-lost', detail: '' } });
    expect(labViewReducer(initialLabViewState, { type: 'session-ended' }).phase).toBe('error');
  });
});
