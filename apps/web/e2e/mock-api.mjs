// Minimal in-process stand-in for the EDU-IT API, used ONLY by the Playwright
// accessibility run (task 24.2). It serves deterministic Seed_Content-shaped
// fixtures for the routes the learner pages read, so the pages render real
// content without a database. Run: `node e2e/mock-api.mjs [port]`.
/* global process, URL, console */
import { createServer } from 'node:http';

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
