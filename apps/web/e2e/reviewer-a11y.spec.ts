// Playwright + axe-core scan of the reviewer queue and task detail under /fr and
// /en (ai-content task 16.2; Req 15.9). Runs in a real browser, so color-contrast is evaluated.
//
// Validates: Requirements 15.9
import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';

const SESSION_COOKIE = 'edu_it_session';

const PAGES: ReadonlyArray<{ name: string; path: string }> = [
  { name: 'reviewer queue', path: 'reviewer' },
  { name: 'reviewer task', path: 'reviewer/rt-1' },
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

  test(`task detail (/${locale}) shows both languages and its actions`, async ({ page: pw }) => {
    await pw.goto(`/${locale}/reviewer/rt-1`);
    await expect(pw.locator('main section[lang="fr"]')).toBeVisible();
    await expect(pw.locator('main section[lang="en"]')).toBeVisible();
    await expect(pw.locator('#review-comment')).toBeVisible();
    await expect(pw.locator('#reviewer-task-title ~ div button')).toHaveCount(4);
  });

  test(`queue (/${locale}) links each task to its detail page`, async ({ page: pw }) => {
    await pw.goto(`/${locale}/reviewer`);
    const links = pw.locator(`main ul a[href^="/${locale}/reviewer/"]`);
    await expect(links).toHaveCount(2);
    await expect(pw.locator('#reviewer-queue-title ~ button')).toHaveCount(1);
  });
}
