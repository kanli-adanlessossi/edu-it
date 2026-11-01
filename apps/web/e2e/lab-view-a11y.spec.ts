// Playwright + axe-core scan of the Lab_View under /fr and /en in the ready,
// grading, results, error, and reconnecting states (task 16.5; Req 16.1, 16.4,
// 16.5), plus keyboard checks that focus is never trapped in the terminal or the
// editor. States come from the deterministic mock API (e2e/mock-api.mjs): the
// lab id `lab-<state>` selects the session `sess-<state>`, and `lab-code` is a
// code-exercise lab rendered with the editor.
import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';

const SESSION_COOKIE = 'edu_it_session';
const WEB_ORIGIN = 'http://localhost:3100';

type Locale = 'fr' | 'en';
type LabState = 'ready' | 'grading' | 'results' | 'error' | 'reconnecting';

const TEXT: Record<Locale, { check: string; start: string }> = {
  en: { check: 'Check my work', start: 'Start lab' },
  fr: { check: 'Vérifier mon travail', start: 'Démarrer le labo' },
};

test.beforeEach(async ({ context }) => {
  await context.addCookies([{ name: SESSION_COOKIE, value: 'e2e-session', url: WEB_ORIGIN }]);
});

async function openLab(page: Page, locale: Locale, labId: string) {
  const response = await page.goto(`/${locale}/labs/${labId}`);
  expect(response?.status()).toBe(200);
  await expect(page.locator('html')).toHaveAttribute('lang', locale);
  await expect(page.locator('main h1').first()).toBeVisible();
}

/**
 * A failed session is not an Active_Session, so the page never lists it; it is
 * what a start attempt returns. Answer this page's POST /lab-sessions with the
 * mock's own `sess-error` body (status `failed`, with an error detail).
 */
async function startReturnsFailedSession(page: Page) {
  await page.route('**/v1/lab-sessions', async (route) => {
    const request = route.request();
    if (request.method() !== 'POST') return route.fallback();
    const failed = await route.fetch({
      url: request.url().replace(/\/lab-sessions$/, '/lab-sessions/sess-error'),
      method: 'GET',
    });
    return route.fulfill({
      status: 201,
      contentType: 'application/json',
      headers: {
        'access-control-allow-origin': WEB_ORIGIN,
        'access-control-allow-credentials': 'true',
      },
      body: await failed.text(),
    });
  });
}

/** Drives the page into the requested state and waits until it is on screen. */
async function reachState(page: Page, locale: Locale, state: LabState) {
  const check = page.getByRole('button', { name: TEXT[locale].check });
  const results = page.getByTestId('lab-results');
  switch (state) {
    case 'ready':
      await expect(check).toBeVisible();
      await expect(page.getByTestId('terminal-pane')).toBeVisible();
      break;
    case 'grading':
      // The session itself reports `grading`: the running indication shows.
      await expect(check).toHaveAttribute('aria-disabled', 'true');
      await expect(results.locator('p')).toHaveCount(1);
      await expect(results.locator('li')).toHaveCount(0);
      break;
    case 'results':
      await check.click();
      await expect(results.locator('li')).toHaveCount(2);
      break;
    case 'error':
      await page.getByRole('button', { name: TEXT[locale].start }).click();
      await expect(page.locator('#lab-failed-title')).toBeVisible();
      await expect(
        page.getByRole('alert').filter({ hasText: 'could not be started' }),
      ).toBeVisible();
      break;
    case 'reconnecting':
      await check.click();
      await expect(results.locator('li')).toHaveCount(1);
      await expect(page.getByTestId('lab-reconnect')).not.toHaveText('');
      break;
  }
}

async function expectNoAxeViolations(page: Page) {
  const results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'best-practice'])
    .analyze();
  const summary = results.violations.map(
    (v) => `[${v.impact}] ${v.id}: ${v.help}\n` + v.nodes.map((n) => `    ${n.html}`).join('\n'),
  );
  expect(summary, summary.join('\n')).toEqual([]);
}

for (const locale of ['fr', 'en'] as const) {
  for (const state of ['ready', 'grading', 'results', 'error', 'reconnecting'] as const) {
    test(`lab view ${state} (/${locale}) has no accessibility violations`, async ({ page }) => {
      if (state === 'error') await startReturnsFailedSession(page);
      await openLab(page, locale, `lab-${state}`);
      await reachState(page, locale, state);
      await expectNoAxeViolations(page);
    });
  }

  test(`focus is not trapped in the terminal (/${locale})`, async ({ page }) => {
    await openLab(page, locale, 'lab-ready');
    const check = page.getByRole('button', { name: TEXT[locale].check });
    await expect(check).toBeVisible();

    // xterm's helper textarea is the keyboard entry point of the terminal.
    const textarea = page.locator('[data-testid="terminal-container"] textarea');
    await textarea.waitFor({ state: 'attached' });
    await textarea.focus();
    await expect(textarea).toBeFocused();

    // The documented exit chord (Ctrl+M) moves focus out to "Check my work".
    await page.keyboard.press('Control+m');
    await expect(check).toBeFocused();

    // Tab continues forward from there, so keyboard navigation is not stuck.
    await page.keyboard.press('Tab');
    await expect(check).not.toBeFocused();
    await expect(textarea).not.toBeFocused();
  });

  test(`focus is not trapped in the editor (/${locale})`, async ({ page }) => {
    await openLab(page, locale, 'lab-code');
    await page.getByRole('button', { name: TEXT[locale].start }).click();
    const check = page.getByRole('button', { name: TEXT[locale].check });
    await expect(check).toBeVisible();

    // Monaco's input textarea, once the self-hosted editor has loaded.
    const input = page.locator('[data-testid="code-editor-file-main.py"] .monaco-editor textarea');
    await input.waitFor({ state: 'attached', timeout: 30_000 });
    await input.focus();
    await expect(input).toBeFocused();

    // Typing goes to the editor; the exit chord moves focus out to "Check my work".
    await page.keyboard.type('x');
    await expect(input).toBeFocused();
    await page.keyboard.press('Control+m');
    await expect(check).toBeFocused();

    await page.keyboard.press('Tab');
    await expect(check).not.toBeFocused();
    await expect(input).not.toBeFocused();

    await expectNoAxeViolations(page);
  });
}
