// Real-stack Seed_Lab test (api-route-wiring task 12.2; Req 17.3, 17.4, 17.5).
//
// A learner signs up through the web app, opens the verification link read from
// E2E_MAIL_LOG, signs in, then passes the Checks of BOTH Seed_Labs and sees the
// translated feedback in /fr and in /en. No mock API is involved: the web app talks to
// the real API, the real worker grades, and the sandbox is a real Docker container.
//
// Run with `npm run test:e2e:real` once the stack is up (`tools/e2e_stack.py up`; see
// playwright.e2e.config.ts). The default `npm run test:e2e` never runs this file.
// Optional: E2E_LAB_TERMINAL_ID, E2E_LAB_CODE_ID.
//
// Expected feedback strings are read from the authored content and the message
// catalogs, so the test asserts the translation actually shown, per locale.
import { readFileSync } from 'node:fs';
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';

import { expect, test, type Page } from '@playwright/test';

type Locale = 'fr' | 'en';
const LOCALES: readonly Locale[] = ['fr', 'en'];

const TERMINAL_LAB = process.env.E2E_LAB_TERMINAL_ID ?? 'lab.fix-the-shared-folder';
const CODE_LAB = process.env.E2E_LAB_CODE_ID ?? 'lab.world-writable-entries';
const MAIL_LOG = process.env.E2E_MAIL_LOG;

const WEB_ROOT = process.cwd();
const CONTENT = resolve(WEB_ROOT, '../../content/linux-fundamentals');

interface CheckDef {
  stable_id: string;
  outcomes: Array<{ code: string; message: Record<Locale, string> }>;
}
interface LabMessages {
  checkWork: string;
  startLab: string;
  session: { endAndStart: string };
  results: { passed: string };
}

function readJson<T>(path: string): T {
  return JSON.parse(readFileSync(path, 'utf8')) as T;
}

const CHECKS = readJson<CheckDef[]>(resolve(CONTENT, 'lab_checks.json'));
const MESSAGES = Object.fromEntries(
  LOCALES.map((l) => [
    l,
    readJson<{ Labs: LabMessages }>(resolve(WEB_ROOT, `src/messages/${l}.json`)).Labs,
  ]),
) as Record<Locale, LabMessages>;

/** The translated "pass" message of every Check whose id starts with `prefix`. */
function passMessages(prefix: string, locale: Locale): string[] {
  return CHECKS.filter((c) => c.stable_id.startsWith(prefix)).map((c) => {
    const pass = c.outcomes.find((o) => o.code === 'pass');
    if (!pass) throw new Error(`no pass outcome for ${c.stable_id}`);
    return pass.message[locale];
  });
}

const SOLUTION = [
  'import sys',
  '',
  'def main():',
  '    for line in sys.stdin:',
  '        line = line.rstrip("\\n")',
  '        if not line or line.startswith("total "):',
  '            continue',
  '        parts = line.split(None, 8)',
  '        if len(parts) < 9:',
  '            continue',
  '        mode, name = parts[0], parts[8]',
  '        if mode[0] != "l" and len(mode) >= 10 and mode[8] == "w":',
  '            print(name)',
  '',
  'if __name__ == "__main__":',
  '    main()',
  '',
].join('\n');

/** Polls the captured console-mailer output for the verification link sent to `email`. */
async function verificationLink(email: string): Promise<string> {
  expect(MAIL_LOG, 'E2E_MAIL_LOG must name the file the API console mailer writes to').toBeTruthy();
  let link = '';
  await expect
    .poll(
      async () => {
        const log = await readFile(MAIL_LOG!, 'utf8').catch(() => '');
        const mails = log.split(/\r?\n(?=.*\bTo:|.*"to")/i).filter((m) => m.includes(email));
        const urls = mails.flatMap((m) => m.match(/https?:\/\/[^\s"'<>]*verif[^\s"'<>]*/gi) ?? []);
        link = urls.at(-1) ?? '';
        return link;
      },
      { message: 'verification link in the mail log', timeout: 30_000 },
    )
    .not.toBe('');
  return link;
}

/** Signs up through the web app, verifies by the emailed link, and signs in if needed. */
async function signUpAndSignIn(page: Page): Promise<void> {
  const email = `seed-labs-${Date.now()}@example.test`;
  const password = 'Correct-Horse-Battery-9';

  await page.goto('/en/sign-up');
  await page.getByLabel('Email address').fill(email);
  await page.getByLabel('Password', { exact: true }).fill(password);
  await page.getByRole('button', { name: 'Sign up', exact: true }).click();
  await page.waitForURL(/\/en\/(verify-email|dashboard)/);
  if (page.url().includes('verify-email')) {
    await page.goto(await verificationLink(email));
  }
  if (!page.url().includes('/dashboard')) {
    await page.goto('/en/sign-in');
    await page.getByLabel('Email address').fill(email);
    await page.getByLabel('Password', { exact: true }).fill(password);
    await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  }
  await page.waitForURL(/\/en\/dashboard/);
}

async function openLab(page: Page, locale: Locale, labId: string): Promise<void> {
  await page.goto(`/${locale}/labs/${encodeURIComponent(labId)}`);
  await expect(page.locator('html')).toHaveAttribute('lang', locale);
  const check = page.getByRole('button', { name: MESSAGES[locale].checkWork });
  const start = page.getByRole('button', { name: MESSAGES[locale].startLab });
  const failure = page.locator('main [role="alert"]').filter({ hasText: /\S/ });
  // Bounded wait for the page to settle on one of three states, so an API error
  // (for example a 401 on GET /labs/{id}) fails fast with a readable message.
  const settled = await Promise.race([
    check.waitFor({ timeout: 30_000 }).then(() => 'check' as const),
    start.waitFor({ timeout: 30_000 }).then(() => 'start' as const),
    failure
      .first()
      .waitFor({ timeout: 30_000 })
      .then(() => 'error' as const),
  ]).catch(() => 'timeout' as const);
  if (settled === 'error') {
    const text = (await failure.first().textContent())?.trim();
    throw new Error(
      `Lab page /${locale}/labs/${labId} showed an error instead of the lab: "${text}". ` +
        'Check that the API accepted the session cookie on GET /v1/labs/{id} and that content is synced.',
    );
  }
  if (settled === 'timeout') {
    throw new Error(
      `Lab page /${locale}/labs/${labId} showed neither "${MESSAGES[locale].startLab}" nor ` +
        `"${MESSAGES[locale].checkWork}" within 30 s. Check the web app, the API, and the lab content.`,
    );
  }
  if (settled === 'start') {
    // No Active_Session yet: start one. An existing one is reused.
    await start.click();
    // One Active_Session per learner (hands-on-labs Req 7.1): the other Seed_Lab's session
    // is still running, so the API answers 409 and the page offers to end it (Req 30.1).
    // Use the product's own button; the rule itself is not bypassed.
    const endAndStart = page.getByRole('button', { name: MESSAGES[locale].session.endAndStart });
    const next = await Promise.race([
      check.waitFor({ timeout: 90_000 }).then(() => 'check' as const),
      endAndStart.waitFor({ timeout: 90_000 }).then(() => 'conflict' as const),
    ]);
    if (next === 'conflict') await endAndStart.click();
  }
  await expect(check).toBeVisible({ timeout: 90_000 });
  await expect(check).toHaveAttribute('aria-disabled', 'false', { timeout: 90_000 });
}

/** Runs "Check my work" and waits until every expected pass message is on screen. */
async function checkAndExpectPass(page: Page, locale: Locale, expected: string[]): Promise<void> {
  const check = page.getByRole('button', { name: MESSAGES[locale].checkWork });
  await expect(check).toHaveAttribute('aria-disabled', 'false', { timeout: 30_000 });
  await check.click();
  const items = page.getByTestId('lab-results').getByRole('listitem');
  await expect(items).toHaveCount(expected.length, { timeout: 120_000 });
  // Checks of one lab may share a pass message, so count each distinct message by its
  // number of occurrences in `expected`.
  const occurrences = new Map<string, number>();
  for (const message of expected) occurrences.set(message, (occurrences.get(message) ?? 0) + 1);
  for (const [message, count] of occurrences) {
    await expect(items.filter({ hasText: message })).toHaveCount(count);
  }
  await expect(items.filter({ hasText: MESSAGES[locale].results.passed })).toHaveCount(
    expected.length,
  );
}

async function runInTerminal(page: Page, command: string): Promise<void> {
  const terminal = page.getByTestId('terminal-container');
  // xterm pauses rendering while the terminal is off-screen, so bring it into view first.
  await terminal.scrollIntoViewIfNeeded();
  await expect(page.locator('[data-testid="terminal-container"] .xterm-rows')).toContainText(/\S/, {
    timeout: 60_000,
  });
  await terminal.click();
  await page.keyboard.type(command, { delay: 15 });
  await page.keyboard.press('Enter');
}

async function solveCodeLab(page: Page): Promise<void> {
  await expect(page.getByTestId('code-editor')).toBeVisible();
  await page.waitForFunction(
    () =>
      ((
        window as unknown as { monaco?: { editor: { getEditors(): unknown[] } } }
      ).monaco?.editor.getEditors().length ?? 0) > 0,
    undefined,
    { timeout: 60_000 },
  );
  await page.evaluate((code) => {
    const monaco = (
      window as unknown as {
        monaco: { editor: { getEditors(): { setValue(v: string): void }[] } };
      }
    ).monaco;
    monaco.editor.getEditors()[0]?.setValue(code);
  }, SOLUTION);
}

// Each test is bounded (5 min: provisioning up to 90 s + grading up to 120 s); the
// sign-up hook is bounded separately so a stuck stack fails instead of hanging.
test.describe.configure({ mode: 'serial', timeout: 5 * 60_000 });

test.describe('Seed_Labs on the real stack (Req 17.3, 17.4, 17.5)', () => {
  let page: Page;

  test.beforeAll(async ({ browser }) => {
    test.setTimeout(2 * 60_000);
    page = await browser.newPage();
    await signUpAndSignIn(page).catch((error: unknown) => {
      throw new Error(
        `Seed_Lab setup failed (sign-up, mail-log verification link, or sign-in): ${
          error instanceof Error ? error.message : String(error)
        }`,
      );
    });
  });

  test.afterAll(async () => {
    await page.close();
  });

  for (const locale of LOCALES) {
    test(`terminal lab "Fix the shared folder" passes with ${locale} feedback`, async () => {
      await openLab(page, locale, TERMINAL_LAB);
      await runInTerminal(
        page,
        'cd /workspace/team && mkdir -p docs scripts && chgrp devs . && chmod g+s . && chmod o-w shared-notes.txt',
      );
      await checkAndExpectPass(
        page,
        locale,
        passMessages('lab-check.fix-the-shared-folder.', locale),
      );
    });

    test(`code lab "World-writable entries" passes with ${locale} feedback`, async () => {
      await openLab(page, locale, CODE_LAB);
      await solveCodeLab(page);
      await checkAndExpectPass(
        page,
        locale,
        passMessages('lab-check.world-writable-entries.', locale),
      );
    });
  }
});
