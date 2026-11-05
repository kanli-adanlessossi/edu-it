// Terminal reconnect schedules (kubernetes-deployment Req 14.5, 14.6, 14.8, 20.1, 20.2).
//
// TypeScript port of `reconnect_delays` (packages/domain/.../labs/k8s_rules.py). The values
// mirror the TERMINAL_*_RECONNECT_* constants of `domain/config.py`; both sides are tested
// against `tests/shared/reconnect-vectors.json`, so a drift on either side fails a test.
//
// Pure: no DOM, no clock, no timers.

/** `drain`: after a reconnect message. `restart`: after an abrupt close. */
export type ReconnectKind = 'drain' | 'restart';

/** `TERMINAL_DRAIN_RECONNECT_DELAYS`, in milliseconds. */
const DRAIN_DELAYS_MS: readonly number[] = [
  1_000, 2_000, 4_000, 8_000, 16_000, 30_000, 30_000, 30_000, 30_000, 30_000,
];
/** `TERMINAL_DRAIN_RECONNECT_MAX_ATTEMPTS`. */
const DRAIN_MAX_ATTEMPTS = 10;
/** `TERMINAL_RESTART_RECONNECT_DELAYS`; the last delay repeats. */
const RESTART_DELAYS_MS: readonly number[] = [1_000, 2_000, 4_000, 5_000];
/** `TERMINAL_RESTART_RECONNECT_TOTAL`. */
const RESTART_TOTAL_MS = 60_000;

/** Delays (ms) between reconnect attempts; its length is the number of attempts. */
export function reconnectSchedule(kind: ReconnectKind): number[] {
  if (kind === 'drain') return DRAIN_DELAYS_MS.slice(0, DRAIN_MAX_ATTEMPTS);
  if (kind !== 'restart') throw new Error(`unknown reconnect schedule: ${String(kind)}`);
  const delays: number[] = [];
  let spent = 0;
  for (let index = 0; ; index += 1) {
    const delay = RESTART_DELAYS_MS[Math.min(index, RESTART_DELAYS_MS.length - 1)] as number;
    if (spent + delay > RESTART_TOTAL_MS) break;
    delays.push(delay);
    spent += delay;
  }
  return delays;
}

/** What the pane does after the socket closes. */
export type CloseOutcome = 'retry' | 'reauth' | 'stop';

/**
 * Close codes 4401 (no valid session) and 4404 (unknown, expired or foreign lab session)
 * are the gateway's ownership/expiry refusals (Req 14.8): never retried, the learner must
 * sign in again. Other refusals and normal end stop quietly; everything else is retried.
 */
export function closeOutcome(code: number): CloseOutcome {
  if (code === 4401 || code === 4404) return 'reauth';
  if (code === 1000 || code === 4403 || code === 4409) return 'stop';
  return 'retry';
}
