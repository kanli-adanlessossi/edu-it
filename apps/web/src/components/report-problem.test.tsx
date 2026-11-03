// @vitest-environment jsdom
//
// Component tests for the "Report a problem" control (ai-content task 20.5).
//
// Validates: Requirements 18.1
//
// The control is shown on each view (question/quiz, lesson, lab) in FR and EN, opens
// a form with a category and an optional comment, and is axe-core clean closed and open.
import type { ReactNode } from 'react';
import axe from 'axe-core';
import { NextIntlClientProvider } from 'next-intl';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import en from '../messages/en.json';
import fr from '../messages/fr.json';
import type { ProblemReportApi, ProblemReportItemKind } from '../problem-report.js';
import { ReportProblem } from './report-problem.js';

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

const CATALOGS = { en, fr } as const;
type L = keyof typeof CATALOGS;
const LOCALES: readonly L[] = ['fr', 'en'];
const KINDS: readonly ProblemReportItemKind[] = ['question', 'lesson', 'lab'];

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

function fakeApi(): ProblemReportApi & { submit: ReturnType<typeof vi.fn> } {
  return {
    submit: vi.fn(async () => ({ stableId: 'pr-1', replayed: false })),
  } as never;
}

async function mount(
  locale: L,
  kind: ProblemReportItemKind,
  api: ProblemReportApi = fakeApi(),
  contentVersion: string | null = 'v1',
) {
  const node: ReactNode = (
    <NextIntlClientProvider locale={locale} messages={CATALOGS[locale]} timeZone="UTC">
      <main lang={locale}>
        <h1>{CATALOGS[locale].ProblemReport.open}</h1>
        <ReportProblem
          locale={locale}
          itemKind={kind}
          itemId="item-1"
          contentVersion={contentVersion ?? undefined}
          number={2}
          api={api}
        />
      </main>
    </NextIntlClientProvider>
  );
  await act(async () => {
    root.render(node);
  });
}

async function openForm() {
  await act(async () => {
    container.querySelector<HTMLButtonElement>('button[aria-expanded]')!.click();
  });
}

async function violations(): Promise<string[]> {
  const results = await axe.run(container, {
    resultTypes: ['violations'],
    rules: { 'color-contrast': { enabled: false }, region: { enabled: false } },
  });
  return results.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.html).join(' | ')}`);
}

describe.each(LOCALES)('report problem control (%s)', (locale) => {
  const T = CATALOGS[locale].ProblemReport;

  it.each(KINDS)('is shown on the %s view and is axe clean closed and open', async (kind) => {
    await mount(locale, kind);
    const open = container.querySelector<HTMLButtonElement>('button[aria-expanded]')!;
    expect(open).not.toBeNull();
    expect(open.textContent).toBe(T.open);
    expect(await violations()).toEqual([]);

    await openForm();
    const form = container.querySelector('form')!;
    expect(form.getAttribute('aria-label')).toContain(
      kind === 'question'
        ? T.itemQuestion.replace('{number}', '2')
        : T[kind === 'lesson' ? 'itemLesson' : 'itemLab'],
    );
    expect(container.querySelector('label')?.textContent).toBe(T.categoryLabel);
    expect(container.querySelectorAll('select option')).toHaveLength(7);
    expect(container.querySelector('textarea')).not.toBeNull();
    expect(await violations()).toEqual([]);
  });

  it('shows an accessible error, still axe clean, when no category is chosen', async () => {
    const api = fakeApi();
    await mount(locale, 'lesson', api);
    await openForm();
    await act(async () => {
      container.querySelector('form')!.requestSubmit();
    });
    expect(container.querySelector('[role="alert"]')?.textContent).toBe(T.categoryRequired);
    expect(api.submit).not.toHaveBeenCalled();
    expect(await violations()).toEqual([]);
  });

  it('submits the chosen category and confirms', async () => {
    const api = fakeApi();
    await mount(locale, 'lab', api);
    await openForm();
    const select = container.querySelector<HTMLSelectElement>('select')!;
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')?.set;
      setter?.call(select, 'unclear');
      select.dispatchEvent(new Event('change', { bubbles: true }));
    });
    await act(async () => {
      container.querySelector('form')!.requestSubmit();
    });
    expect(api.submit).toHaveBeenCalledTimes(1);
    expect(api.submit.mock.calls[0]![0]).toMatchObject({
      itemKind: 'lab',
      itemId: 'item-1',
      contentVersion: 'v1',
      category: 'unclear',
    });
    expect(container.querySelector('[role="status"]')?.textContent).toBe(T.sent);
    expect(await violations()).toEqual([]);
  });

  it('renders nothing without a content version', async () => {
    await mount(locale, 'lesson', fakeApi(), null);
    expect(container.querySelector('button')).toBeNull();
  });
});
