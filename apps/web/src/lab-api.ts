// Labs API adapter for the Lab_View, through the generated client (Req 16.3).
//
// Non-2xx responses (RFC 9457 Problem_Details) become a `LabApiError` carrying
// only the translated learner-facing `detail`. Session instants are converted to
// epoch milliseconds together with a server clock offset taken from `server_now`
// (see lab-state.ts), so the view never trusts the client clock.

import { apiBaseUrl, createApiClient } from './create-api-client.js';
import type { GeneratedClient } from './generated/api-client.js';
import { routing } from './i18n/routing.js';
import { clockOffsetMs, type CheckResultView, type RunStatus } from './lab-state.js';
import { isLocale, type Locale } from './locale.js';

/** Client durations generated from the Config source (scripts/lab-config.mjs). */
export { labClientConfig } from './generated/lab-config.js';

export type LabSessionStatus =
  'provisioning' | 'ready' | 'grading' | 'ended' | 'expired' | 'idle-expired' | 'failed';

export interface LabSessionView {
  readonly stableId: string;
  readonly labId: string;
  readonly status: LabSessionStatus;
  readonly createdAtMs: number;
  readonly ttlAtMs: number;
  readonly idleDeadlineMs: number;
  /** Earliest instant the next grading request is accepted, or null. */
  readonly gradingAvailableAtMs: number | null;
  /** `server_now - Date.now()` at response time; add to the client clock for server time. */
  readonly clockOffsetMs: number;
  readonly error: { readonly title: string; readonly detail: string } | null;
}

export interface GradingRunView {
  readonly stableId: string;
  readonly sessionId: string;
  readonly status: RunStatus;
  readonly acceptedAtMs: number;
  readonly results: readonly CheckResultView[];
}

export class LabApiError extends Error {
  readonly status: number;
  /** Seconds from the `Retry-After` header, when present. */
  readonly retryAfterSeconds: number | null;

  constructor(detail: string, status: number, retryAfterSeconds: number | null = null) {
    super(detail);
    this.name = 'LabApiError';
    this.status = status;
    this.retryAfterSeconds = retryAfterSeconds;
  }
}

type Json = Record<string, unknown>;

/** Base URL of the API, also used to derive the terminal WebSocket URL. */
export function labApiBaseUrl(): string {
  return apiBaseUrl();
}

function resolveLocale(locale: string): Locale {
  return isLocale(locale) ? locale : routing.defaultLocale;
}

function str(value: unknown): string {
  return typeof value === 'string' ? value : '';
}

function instant(value: unknown): number {
  const ms = typeof value === 'string' ? Date.parse(value) : Number.NaN;
  return Number.isFinite(ms) ? ms : 0;
}

const SESSION_STATUSES: readonly LabSessionStatus[] = [
  'provisioning',
  'ready',
  'grading',
  'ended',
  'expired',
  'idle-expired',
  'failed',
];

const RUN_STATUSES: readonly RunStatus[] = ['running', 'passed', 'failed', 'could-not-complete'];

async function readJson(response: Response): Promise<Json> {
  if (!response.ok) {
    let detail = '';
    try {
      const body = (await response.json()) as { detail?: unknown; title?: unknown };
      if (typeof body.detail === 'string') detail = body.detail;
      else if (typeof body.title === 'string') detail = body.title;
    } catch {
      // Non-JSON error body: no learner-facing detail to surface.
    }
    const retry = Number(response.headers.get('retry-after'));
    throw new LabApiError(
      detail,
      response.status,
      Number.isFinite(retry) && retry > 0 ? retry : null,
    );
  }
  return (await response.json()) as Json;
}

/** Map a `LabSessionResponse` body; `clientNowMs` is the client clock at response time. */
export function toSessionView(body: Json, clientNowMs: number): LabSessionView {
  const status = SESSION_STATUSES.find((s) => s === body.status) ?? 'failed';
  const error = body.error as Json | null | undefined;
  return {
    stableId: str(body.stable_id),
    labId: str(body.lab_id),
    status,
    createdAtMs: instant(body.created_at),
    ttlAtMs: instant(body.ttl_at),
    idleDeadlineMs: instant(body.idle_deadline),
    gradingAvailableAtMs: body.grading_available_at ? instant(body.grading_available_at) : null,
    clockOffsetMs: clockOffsetMs(instant(body.server_now), clientNowMs),
    error: error ? { title: str(error.title), detail: str(error.detail) } : null,
  };
}

/** Map one `CheckResultResponse` (learner-facing: no raw output, Req 18.3). */
export function toCheckResultView(body: Json): CheckResultView {
  const outcome =
    body.outcome === 'passed' || body.outcome === 'failed' ? body.outcome : 'could_not_complete';
  return {
    taskId: str(body.task_id),
    checkId: str(body.check_id),
    outcome,
    message: str(body.message),
  };
}

export function toRunView(body: Json): GradingRunView {
  const results = Array.isArray(body.results) ? (body.results as Json[]) : [];
  return {
    stableId: str(body.stable_id),
    sessionId: str(body.session_id),
    status: RUN_STATUSES.find((s) => s === body.status) ?? 'could-not-complete',
    acceptedAtMs: instant(body.accepted_at),
    results: results.map(toCheckResultView),
  };
}

function http(client?: GeneratedClient): Pick<GeneratedClient, 'call'> {
  return client ?? createApiClient();
}

export async function fetchLabSession(
  sessionId: string,
  localeInput: string,
  client?: GeneratedClient,
  clientNow: () => number = Date.now,
): Promise<LabSessionView> {
  const locale = resolveLocale(localeInput);
  const response = await http(client).call(
    'GET',
    `/lab-sessions/${encodeURIComponent(sessionId)}`,
    {
      credentials: 'include',
      cache: 'no-store',
      headers: { accept: 'application/json', 'accept-language': locale },
    },
  );
  const now = clientNow();
  return toSessionView(await readJson(response), now);
}

/** Request grading ("Check my work"); a repeated key returns the stored run. */
export async function requestGrading(
  sessionId: string,
  localeInput: string,
  idempotencyKey: string,
  client?: GeneratedClient,
): Promise<GradingRunView> {
  const locale = resolveLocale(localeInput);
  const response = await http(client).call(
    'POST',
    `/lab-sessions/${encodeURIComponent(sessionId)}/grading-runs`,
    {
      credentials: 'include',
      headers: {
        accept: 'application/json',
        'accept-language': locale,
        'idempotency-key': idempotencyKey,
      },
    },
  );
  return toRunView(await readJson(response));
}

export async function fetchGradingRun(
  sessionId: string,
  runId: string,
  localeInput: string,
  client?: GeneratedClient,
): Promise<GradingRunView> {
  const locale = resolveLocale(localeInput);
  const response = await http(client).call(
    'GET',
    `/lab-sessions/${encodeURIComponent(sessionId)}/grading-runs/${encodeURIComponent(runId)}`,
    {
      credentials: 'include',
      cache: 'no-store',
      headers: { accept: 'application/json', 'accept-language': locale },
    },
  );
  return toRunView(await readJson(response));
}

// --- lab, session lifecycle, hints, files, and the result stream -------------------

export interface LabTaskView {
  readonly taskId: string;
  readonly required: boolean;
  readonly text: string;
  readonly hintCount: number;
}

export interface LabDetailView {
  readonly labId: string;
  /** `code` for a Code_Exercise (Monaco), anything else is a terminal lab (xterm.js). */
  readonly kind: string;
  readonly title: string;
  readonly locale: string;
  readonly tasks: readonly LabTaskView[];
  readonly learnerFiles: readonly string[];
}

export interface HintView {
  readonly taskId: string;
  readonly level: number;
  readonly totalLevels: number;
  readonly text: string;
}

export function toLabDetail(body: Json): LabDetailView {
  const tasks = Array.isArray(body.tasks) ? (body.tasks as Json[]) : [];
  const files = Array.isArray(body.learner_files) ? body.learner_files : [];
  return {
    labId: str(body.lab_id),
    kind: str(body.kind),
    title: str(body.title),
    locale: str(body.locale),
    tasks: tasks.map((t) => ({
      taskId: str(t.task_id),
      required: t.required !== false,
      text: str(t.text),
      hintCount: typeof t.hint_count === 'number' ? t.hint_count : 0,
    })),
    learnerFiles: files.filter((f): f is string => typeof f === 'string'),
  };
}

/** Fetch a lab in `locale`; resolves to null when the lab does not exist. */
export async function fetchLab(
  labId: string,
  localeInput: string,
  cookie?: string,
  client?: GeneratedClient,
): Promise<LabDetailView | null> {
  const locale = resolveLocale(localeInput);
  // Server-side call: the Session_Cookie must be forwarded explicitly (no cookie jar),
  // otherwise the API answers 401 (api-route-wiring task 13.3).
  const response = await http(client).call(
    'GET',
    `/labs/${encodeURIComponent(labId)}?locale=${encodeURIComponent(locale)}`,
    {
      cache: 'no-store',
      headers: {
        accept: 'application/json',
        'accept-language': locale,
        ...(cookie ? { cookie } : {}),
      },
    },
  );
  if (response.status === 404) return null;
  return toLabDetail(await readJson(response));
}

const ACTIVE_STATUSES: readonly LabSessionStatus[] = ['provisioning', 'ready', 'grading'];

/** Whether the session still counts as the learner's Active_Session. */
export function isActiveStatus(status: LabSessionStatus): boolean {
  return ACTIVE_STATUSES.includes(status);
}

const SESSION_PAGE_LIMIT = 50;
const SESSION_PAGE_CAP = 40;

/** The learner's active session for `labId`, or null when there is none. */
export function findActiveSession(
  labId: string,
  localeInput: string,
  client?: GeneratedClient,
  clientNow: () => number = Date.now,
): Promise<LabSessionView | null> {
  return findActiveSessionWhere((view) => view.labId === labId, localeInput, client, clientNow);
}

/** The learner's Active_Session whatever the lab (at most one exists, Req 7.1), or null. */
export function findAnyActiveSession(
  localeInput: string,
  client?: GeneratedClient,
  clientNow: () => number = Date.now,
): Promise<LabSessionView | null> {
  return findActiveSessionWhere(() => true, localeInput, client, clientNow);
}

/** End a session; ending an already ended session is a no-op (Req 30.1 offers this on a 409). */
export async function endLabSession(
  sessionId: string,
  localeInput: string,
  client?: GeneratedClient,
  clientNow: () => number = Date.now,
): Promise<LabSessionView> {
  const locale = resolveLocale(localeInput);
  const response = await http(client).call(
    'DELETE',
    `/lab-sessions/${encodeURIComponent(sessionId)}`,
    {
      credentials: 'include',
      headers: { accept: 'application/json', 'accept-language': locale },
    },
  );
  const now = clientNow();
  return toSessionView(await readJson(response), now);
}

async function findActiveSessionWhere(
  matches: (view: LabSessionView) => boolean,
  localeInput: string,
  client: GeneratedClient | undefined,
  clientNow: () => number,
): Promise<LabSessionView | null> {
  const locale = resolveLocale(localeInput);
  let cursor: string | null = null;
  let found: LabSessionView | null = null;
  for (let page = 0; page < SESSION_PAGE_CAP; page += 1) {
    const query = `limit=${SESSION_PAGE_LIMIT}${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ''}`;
    const response = await http(client).call('GET', `/lab-sessions?${query}`, {
      credentials: 'include',
      cache: 'no-store',
      headers: { accept: 'application/json', 'accept-language': locale },
    });
    const now = clientNow();
    const body = await readJson(response);
    const items = Array.isArray(body.items) ? (body.items as Json[]) : [];
    for (const item of items) {
      const view = toSessionView(item, now);
      if (matches(view) && isActiveStatus(view.status)) found = view;
    }
    cursor = typeof body.next_cursor === 'string' ? body.next_cursor : null;
    if (cursor === null) break;
  }
  return found;
}

/** Start a session of `labId`; a repeated key returns the stored session. */
export async function startLabSession(
  labId: string,
  localeInput: string,
  idempotencyKey: string,
  client?: GeneratedClient,
  clientNow: () => number = Date.now,
): Promise<LabSessionView> {
  const locale = resolveLocale(localeInput);
  const response = await http(client).call('POST', '/lab-sessions', {
    credentials: 'include',
    headers: {
      accept: 'application/json',
      'accept-language': locale,
      'content-type': 'application/json',
      'idempotency-key': idempotencyKey,
    },
    body: JSON.stringify({ lab_id: labId }),
  });
  const now = clientNow();
  return toSessionView(await readJson(response), now);
}

/** End the session and start a fresh one for the same lab; completions are untouched. */
export async function resetLabSession(
  sessionId: string,
  localeInput: string,
  idempotencyKey: string,
  client?: GeneratedClient,
  clientNow: () => number = Date.now,
): Promise<LabSessionView> {
  const locale = resolveLocale(localeInput);
  const response = await http(client).call(
    'POST',
    `/lab-sessions/${encodeURIComponent(sessionId)}/reset`,
    {
      credentials: 'include',
      headers: {
        accept: 'application/json',
        'accept-language': locale,
        'idempotency-key': idempotencyKey,
      },
    },
  );
  const now = clientNow();
  return toSessionView(await readJson(response), now);
}

/** Reveal one hint level; the API refuses a level before the previous one is revealed. */
export async function revealHint(
  sessionId: string,
  taskId: string,
  level: number,
  localeInput: string,
  client?: GeneratedClient,
): Promise<HintView> {
  const locale = resolveLocale(localeInput);
  const response = await http(client).call(
    'POST',
    `/lab-sessions/${encodeURIComponent(sessionId)}/hints`,
    {
      credentials: 'include',
      headers: {
        accept: 'application/json',
        'accept-language': locale,
        'content-type': 'application/json',
      },
      body: JSON.stringify({ task_id: taskId, level }),
    },
  );
  const body = await readJson(response);
  return {
    taskId: str(body.task_id),
    level: typeof body.level === 'number' ? body.level : level,
    totalLevels: typeof body.total_levels === 'number' ? body.total_levels : level,
    text: str(body.text),
  };
}

/** Save learner files into the sandbox workspace. */
export async function saveLabFiles(
  sessionId: string,
  files: Readonly<Record<string, string>>,
  localeInput: string,
  client?: GeneratedClient,
): Promise<void> {
  const locale = resolveLocale(localeInput);
  const response = await http(client).call(
    'PUT',
    `/lab-sessions/${encodeURIComponent(sessionId)}/files`,
    {
      credentials: 'include',
      headers: {
        accept: 'application/json',
        'accept-language': locale,
        'content-type': 'application/json',
      },
      body: JSON.stringify({ files }),
    },
  );
  await readJson(response);
}

// --- server-sent events -------------------------------------------------------------

export type RunStreamEvent =
  | { readonly type: 'result'; readonly id: number; readonly result: CheckResultView }
  | { readonly type: 'run'; readonly id: number; readonly status: RunStatus };

/**
 * Parse complete SSE blocks out of `buffer`. Returns the events found and the
 * unparsed remainder (a block still waiting for its blank-line terminator).
 */
export function parseSseBuffer(buffer: string): { events: RunStreamEvent[]; rest: string } {
  const events: RunStreamEvent[] = [];
  const blocks = buffer.replace(/\r\n/g, '\n').split('\n\n');
  const rest = blocks.pop() ?? '';
  for (const block of blocks) {
    let name = '';
    let id = 0;
    const data: string[] = [];
    for (const line of block.split('\n')) {
      if (line.startsWith('event:')) name = line.slice(6).trim();
      else if (line.startsWith('id:')) id = Number(line.slice(3).trim()) || 0;
      else if (line.startsWith('data:')) data.push(line.slice(5).trimStart());
    }
    if (data.length === 0) continue;
    let payload: Json;
    try {
      payload = JSON.parse(data.join('\n')) as Json;
    } catch {
      continue;
    }
    if (name === 'result') {
      events.push({ type: 'result', id, result: toCheckResultView(payload) });
    } else if (name === 'run') {
      const status = RUN_STATUSES.find((s) => s === payload.status) ?? 'could-not-complete';
      events.push({ type: 'run', id, status });
    }
  }
  return { events, rest };
}

/**
 * Open the result stream of a run and call `onEvent` for each event as it
 * arrives (incremental display, Req 11.4). Resolves when the stream ends and
 * rejects on a transport failure or non-2xx response; `lastEventId` resumes it.
 */
export async function streamRunEvents(
  sessionId: string,
  runId: string,
  localeInput: string,
  lastEventId: number,
  onEvent: (event: RunStreamEvent) => void,
  signal: AbortSignal,
  client?: GeneratedClient,
): Promise<void> {
  const locale = resolveLocale(localeInput);
  const headers: Record<string, string> = {
    accept: 'text/event-stream',
    'accept-language': locale,
  };
  if (lastEventId > 0) headers['last-event-id'] = String(lastEventId);
  const response = await http(client).call(
    'GET',
    `/lab-sessions/${encodeURIComponent(sessionId)}/grading-runs/${encodeURIComponent(runId)}/events`,
    { credentials: 'include', cache: 'no-store', headers, signal },
  );
  if (!response.ok || response.body === null) {
    await readJson(response);
    throw new LabApiError('', response.status);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parsed = parseSseBuffer(buffer);
    buffer = parsed.rest;
    for (const event of parsed.events) onEvent(event);
  }
}
