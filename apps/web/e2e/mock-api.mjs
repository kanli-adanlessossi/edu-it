// Minimal in-process stand-in for the EDU-IT API, used ONLY by the Playwright
// accessibility run (task 24.2). It serves deterministic Seed_Content-shaped
// fixtures for the routes the learner pages read, so the pages render real
// content without a database. Run: `node e2e/mock-api.mjs [port]`.
/* global process, URL, console */
import { createServer } from 'node:http';
import { setTimeout } from 'node:timers';

const PORT = Number(process.argv[2] ?? process.env.MOCK_API_PORT ?? 4010);

const options = (base) => [
  { option_id: base + 1, text: 'Option A' },
  { option_id: base + 2, text: 'Option B' },
  { option_id: base + 3, text: 'Option C' },
];

const fixtures = {
  enrollments: {
    items: [{ stable_id: 'enr-1', track_id: 'track-1', enrolled_at: '2025-01-01T00:00:00Z' }],
    next_cursor: null,
  },
  progress: {
    enrollment_id: 'enr-1',
    track_id: 'track-1',
    modules: [
      {
        module_id: 'module-1',
        ordinal: 1,
        state: 'unlocked',
        tested_out: false,
        completions: [{ lesson_id: 'lesson-1', completed_at: '2025-01-02T00:00:00Z' }],
      },
      { module_id: 'module-2', ordinal: 2, state: 'locked', tested_out: false, completions: [] },
    ],
    mastery: [{ concept_id: 'concept-1', score: 60, state: 'learning' }],
  },
  schedule: {
    items: [
      { concept_id: 'concept-due', due_at: new Date(Date.now() - 3600_000).toISOString() },
      { concept_id: 'concept-overdue', due_at: new Date(Date.now() - 5 * 86400_000).toISOString() },
    ],
  },
  lesson: {
    stable_id: 'lesson-1',
    module_id: 'module-1',
    quiz_id: 'quiz-1',
    title: 'Lesson one',
    body: '## Overview\n\nA short paragraph of lesson text.\n\n- First point\n- Second point\n',
    locale: 'en',
    content_version: 'v1',
  },
  quiz: {
    stable_id: 'quiz-1',
    title: 'Quiz one',
    content_version: 'v1',
    questions: [
      {
        stable_id: 'q-1',
        prompt: 'Question one?',
        options: options(10),
        hints: ['Hint 1', 'Hint 2'],
      },
      { stable_id: 'q-2', prompt: 'Question two?', options: options(20), hints: ['Hint 1'] },
    ],
  },
  review: {
    questions: [{ stable_id: 'rq-1', prompt: 'Review question?', options: options(30) }],
  },
  project: {
    stable_id: 'project-1',
    title: 'Project one',
    description: 'Build something small.',
    rubric: [
      { stable_id: 'crit-1', title: 'Correctness', max_points: 4 },
      { stable_id: 'crit-2', title: 'Clarity', max_points: 4 },
    ],
  },
};

// ---- Lab_View fixtures (task 16.4) ----------------------------------------
// The lab id selects the state: `lab-<state>` with <state> in LAB_STATES. The
// session and run ids carry the same suffix, so every endpoint stays stateless
// and deterministic. Page-level accessibility tests only.
const LAB_STATES = ['ready', 'grading', 'results', 'error', 'reconnecting'];
const stateOf = (id) => {
  const s = String(id).replace(/^(lab|sess|run)-/, '');
  return LAB_STATES.includes(s) ? s : 'ready';
};
const SESSION_STATUS = {
  ready: 'ready',
  grading: 'grading',
  results: 'ready',
  error: 'failed',
  reconnecting: 'ready',
};
const RESULTS = [
  {
    task_id: 'task-1',
    check_id: 'check-1',
    outcome: 'passed',
    feedback_key: 'ok',
    message: 'Task one passed.',
  },
  {
    task_id: 'task-2',
    check_id: 'check-2',
    outcome: 'failed',
    feedback_key: 'retry',
    message: 'Task two needs another look.',
  },
];
const RUN_FINAL = { passed: 'failed' };

// `lab-code` is a code-exercise lab (editor instead of terminal) with no listed
// session, so the page offers "Start lab" (task 16.5 editor focus check).
const CODE_LAB_ID = 'lab-code';

function labBody(labId, locale) {
  const fr = locale === 'fr';
  const code = labId === CODE_LAB_ID;
  return {
    lab_id: labId,
    kind: code ? 'code' : 'shell',
    title: fr ? 'Atelier de test' : 'Test lab',
    locale,
    concept_ids: ['concept-1'],
    tasks: [
      {
        task_id: 'task-1',
        required: true,
        text: fr ? 'Premi�re t�che' : 'First task',
        hint_count: 2,
      },
      {
        task_id: 'task-2',
        required: false,
        text: fr ? 'Seconde t�che' : 'Second task',
        hint_count: 1,
      },
    ],
    learner_files: code ? ['main.py'] : [],
  };
}

function sessionBody(sessionId) {
  const state = stateOf(sessionId);
  const now = Date.now();
  const iso = (ms) => new Date(ms).toISOString();
  const failed = state === 'error';
  return {
    stable_id: `sess-${state}`,
    lab_id: `lab-${state}`,
    status: SESSION_STATUS[state],
    created_at: iso(now - 60_000),
    ttl_at: iso(now + 30 * 60_000),
    idle_deadline: iso(now + 15 * 60_000),
    grading_available_at: state === 'grading' ? iso(now + 10_000) : null,
    ended_at: null,
    server_now: iso(now),
    error: failed
      ? { type: 'about:blank', title: 'Lab unavailable', detail: 'The lab could not be started.' }
      : null,
  };
}

function runBody(runId) {
  const state = stateOf(runId);
  const now = Date.now();
  const iso = (ms) => new Date(ms).toISOString();
  const done = state === 'results';
  return {
    stable_id: `run-${state}`,
    session_id: `sess-${state}`,
    status: done ? RUN_FINAL.passed : 'running',
    accepted_at: iso(now - 2000),
    deadline_at: iso(now + 13_000),
    started_at: iso(now - 1000),
    graded_at: done ? iso(now) : null,
    results: done ? RESULTS : [],
  };
}

const sse = (event, id, data) => `event: ${event}\nid: ${id}\ndata: ${JSON.stringify(data)}\n\n`;

function labStream(runId, res) {
  const state = stateOf(runId);
  res.writeHead(200, { 'content-type': 'text/event-stream', 'cache-control': 'no-store' });
  if (state === 'results') {
    RESULTS.forEach((r, i) => res.write(sse('result', i + 1, r)));
    const run = runBody(runId);
    res.end(
      sse('run', RESULTS.length + 1, {
        stable_id: run.stable_id,
        session_id: run.session_id,
        status: run.status,
        graded_at: run.graded_at,
      }),
    );
    return;
  }
  if (state === 'reconnecting') {
    // One result, then the connection drops so the client shows its reconnect state.
    res.write(sse('result', 1, RESULTS[0]));
    setTimeout(() => res.destroy(), 50);
    return;
  }
  // grading (and any other state): stay open, no results yet.
  res.write(': open\n\n');
}

/** Returns `{ status, body }` for lab routes, or undefined when not a lab route. */
function labRoute(method, url, acceptLocale) {
  const path = url.pathname.replace(/^\/v1/, '');
  const locale = url.searchParams.get('locale') ?? acceptLocale ?? 'en';
  let m = path.match(/^\/labs\/([^/]+)$/);
  if (m && method === 'GET') return { status: 200, body: labBody(m[1], locale) };
  if (path === '/lab-sessions' && method === 'POST')
    return { status: 201, body: sessionBody('lab-ready'), startFor: true };
  if (path === '/lab-sessions' && method === 'GET') {
    return {
      status: 200,
      body: { items: LAB_STATES.map((s) => sessionBody(s)), next_cursor: null },
    };
  }
  m = path.match(/^\/lab-sessions\/([^/]+)$/);
  if (m && method === 'GET') return { status: 200, body: sessionBody(m[1]) };
  if (m && method === 'DELETE')
    return { status: 200, body: { ...sessionBody(m[1]), status: 'ended' } };
  m = path.match(/^\/lab-sessions\/([^/]+)\/(reset|files|hints|grading-runs)$/);
  if (m) {
    const [, sid, action] = m;
    if (action === 'reset' && method === 'POST') return { status: 201, body: sessionBody(sid) };
    if (action === 'files' && method === 'PUT') return { status: 200, body: { saved: 0 } };
    if (action === 'hints' && method === 'POST') {
      return {
        status: 200,
        body: {
          task_id: 'task-1',
          level: 1,
          total_levels: 2,
          newly_revealed: true,
          text: 'A first hint.',
        },
      };
    }
    if (action === 'grading-runs' && method === 'POST') return { status: 202, body: runBody(sid) };
  }
  m = path.match(/^\/lab-sessions\/([^/]+)\/grading-runs\/([^/]+)(\/events)?$/);
  if (m && method === 'GET') {
    return m[3] ? { stream: m[2] } : { status: 200, body: runBody(m[2]) };
  }
  return undefined;
}
// ---- Auth routes in the real API shapes (api-route-wiring) -----------------
// GET /auth/csrf sets the readable `csrf_token` cookie; unsafe calls must echo it
// in `X-CSRF-Token` (double submit). Sign-in answers with the snake_case wire
// member `redirect_to`; verify-email answers 204 for the token `valid-token` and a
// 400 Problem_Details otherwise.
const csrfOk = (req) => {
  const cookie = /(?:^|;\s*)csrf_token=([^;]+)/.exec(req.headers.cookie ?? '');
  const header = req.headers['x-csrf-token'];
  return cookie !== null && typeof header === 'string' && decodeURIComponent(cookie[1]) === header;
};
const problem = (status, detail) => ({
  status,
  body: { type: 'about:blank', title: detail, status, detail },
});

function authRoute(req, url, raw) {
  const path = url.pathname.replace(/^\/v1/, '');
  if (!path.startsWith('/auth/')) return undefined;
  if (path === '/auth/csrf' && req.method === 'GET') {
    return {
      status: 204,
      headers: { 'set-cookie': 'csrf_token=mock-csrf-token; Path=/; SameSite=Lax' },
    };
  }
  if (req.method !== 'POST') return undefined;
  if (!csrfOk(req)) return problem(403, 'CSRF check failed');
  let json;
  try {
    json = JSON.parse(raw || '{}');
  } catch {
    return problem(422, 'Invalid body');
  }
  if (path === '/auth/sign-up') return { status: 202, body: {} };
  if (path === '/auth/verify-email/resend') return { status: 202, body: {} };
  if (path === '/auth/sign-in') return { status: 200, body: { redirect_to: '/dashboard' } };
  // Milestone 2: reset request (202), reset completion (token `valid-token` signs in,
  // anything else is the 400 invalid-or-expired link), and the GitHub link step.
  if (path === '/auth/password-reset') return { status: 202, body: {} };
  if (path === '/auth/password-reset/complete') {
    return json.token === 'valid-token'
      ? { status: 200, body: { redirect_to: '/dashboard' } }
      : problem(400, 'Invalid or expired link');
  }
  if (path === '/auth/github/confirm') return { status: 200, body: { redirect_to: '/dashboard' } };
  if (path === '/auth/github/decline') return { status: 204 };
  if (path === '/auth/verify-email') {
    return json.token === 'valid-token' ? { status: 204 } : problem(400, 'Invalid or expired link');
  }
  return undefined;
}

function route(method, url) {
  if (method !== 'GET') return undefined;
  const { pathname } = url;
  const path = pathname.replace(/^\/v1/, '');
  if (path === '/auth/session') return { user_id: 'u-1' };
  if (path === '/enrollments') return fixtures.enrollments;
  if (path === '/progress') return fixtures.progress;
  if (path === '/reviews/schedule') return fixtures.schedule;
  if (path.startsWith('/lessons/')) {
    const locale = url.searchParams.get('locale') ?? 'en';
    return { ...fixtures.lesson, locale };
  }
  if (path.startsWith('/quizzes/')) return fixtures.quiz;
  if (path === '/reviews') return fixtures.review;
  if (path.startsWith('/projects/')) return fixtures.project;
  return undefined;
}

createServer((req, res) => {
  const url = new URL(req.url ?? '/', `http://localhost:${PORT}`);
  // CORS for the client-side Lab_View (credentialed, cross-origin).
  const origin = req.headers.origin;
  if (origin) {
    res.setHeader('access-control-allow-origin', origin);
    res.setHeader('access-control-allow-credentials', 'true');
    res.setHeader(
      'access-control-allow-headers',
      'content-type, accept, accept-language, idempotency-key, last-event-id, x-csrf-token',
    );
    res.setHeader('access-control-allow-methods', 'GET, POST, PUT, DELETE, OPTIONS');
    res.setHeader('vary', 'origin');
  }
  if (req.method === 'OPTIONS') {
    res.statusCode = 204;
    res.end();
    return;
  }
  if (url.pathname.replace(/^\/v1/, '').startsWith('/auth/') && req.method !== 'GET') {
    let raw = '';
    req.on('data', (chunk) => (raw += chunk));
    req.on('end', () => {
      const auth = authRoute(req, url, raw);
      res.setHeader('content-type', 'application/json');
      res.statusCode = auth?.status ?? 404;
      for (const [k, v] of Object.entries(auth?.headers ?? {})) res.setHeader(k, v);
      res.end(auth?.body === undefined ? undefined : JSON.stringify(auth.body));
    });
    return;
  }
  const authGet = req.method === 'GET' ? authRoute(req, url, '') : undefined;
  if (authGet && url.pathname.endsWith('/auth/csrf')) {
    res.statusCode = authGet.status;
    for (const [k, v] of Object.entries(authGet.headers ?? {})) res.setHeader(k, v);
    res.end();
    return;
  }
  const lab = labRoute(req.method ?? 'GET', url, req.headers['accept-language']);
  if (lab?.stream !== undefined) {
    labStream(lab.stream, res);
    return;
  }
  if (lab) {
    res.setHeader('content-type', 'application/json');
    res.statusCode = lab.status;
    res.end(JSON.stringify(lab.body));
    return;
  }
  const body = route(req.method ?? 'GET', url);
  res.setHeader('content-type', 'application/json');
  if (body === undefined) {
    res.statusCode = 404;
    res.end(JSON.stringify({ title: 'Not found' }));
    return;
  }
  res.end(JSON.stringify(body));
}).listen(PORT, () => {
  console.log(`mock API listening on ${PORT}`);
});
