// Playwright + axe-core scan of every learner page under /fr and /en
// (task 24.2; Req 30.9, 19.1-19.5). Unlike the jsdom scan, this runs in a real
// browser, so rules such as color-contrast are evaluated too.
import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';

const SESSION_COOKIE = 'edu_it_session';

const PAGES: ReadonlyArray<{ name: string; path: string; heading: RegExp | string }> = [
  { name: 'dashboard', path: 'dashboard', heading: 'h1' },
  { name: 'track', path: 'tracks/track-1', heading: 'h1' },
  { name: 'module', path: 'tracks/track-1/modules/module-1', heading: 'h1' },
  {
    name: 'lesson reader',
    path: 'tracks/track-1/modules/module-1/lessons/lesson-1',
    heading: 'h1',
  },
  { name: 'quiz player', path: 'quizzes/quiz-1', heading: 'h1' },
  { name: 'review session', path: 'reviews/concept-1', heading: 'h1' },
  { name: 'project submission', path: 'projects/project-1', heading: 'h1' },
];

test.beforeEach(async ({ context }) => {
  await context.addCookies([
    { name: SESSION_COOKIE, value: 'e2e-session', url: 'http://localhost:3100' },
  ]);
});

for (const locale of ['fr', 'en'] as const) {
  for (const page of PAGES) {
    test(`${page.name} (/${locale}) has no accessibility violations`, async ({ page: pw }) => {
      const response = await pw.goto(`/${locale}/${page.path}`);
      expect(response?.status()).toBe(200);
      await expect(pw.locator('main h1').first()).toBeVisible();
      await expect(pw.locator('html')).toHaveAttribute('lang', locale);

      const results = await new AxeBuilder({ page: pw })
        .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'best-practice'])
        .analyze();

      const summary = results.violations.map(
        (v) =>
          `[${v.impact}] ${v.id}: ${v.help}\n` + v.nodes.map((n) => `    ${n.html}`).join('\n'),
      );
      expect(summary, summary.join('\n')).toEqual([]);
    });
  }
}
