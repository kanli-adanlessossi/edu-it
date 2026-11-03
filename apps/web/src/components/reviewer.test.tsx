// @vitest-environment jsdom
//
// Component tests for the reviewer queue and task detail (ai-content task 16.2).
//
// Validates: Requirements 15.9
//
// The components take an injected `ReviewerApi`, so the tests drive every action,
// the error mappings and the pagination against a recording fake in both locales.
import type { ReactNode } from 'react';
import { NextIntlClientProvider } from 'next-intl';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import en from '../messages/en.json';
import fr from '../messages/fr.json';
import {
  ReviewerApiError,
  parseVariantBody,
  type ReviewTaskDetail,
  type ReviewTaskPage,
  type ReviewTaskView,
  type ReviewerApi,
} from '../review-tasks.js';
import { ReviewQueue } from './review-queue.js';
import { ReviewTaskDetailView } from './review-task-detail.js';

vi.mock('next/link', () => ({
  __esModule: true,
  default: ({
    href,
    children,
    ...rest
  }: { href: string; children?: ReactNode } & Record<string, unknown>) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

const CATALOGS = { en, fr } as const;
type L = keyof typeof CATALOGS;
const LOCALES: readonly L[] = ['fr', 'en'];

const STORED_BODY = {
  prompt_text: { fr: 'Quelle commande ?', en: 'Which command?' },
  explanation: { fr: 'Parce que.', en: 'Because.' },
  options: [
    { text: { fr: 'ls', en: 'ls' }, is_correct: true },
    { text: { fr: 'cd', en: 'cd' }, is_correct: false },
  ],
};

function task(overrides: Partial<ReviewTaskView> = {}): ReviewTaskView {
  return {
    stableId: 'rt-1',
    kind: 'new_variant',
    targetKind: 'variant',
    targetStableId: 'var-1',
    trackId: 'track-1',
    state: 'open',
    createdAt: '2030-01-01T10:00:00Z',
    revisionContentVersion: 'v1',
    reason: null,
    comment: null,
    reviewerId: null,
    decidedAt: null,
    ...overrides,
  };
}

function detail(overrides: Partial<ReviewTaskDetail['task']> = {}): ReviewTaskDetail {
  return {
    task: task(overrides),
    variant: {
      stableId: 'var-1',
      parentQuestionId: 'q-9',
      trackId: 'track-1',
      state: 'pending_review',
      approvedContentVersion: null,
    },
    revision: {
      contentVersion: 'v1',
      state: 'pending_review',
      body: parseVariantBody(STORED_BODY),
      origin: 'ai_generated',
      model: 'model-x',
      promptId: 'variant-gen',
      promptVersion: '3',
      generationDate: '2030-01-01T09:00:00Z',
      reviewedBy: null,
      reviewedAt: null,
      findings: [{ kind: 'duplicate', target: 'prompt', detail: 'Matches q-2' }],
      createdAt: '2030-01-01T09:00:00Z',
    },
  };
}

function fakeApi(overrides: Partial<ReviewerApi> = {}): ReviewerApi & {
  decide: ReturnType<typeof vi.fn>;
  edit: ReturnType<typeof vi.fn>;
  list: ReturnType<typeof vi.fn>;
} {
  return {
    list: vi.fn(async () => ({ items: [], nextCursor: null })),
    get: vi.fn(async () => detail()),
    decide: vi.fn(async () => task({ state: 'approved' })),
    edit: vi.fn(async () => task({ stableId: 'rt-2' })),
    ...overrides,
  } as never;
}

let container: HTMLElement;
let root: Root;

beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

async function mount(locale: L, node: ReactNode) {
  await act(async () => {
    root.render(
      <NextIntlClientProvider locale={locale} messages={CATALOGS[locale]} timeZone="UTC">
        {node}
      </NextIntlClientProvider>,
    );
  });
}

const R = (locale: L) => CATALOGS[locale].Reviewer;

function button(name: string): HTMLButtonElement {
  const found = Array.from(container.querySelectorAll('button')).find(
    (b) => b.textContent === name,
  );
  if (!found) throw new Error(`no button "${name}"`);
  return found as HTMLButtonElement;
}

async function click(name: string) {
  await act(async () => {
    button(name).click();
  });
}

/** Set a React-controlled textarea value the way a user typing would. */
async function type(el: HTMLTextAreaElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')?.set;
  await act(async () => {
    setter?.call(el, value);
    el.dispatchEvent(new Event('input', { bubbles: true }));
  });
}

const comment = () => container.querySelector<HTMLTextAreaElement>('#review-comment')!;
const alertText = () => container.querySelector('[role="alert"]')?.textContent ?? null;

describe.each(LOCALES)('review queue (%s)', (locale) => {
  it('shows the empty state', async () => {
    await mount(
      locale,
      <ReviewQueue locale={locale} initial={{ items: [], nextCursor: null }} api={fakeApi()} />,
    );
    expect(container.querySelector('[role="status"]')?.textContent).toBe(R(locale).queue.empty);
    expect(container.querySelector('ul')).toBeNull();
    expect(container.querySelector('button')).toBeNull();
  });

  it('lists tasks with links and loads more through the cursor', async () => {
    const second: ReviewTaskPage = {
      items: [task({ stableId: 'rt-2', targetStableId: 'var-2' })],
      nextCursor: null,
    };
    const api = fakeApi({ list: vi.fn(async () => second) as never });
    await mount(
      locale,
      <ReviewQueue locale={locale} initial={{ items: [task()], nextCursor: 'cur-1' }} api={api} />,
    );
    expect(container.querySelectorAll('li')).toHaveLength(1);
    expect(container.querySelector('a')?.getAttribute('href')).toBe(`/${locale}/reviewer/rt-1`);

    await click(R(locale).queue.loadMore);
    expect(api.list).toHaveBeenCalledWith('cur-1');
    expect(container.querySelectorAll('li')).toHaveLength(2);
    expect(container.querySelectorAll('a')[1]?.getAttribute('href')).toBe(
      `/${locale}/reviewer/rt-2`,
    );
    // The last page hides the button.
    expect(container.querySelector('button')).toBeNull();
  });

  it('maps a 403 and other failures while loading more', async () => {
    const api = fakeApi({
      list: vi
        .fn()
        .mockRejectedValueOnce(new ReviewerApiError('', 403))
        .mockRejectedValueOnce(new ReviewerApiError('', 500)) as never,
    });
    await mount(
      locale,
      <ReviewQueue locale={locale} initial={{ items: [task()], nextCursor: 'c' }} api={api} />,
    );
    await click(R(locale).queue.loadMore);
    expect(alertText()).toBe(R(locale).errors.expertise);
    // The cursor is kept, so the reviewer can retry.
    await click(R(locale).queue.loadMore);
    expect(alertText()).toBe(R(locale).errors.generic);
    expect(container.querySelectorAll('li')).toHaveLength(1);
  });
});

describe.each(LOCALES)('review task detail (%s)', (locale) => {
  const view = (api: ReviewerApi, d = detail()) => (
    <ReviewTaskDetailView locale={locale} detail={d} api={api} />
  );

  it('shows both language variants, provenance and findings', async () => {
    await mount(locale, view(fakeApi()));
    const sections = Array.from(container.querySelectorAll('section[lang]'));
    expect(sections.map((s) => s.getAttribute('lang'))).toEqual(['fr', 'en']);
    expect(sections[0]?.textContent).toContain('Quelle commande ?');
    expect(sections[1]?.textContent).toContain('Which command?');
    expect(container.textContent).toContain('model-x');
    expect(container.textContent).toContain('variant-gen v3');
    expect(container.textContent).toContain('Matches q-2');
    expect(container.textContent).toContain(R(locale).findings.kind.duplicate);
  });

  it.each([
    ['approve', 'approve'],
    ['reject', 'reject'],
  ] as const)('%s calls the API and shows the outcome', async (action, key) => {
    const api = fakeApi();
    await mount(locale, view(api));
    await type(comment(), 'looks good');
    await click(R(locale).actions[action]);
    expect(api.decide).toHaveBeenCalledWith(action, 'rt-1', 'looks good');
    expect(container.querySelector('[role="status"]')?.textContent).toBe(R(locale).outcome[key]);
    // Once decided the actions disappear.
    expect(container.querySelector('#review-comment')).toBeNull();
  });

  it('requires a comment to request changes, then sends it', async () => {
    const api = fakeApi();
    await mount(locale, view(api));
    await click(R(locale).actions.requestChanges);
    expect(alertText()).toBe(R(locale).errors.commentRequired);
    expect(api.decide).not.toHaveBeenCalled();

    await type(comment(), 'Fix option 2');
    await click(R(locale).actions.requestChanges);
    expect(api.decide).toHaveBeenCalledWith('requestChanges', 'rt-1', 'Fix option 2');
    expect(container.querySelector('[role="status"]')?.textContent).toBe(
      R(locale).outcome.requestChanges,
    );
  });

  it('rejects an over-long comment before calling the API', async () => {
    const api = fakeApi();
    await mount(locale, view(api));
    // maxLength blocks typing, so set the DOM value directly past the limit.
    const area = comment();
    area.removeAttribute('maxlength');
    await type(area, 'x'.repeat(2001));
    await click(R(locale).actions.approve);
    expect(alertText()).toBe(R(locale).errors.commentTooLong.replace('{max}', '2000'));
    expect(api.decide).not.toHaveBeenCalled();
  });

  it.each([
    [403, 'https://x/problems/reviewer-expertise-required', 'expertise'],
    [409, 'https://x/problems/review-task-already-decided', 'alreadyDecided'],
    [404, 'about:blank', 'notFound'],
    [422, 'about:blank', 'invalid'],
    [401, 'about:blank', 'unauthenticated'],
    [500, 'about:blank', 'generic'],
  ] as const)('maps %i to the %s message', async (status, type_, key) => {
    const api = fakeApi({
      decide: vi.fn().mockRejectedValue(new ReviewerApiError('raw', status, type_)) as never,
    });
    await mount(locale, view(api));
    await click(R(locale).actions.approve);
    expect(alertText()).toBe(R(locale).errors[key]);
    // The task stays open so the reviewer can retry.
    expect(container.querySelector('#review-comment')).not.toBeNull();
  });

  it('edits both languages, sends the stored body with changed texts and links the new task', async () => {
    const api = fakeApi();
    await mount(locale, view(api));
    await click(R(locale).actions.edit);

    // Saving without a change is refused.
    await click(R(locale).actions.saveEdit);
    expect(alertText()).toBe(R(locale).errors.editUnchanged);
    expect(api.edit).not.toHaveBeenCalled();

    const en = container.querySelector<HTMLTextAreaElement>('#prompt-en')!;
    expect(en.getAttribute('lang')).toBe('en');
    expect(container.querySelector('#prompt-fr')?.getAttribute('lang')).toBe('fr');
    await type(en, 'Which command lists files?');
    await click(R(locale).actions.saveEdit);

    expect(api.edit).toHaveBeenCalledTimes(1);
    const [id, body] = api.edit.mock.calls[0] as [string, Record<string, unknown>];
    expect(id).toBe('rt-1');
    expect(body.prompt_text).toEqual({ fr: 'Quelle commande ?', en: 'Which command lists files?' });
    expect(body.explanation).toEqual(STORED_BODY.explanation);
    const link = container.querySelector('[role="status"] a');
    expect(link?.getAttribute('href')).toBe(`/${locale}/reviewer/rt-2`);
    expect(container.querySelector('[role="status"]')?.textContent).toContain(
      R(locale).outcome.edit,
    );
  });

  it('refuses an edit with a blank text and can be cancelled', async () => {
    const api = fakeApi();
    await mount(locale, view(api));
    await click(R(locale).actions.edit);
    await type(container.querySelector<HTMLTextAreaElement>('#explanation-fr')!, '   ');
    await click(R(locale).actions.saveEdit);
    expect(alertText()).toBe(R(locale).errors.editIncomplete);
    await click(R(locale).actions.cancelEdit);
    expect(container.querySelector('#prompt-fr')).toBeNull();
    expect(api.edit).not.toHaveBeenCalled();
  });

  it('maps an edit failure', async () => {
    const api = fakeApi({
      edit: vi.fn().mockRejectedValue(new ReviewerApiError('', 409)) as never,
    });
    await mount(locale, view(api));
    await click(R(locale).actions.edit);
    await type(container.querySelector<HTMLTextAreaElement>('#prompt-fr')!, 'Autre ?');
    await click(R(locale).actions.saveEdit);
    expect(alertText()).toBe(R(locale).errors.alreadyDecided);
  });

  it('hides the actions once the task is not open', async () => {
    await mount(locale, view(fakeApi(), detail({ state: 'approved' })));
    expect(container.querySelector('#review-comment')).toBeNull();
    expect(container.textContent).toContain(R(locale).state.approved);
    expect(Array.from(container.querySelectorAll('button'))).toHaveLength(0);
  });
});
