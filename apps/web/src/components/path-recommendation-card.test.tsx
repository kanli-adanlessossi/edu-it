// @vitest-environment jsdom
//
// Component tests for the dashboard path recommendation card (ai-content task 22.8).
//
// Validates: Requirements 17.7, 17.8
//
// The card is shown with the ordered concepts in FR and EN, dismissal replaces it with a
// focused status message, a failed dismissal keeps it and announces an alert, and every
// state is axe-core clean.
import axe from 'axe-core';
import { NextIntlClientProvider } from 'next-intl';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import en from '../messages/en.json';
import fr from '../messages/fr.json';
import {
  PathRecommendationApiError,
  type PathRecommendation,
  type PathRecommendationApi,
} from '../path-recommendation.js';
import { PathRecommendationCard } from './path-recommendation-card.js';

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

const CATALOGS = { en, fr } as const;
type L = keyof typeof CATALOGS;
const LOCALES: readonly L[] = ['fr', 'en'];

const RECOMMENDATION: PathRecommendation = {
  stable_id: 'rec-1',
  source: 'llm',
  ordering: ['dns-basics', 'ip-addressing', 'routing'],
  created_at: '2025-01-01T00:00:00Z',
};

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

function fakeApi(fail = false): PathRecommendationApi & { dismiss: ReturnType<typeof vi.fn> } {
  return {
    get: vi.fn(async () => null),
    dismiss: vi.fn(async () => {
      if (fail) throw new PathRecommendationApiError(500);
    }),
  } as never;
}

async function mount(
  locale: L,
  api: PathRecommendationApi,
  recommendation: PathRecommendation = RECOMMENDATION,
) {
  await act(async () => {
    root.render(
      <NextIntlClientProvider locale={locale} messages={CATALOGS[locale]} timeZone="UTC">
        <main lang={locale}>
          <h1>{CATALOGS[locale].PathRecommendation.heading}</h1>
          <PathRecommendationCard locale={locale} recommendation={recommendation} api={api} />
        </main>
      </NextIntlClientProvider>,
    );
  });
}

async function clickDismiss() {
  await act(async () => {
    container.querySelector<HTMLButtonElement>('button')!.click();
  });
}

async function violations(): Promise<string[]> {
  const results = await axe.run(container, {
    resultTypes: ['violations'],
    rules: { 'color-contrast': { enabled: false }, region: { enabled: false } },
  });
  return results.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.html).join(' | ')}`);
}

describe.each(LOCALES)('path recommendation card (%s)', (locale) => {
  const T = CATALOGS[locale].PathRecommendation;

  it('is shown with the ordered concepts and a dismiss button, axe clean', async () => {
    await mount(locale, fakeApi());
    expect(container.querySelector('h2')?.textContent).toBe(T.heading);
    expect(container.querySelector('section')?.getAttribute('aria-labelledby')).toBe(
      container.querySelector('h2')?.id,
    );
    const items = [...container.querySelectorAll('ol li')].map((li) => li.textContent);
    expect(items).toEqual(RECOMMENDATION.ordering);
    expect(container.textContent).toContain(T.sourceLlm);
    expect(container.querySelector('button')?.textContent).toBe(T.dismiss);
    expect(container.querySelector('[role="alert"]')).toBeNull();
    expect(await violations()).toEqual([]);
  });

  it('words a rule-based suggestion differently and omits the note for unknown sources', async () => {
    await mount(locale, fakeApi(), { ...RECOMMENDATION, source: 'rule-based' });
    expect(container.textContent).toContain(T.sourceRuleBased);
    expect(container.textContent).not.toContain(T.sourceLlm);

    await mount(locale, fakeApi(), { ...RECOMMENDATION, source: 'other' });
    expect(container.textContent).not.toContain(T.sourceRuleBased);
    expect(container.textContent).not.toContain(T.sourceLlm);
  });

  it('dismissal hides the card, shows a focused status message and is axe clean', async () => {
    const api = fakeApi();
    await mount(locale, api);
    await clickDismiss();

    expect(api.dismiss).toHaveBeenCalledTimes(1);
    expect(api.dismiss).toHaveBeenCalledWith('rec-1');
    expect(container.querySelector('section')).toBeNull();
    expect(container.querySelector('ol')).toBeNull();
    expect(container.querySelector('button')).toBeNull();
    const status = container.querySelector<HTMLElement>('[role="status"]')!;
    expect(status.textContent).toBe(T.dismissed);
    expect(document.activeElement).toBe(status);
    expect(await violations()).toEqual([]);
  });

  it('keeps the card and announces an alert when dismissal fails, axe clean', async () => {
    const api = fakeApi(true);
    await mount(locale, api);
    await clickDismiss();

    expect(api.dismiss).toHaveBeenCalledTimes(1);
    expect(container.querySelector('section')).not.toBeNull();
    expect(container.querySelector('[role="alert"]')?.textContent).toBe(T.dismissError);
    expect(container.querySelector('[role="status"]')).toBeNull();
    const button = container.querySelector<HTMLButtonElement>('button')!;
    expect(button.disabled).toBe(false);
    expect(await violations()).toEqual([]);
  });
});
