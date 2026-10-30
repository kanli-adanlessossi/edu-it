// Pure client rules for the Lab_View (hands-on-labs Req 13.4, 15.5, 15.6, 15.8).
//
// No clock reads and no business numbers live here: every duration comes in as a
// `LabClientConfig` (generated from the Config source by scripts/lab-config.mjs)
// and every instant is passed in. Server time is derived from the API's
// `server_now` through an offset, so client clock skew never affects the
// "Check my work" 10 s rule or the timer.

/** Durations in milliseconds, taken from the Config source. */
export interface LabClientConfig {
  readonly gradingMinIntervalMs: number;
  readonly warningThresholdMs: number;
  readonly resultPollIntervalMs: number;
  /** Provisioning older than this shows the "taking longer than usual" message. */
  readonly provisionSlowMs: number;
  readonly reconnectWindowMs: number;
  readonly reconnectBackoffMs: readonly number[];
}

// --- server clock offset --------------------------------------------------------

/** Offset to add to the client clock to get server time: `serverNow - clientNow`. */
export function clockOffsetMs(serverNowMs: number, clientNowMs: number): number {
  return serverNowMs - clientNowMs;
}

/** Estimated server time (ms since epoch) at client time `clientNowMs`. */
export function serverTimeMs(clientNowMs: number, offsetMs: number): number {
  return clientNowMs + offsetMs;
}

// --- Check my work (Req 15.5) -----------------------------------------------------

export type RunStatus = 'running' | 'passed' | 'failed' | 'could-not-complete';

export interface CheckWorkInput {
  /** Status of the current grading run, or null when there is none. */
  readonly runStatus: RunStatus | null;
  /** Server instant of the last accepted grading request, or null if none. */
  readonly lastAcceptedAtMs: number | null;
  /** Current server time (see {@link serverTimeMs}). */
  readonly nowMs: number;
}

/** Enabled iff no run is running and strictly more than the minimum interval has passed. */
export function canCheckWork(input: CheckWorkInput, config: LabClientConfig): boolean {
  if (input.runStatus === 'running') return false;
  if (input.lastAcceptedAtMs === null) return true;
  return input.nowMs - input.lastAcceptedAtMs > config.gradingMinIntervalMs;
}

// --- timer (Req 15.6, 15.8) ---------------------------------------------------------

/** Time left before the earlier of the TTL and the idle deadline, never negative. */
export function timeRemaining(ttlAtMs: number, idleDeadlineMs: number, nowMs: number): number {
  return Math.max(0, Math.min(ttlAtMs, idleDeadlineMs) - nowMs);
}

/** The warning is active iff the remaining time is below the warning threshold. */
export function warningActive(remainingMs: number, config: LabClientConfig): boolean {
  return remainingMs < config.warningThresholdMs;
}

// --- reconnect (Req 13.4) -----------------------------------------------------------

/**
 * Delay before reconnect attempt `attempt` (0-based): the configured backoff
 * entries in order, then the last entry repeated.
 */
export function reconnectDelayMs(attempt: number, config: LabClientConfig): number {
  const steps = config.reconnectBackoffMs;
  const index = Math.min(Math.max(0, Math.trunc(attempt)), steps.length - 1);
  return steps[index] as number;
}

/**
 * The full retry schedule: delays continue until their cumulative total reaches
 * the reconnect window; the delay that reaches it is the last one.
 */
export function reconnectDelays(config: LabClientConfig): number[] {
  if (config.reconnectBackoffMs.length === 0 || config.reconnectWindowMs <= 0) return [];
  const delays: number[] = [];
  let total = 0;
  while (total < config.reconnectWindowMs) {
    const delay = reconnectDelayMs(delays.length, config);
    delays.push(delay);
    total += delay;
  }
  return delays;
}

// --- view-state reducer -------------------------------------------------------------

export type CheckOutcome = 'passed' | 'failed' | 'could_not_complete';

export interface CheckResultView {
  readonly taskId: string;
  readonly checkId: string;
  readonly outcome: CheckOutcome;
  readonly message: string;
}

export type LabErrorKind =
  'grading-failed' | 'session-ended' | 'connection-lost' | 'request-failed';

export interface LabViewError {
  readonly kind: LabErrorKind;
  /** Translated, learner-facing detail from the API, when there is one. */
  readonly detail: string;
}

export type SettledLabViewState =
  | { readonly phase: 'ready' }
  | { readonly phase: 'grading'; readonly results: readonly CheckResultView[] }
  | {
      readonly phase: 'results';
      readonly results: readonly CheckResultView[];
      readonly runStatus: Exclude<RunStatus, 'running'>;
    };

export type LabViewState =
  | SettledLabViewState
  | { readonly phase: 'error'; readonly error: LabViewError }
  | {
      readonly phase: 'reconnecting';
      /** 0-based index of the next reconnect attempt. */
      readonly attempt: number;
      /** What the view shows again once the connection is back. */
      readonly resume: SettledLabViewState;
    };

export type LabViewEvent =
  | { readonly type: 'grading-requested' }
  | { readonly type: 'check-result'; readonly result: CheckResultView }
  | { readonly type: 'grading-finished'; readonly runStatus: Exclude<RunStatus, 'running'> }
  | { readonly type: 'grading-failed'; readonly detail: string }
  | { readonly type: 'connection-lost' }
  | { readonly type: 'reconnect-attempted' }
  | { readonly type: 'connection-restored' }
  | { readonly type: 'reconnect-gave-up' }
  | { readonly type: 'session-ended'; readonly detail?: string }
  | { readonly type: 'dismiss-results' };

export const initialLabViewState: LabViewState = { phase: 'ready' };

function settle(state: SettledLabViewState, event: LabViewEvent): SettledLabViewState {
  switch (event.type) {
    case 'grading-requested':
      return state.phase === 'grading' ? state : { phase: 'grading', results: [] };
    case 'check-result':
      if (state.phase !== 'grading') return state;
      return { phase: 'grading', results: upsert(state.results, event.result) };
    case 'grading-finished':
      if (state.phase !== 'grading') return state;
      return { phase: 'results', results: state.results, runStatus: event.runStatus };
    case 'dismiss-results':
      return state.phase === 'results' ? { phase: 'ready' } : state;
    default:
      return state;
  }
}

/** A repeated delivery of the same Check (same task and check) replaces the earlier one. */
function upsert(
  results: readonly CheckResultView[],
  next: CheckResultView,
): readonly CheckResultView[] {
  const index = results.findIndex((r) => r.taskId === next.taskId && r.checkId === next.checkId);
  if (index === -1) return [...results, next];
  return results.map((r, i) => (i === index ? next : r));
}

/** Reducer for the Lab_View: ready, grading, results, error, reconnecting. */
export function labViewReducer(state: LabViewState, event: LabViewEvent): LabViewState {
  if (event.type === 'session-ended') {
    return { phase: 'error', error: { kind: 'session-ended', detail: event.detail ?? '' } };
  }
  if (state.phase === 'error') {
    // The session is over or the request failed; only a fresh grading request recovers
    // from a request failure. Session end and lost connections are terminal.
    if (state.error.kind === 'request-failed' || state.error.kind === 'grading-failed') {
      if (event.type === 'grading-requested') return { phase: 'grading', results: [] };
      if (event.type === 'dismiss-results') return { phase: 'ready' };
    }
    return state;
  }
  if (event.type === 'grading-failed') {
    return { phase: 'error', error: { kind: 'grading-failed', detail: event.detail } };
  }
  if (state.phase === 'reconnecting') {
    switch (event.type) {
      case 'reconnect-attempted':
        return { ...state, attempt: state.attempt + 1 };
      case 'connection-restored':
        return state.resume;
      case 'reconnect-gave-up':
        return { phase: 'error', error: { kind: 'connection-lost', detail: '' } };
      case 'connection-lost':
        return state;
      default:
        // Results keep arriving over a separate channel; fold them into what we resume.
        return { ...state, resume: settle(state.resume, event) };
    }
  }
  switch (event.type) {
    case 'connection-lost':
      return { phase: 'reconnecting', attempt: 0, resume: state };
    case 'reconnect-attempted':
    case 'connection-restored':
    case 'reconnect-gave-up':
      return state;
    default:
      return settle(state, event);
  }
}
