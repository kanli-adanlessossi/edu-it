// Playwright + axe-core scan of the Milestone 1 auth pages under /fr and /en
// (api-route-wiring Req 14.5, 14.6, 14.8, 14.11, 17.6), against the mock API,
// which serves the real route shapes (/auth/csrf, redirect_to, /auth/verify-email).
import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';

async function expectNoViolations(page: Page): Promise<void> {
  const results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'best-practice'])
    .analyze();
  const summary = results.violations.map(
    (v) => `[${v.impact}] ${v.id}: ${v.help}\n` + v.nodes.map((n) => `    ${n.html}`).join('\n'),
  );
  expect(summary, summary.join('\n')).toEqual([]);
}

for (const locale of ['fr', 'en'] as const) {
  test(`sign-up (/${locale}) has no violations and redirects to verify-email`, async ({ page }) => {
    await page.goto(`/${locale}/sign-up`);
    await expect(page.locator('main h1').first()).toBeVisible();
    await expect(page.locator('html')).toHaveAttribute('lang', locale);
    await expectNoViolations(page);

    // Retry until the client has hydrated (a dev-server cold start can submit natively).
    await expect(async () => {
      await page.locator('input[name="email"]').fill('learner@example.test');
      await page.locator('input[name="password"]').fill('correct horse battery staple');
      await page.locator('form button[type="submit"]').click();
      await expect(page).toHaveURL(new RegExp(`/${locale}/verify-email$`), { timeout: 3000 });
    }).toPass({ timeout: 30_000 });
  });

  test(`sign-in (/${locale}) has no violations and navigates to the locale dashboard`, async ({
    page,
  }) => {
    await page.goto(`/${locale}/sign-in`);
    await expect(page.locator('main h1').first()).toBeVisible();
    await expectNoViolations(page);

    await expect(async () => {
      await page.locator('input[name="email"]').fill('learner@example.test');
      await page.locator('input[name="password"]').fill('correct horse battery staple');
      await page.locator('form button[type="submit"]').click();
      await expect(page).toHaveURL(new RegExp(`/${locale}/dashboard`), { timeout: 3000 });
    }).toPass({ timeout: 30_000 });
  });

  test(`verify-email (/${locale}) resend state has no violations`, async ({ page }) => {
    await page.goto(`/${locale}/verify-email`);
    await expect(page.locator('main h1').first()).toBeVisible();
    await expectNoViolations(page);
  });

  test(`verify-email (/${locale}) success state has no violations`, async ({ page }) => {
    await page.goto(`/${locale}/verify-email?token=valid-token`);
    await expect(page.locator('main [role="status"]').first()).toBeVisible();
    await expect(page.locator('main form')).toHaveCount(0);
    await expectNoViolations(page);
  });

  test(`sign-in (/${locale}) shows the GitHub link to the authorize route`, async ({ page }) => {
    await page.goto(`/${locale}/sign-in`);
    await expect(page.locator('main a[href$="/auth/github/authorize"]')).toHaveCount(1);
  });

  test(`reset-password (/${locale}) request state has no violations`, async ({ page }) => {
    await page.goto(`/${locale}/reset-password`);
    await expect(page.locator('main h1').first()).toBeVisible();
    await expect(page.locator('input[name="email"]')).toHaveCount(1);
    await expectNoViolations(page);
  });

  test(`reset-password (/${locale}) new-password state signs in on success`, async ({ page }) => {
    await page.goto(`/${locale}/reset-password?token=valid-token`);
    await expect(page.locator('input[name="newPassword"]')).toBeVisible();
    await expectNoViolations(page);
    await expect(async () => {
      await page.locator('input[name="newPassword"]').fill('correct horse battery staple');
      await page.locator('form button[type="submit"]').click();
      await expect(page).toHaveURL(new RegExp(`/${locale}/dashboard`), { timeout: 3000 });
    }).toPass({ timeout: 30_000 });
  });

  test(`reset-password (/${locale}) invalid link state offers a new request`, async ({ page }) => {
    await page.goto(`/${locale}/reset-password?token=bad-token`);
    await expect(async () => {
      await page.locator('input[name="newPassword"]').fill('correct horse battery staple');
      await page.locator('form button[type="submit"]').click();
      await expect(page.locator('main [role="alert"]').first()).toBeVisible({ timeout: 3000 });
    }).toPass({ timeout: 30_000 });
    await expect(page.locator('main form')).toHaveCount(0);
    await expectNoViolations(page);
  });

  test(`auth/github/confirm (/${locale}) has no violations and confirm signs in`, async ({
    page,
  }) => {
    await page.goto(`/${locale}/auth/github/confirm`);
    await expect(page.locator('main h1').first()).toBeVisible();
    await expect(page.locator('main button')).toHaveCount(2);
    await expectNoViolations(page);
    await expect(async () => {
      await page.locator('main button').first().click();
      await expect(page).toHaveURL(new RegExp(`/${locale}/dashboard`), { timeout: 3000 });
    }).toPass({ timeout: 30_000 });
  });

  test(`auth/github/confirm (/${locale}) decline shows a neutral status`, async ({ page }) => {
    await page.goto(`/${locale}/auth/github/confirm`);
    await expect(async () => {
      await page.locator('main button').nth(1).click();
      await expect(page.locator('main [role="status"]')).toBeVisible({ timeout: 3000 });
    }).toPass({ timeout: 30_000 });
    await expectNoViolations(page);
  });

  test(`verify-email (/${locale}) invalid state shows the resend form`, async ({ page }) => {
    await page.goto(`/${locale}/verify-email?token=bad-token`);
    await expect(page.locator('main [role="alert"]').first()).toBeVisible();
    await expect(page.locator('main form')).toHaveCount(1);
    await expectNoViolations(page);
  });
}
