// Unit and property tests for the reviewer queue logic and API adapter.
//
// Validates: Requirements 15.9
import fc from 'fast-check';
import { describe, expect, it } from 'vitest';

import {
  COMMENT_MAX_CHARS,
  ReviewerApiError,
  buildEditedBody,
  classifyReviewerError,
  isBodyChanged,
  isBodyComplete,
  parseVariantBody,
  validateComment,
  type DecisionAction,
} from './review-tasks.js';
import { createReviewerApi } from './review-tasks-api.js';
import type { GeneratedClient } from './generated/api-client.js';

const STORED = {
  prompt_text: { fr: 'Question ?', en: 'Question?' },
  explanation: { fr: 'Parce que.', en: 'Because.' },
  options: [
    { text: { fr: 'Oui', en: 'Yes' }, is_correct: true, option_id: 11 },
    { text: { fr: 'Non', en: 'No' }, is_correct: false, option_id: 12 },
  ],
  extra: 'kept',
};

const TASK = {
  stable_id: 'rt-1',
  kind: 'new_variant',
  target_kind: 'variant',
  target_stable_id: 'var-1',
  track_id: 'track-1',
  state: 'open',
  created_at: '2030-01-01T00:00:00Z',
};

interface Recorded {
  method: string;
  path: string;
  init?: RequestInit;
}

function fakeClient(respond: (r: Recorded) => Response): {
  client: GeneratedClient;
  calls: Recorded[];
} {
  const calls: Recorded[] = [];
  const client = {
    call: async (method: string, path: string, init?: RequestInit) => {
      const recorded = { method, path, init };
      calls.push(recorded);
      return respond(recorded);
    },
  } as unknown as GeneratedClient;
  return { client, calls };
}

const json = (body: unknown, status = 200): Response =>
  new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });

describe('validateComment', () => {
  it('requires a comment to request changes only', () => {
    expect(validateComment('requestChanges', '   ')).toBe('required');
    expect(validateComment('requestChanges', 'Fix the second option')).toBeNull();
    expect(validateComment('approve', '')).toBeNull();
    expect(validateComment('reject', '')).toBeNull();
  });

  it('rejects comments over the limit for every action', () => {
    const long = 'x'.repeat(COMMENT_MAX_CHARS + 1);
    expect(validateComment('approve', long)).toBe('tooLong');
    expect(validateComment('approve', 'x'.repeat(COMMENT_MAX_CHARS))).toBeNull();
  });

  it('property: only a blank request-changes comment or an oversized one fails', () => {
    fc.assert(
      fc.property(
        fc.constantFrom<DecisionAction>('approve', 'requestChanges', 'reject'),
        fc.string({ maxLength: COMMENT_MAX_CHARS + 50 }),
        (action, comment) => {
          const trimmed = comment.trim();
          const expected =
            action === 'requestChanges' && trimmed.length === 0
              ? 'required'
              : trimmed.length > COMMENT_MAX_CHARS
                ? 'tooLong'
                : null;
          expect(validateComment(action, comment)).toBe(expected);
        },
      ),
    );
  });
});

describe('classifyReviewerError', () => {
  it.each([
    [403, 'https://x/problems/reviewer-expertise-required', 'expertise'],
    [403, 'about:blank', 'expertise'],
    [409, 'https://x/problems/review-task-already-decided', 'alreadyDecided'],
    [409, 'about:blank', 'alreadyDecided'],
    [404, 'about:blank', 'notFound'],
    [422, 'about:blank', 'invalid'],
    [401, 'about:blank', 'unauthenticated'],
    [500, 'about:blank', 'generic'],
  ])('maps %i %s to %s', (status, type, kind) => {
    expect(classifyReviewerError(status, type)).toBe(kind);
    expect(new ReviewerApiError('d', status, type).kind).toBe(kind);
  });
});

describe('variant body helpers', () => {
  it('parses both languages and the options', () => {
    const body = parseVariantBody(STORED);
    expect(body.prompt).toEqual({ fr: 'Question ?', en: 'Question?' });
    expect(body.options.map((o) => o.isCorrect)).toEqual([true, false]);
    expect(isBodyComplete(body)).toBe(true);
  });

  it('builds an edited body that keeps unshown keys and changes only the texts', () => {
    const body = parseVariantBody(STORED);
    const edited = buildEditedBody({ ...body, prompt: { ...body.prompt, en: 'Changed?' } });
    expect(edited.extra).toBe('kept');
    expect(edited.prompt_text).toEqual({ fr: 'Question ?', en: 'Changed?' });
    expect((edited.options as Array<Record<string, unknown>>)[0]?.option_id).toBe(11);
  });

  it('detects unchanged and incomplete edits', () => {
    const body = parseVariantBody(STORED);
    expect(isBodyChanged(body, body)).toBe(false);
    const blank = { ...body, explanation: { ...body.explanation, fr: '  ' } };
    expect(isBodyComplete(blank)).toBe(false);
    expect(isBodyChanged(body, blank)).toBe(true);
  });
});

describe('createReviewerApi', () => {
  it('lists with the cursor and maps the page', async () => {
    const { client, calls } = fakeClient(() => json({ items: [TASK], next_cursor: 'c2' }));
    const page = await createReviewerApi('fr', { client }).list('a b');
    expect(calls[0]).toMatchObject({ method: 'GET', path: '/review-tasks?cursor=a%20b' });
    expect(page.nextCursor).toBe('c2');
    expect(page.items[0]?.targetStableId).toBe('var-1');
  });

  it.each([
    ['approve', 'approve'],
    ['reject', 'reject'],
    ['requestChanges', 'request-changes'],
  ] as const)('posts the %s decision to /%s with a trimmed comment', async (action, segment) => {
    const { client, calls } = fakeClient(() => json({ ...TASK, state: 'approved' }));
    await createReviewerApi('en', { client }).decide(action, 'rt-1', '  note  ');
    expect(calls[0]?.method).toBe('POST');
    expect(calls[0]?.path).toBe(`/review-tasks/rt-1/${segment}`);
    expect(JSON.parse(String(calls[0]?.init?.body))).toEqual({ comment: 'note' });
  });

  it('omits an empty comment and wraps the edit body', async () => {
    const { client, calls } = fakeClient(() => json(TASK, 201));
    const api = createReviewerApi('en', { client });
    await api.decide('approve', 'rt-1', '');
    await api.edit('rt-1', { a: 1 });
    expect(JSON.parse(String(calls[0]?.init?.body))).toEqual({});
    expect(calls[1]?.path).toBe('/review-tasks/rt-1/edit');
    expect(JSON.parse(String(calls[1]?.init?.body))).toEqual({ body: { a: 1 } });
  });

  it('turns problem documents into classified errors', async () => {
    const { client } = fakeClient(() =>
      json(
        { type: 'https://x/problems/review-task-already-decided', detail: 'Already decided' },
        409,
      ),
    );
    const error = await createReviewerApi('en', { client })
      .decide('approve', 'rt-1', '')
      .catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ReviewerApiError);
    expect((error as ReviewerApiError).kind).toBe('alreadyDecided');
    expect((error as ReviewerApiError).detail).toBe('Already decided');
  });

  it('maps a detail document with no revision', async () => {
    const { client } = fakeClient(() => json({ task: TASK, variant: null, revision: null }));
    const detail = await createReviewerApi('en', { client }).get('rt-1');
    expect(detail.revision).toBeNull();
    expect(detail.task.state).toBe('open');
  });
});
