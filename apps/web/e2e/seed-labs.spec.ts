// Playwright end-to-end tests for both Seed_Labs and refresh recovery
// (hands-on-labs task 21.2; Req 13.3, 13.6, 32.2, 32.3).
//
// These run against the REAL stack (Docker_Provider, worker, API, web app), never
// the mock API. They are skipped unless `E2E_REAL_STACK=1`. Configuration:
//   E2E_WEB_URL            web origin (default http://localhost:3000)
//   E2E_LEARNER_EMAIL      an existing, verified learner (default e2e-learner@example.test)
//   E2E_LEARNER_PASSWORD   that learner's password
//   E2E_LAB_TERMINAL_ID    id of "Fix the shared folder"  (default lab.fix-the-shared-folder)
//   E2E_LAB_CODE_ID        id of "World-writable entries" (default lab.world-writable-entries)
// The page and API share cookies, so the web app must be built with
// NEXT_PUBLIC_API_BASE_URL pointing at the real API. Everything is self-contained in
// this file so it does not depend on the journey test (task 21.1).
//
// Expected feedback strings are read from the authored content and message
// catalogs, so the tests assert the translation actually shown, per locale.
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { expect, test, type Locator, type Page } from '@playwright/test';

type Locale = 'fr' | 'en';
const LOCALES: readonly Locale[] = ['fr', 'en'];

const WEB_URL = process.env.E2E_WEB_URL ?? 'http://localhost:3000';
const EMAIL = process.env.E2E_LEARNER_EMAIL ?? 'e2e-learner@example.test';
const PASSWORD = process.env.E2E_LEARNER_PASSWORD ?? '';
const TERMINAL_LAB = process.env.E2E_LAB_TERMINAL_ID ?? 'lab.fix-the-shared-folder';
const CODE_LAB = process.env.E2E_LAB_CODE_ID ?? 'lab.world-writable-entries';

const WEB_ROOT = resolve(fileURLToPath(new URL('.', import.meta.url)), '..');
const CONTENT = resolve(WEB_ROOT, '../../content/linux-fundamentals');

interface CheckOutcome {
  code: string;
  message: Record<Locale, string>;
}
interface CheckDef {
  stable_id: string;
  outcomes: CheckOutcome[];
}

function readJson<T>(path: string): T {
  return JSON.parse(readFileSync(path, 'utf8')) as T;
}

const CHECKS = readJson<CheckDef[]>(resolve(CONTENT, 'lab_checks.json'));
const MESSAGES = Object.fromEntries(
  LOCALES.map((l) => [
    l,
    readJson<{
      Labs: { checkWork: string; resetLab: string; startLab: string; results: { passed: string } };
    }>(resolve(WEB_ROOT, `src/messages/${l}.json`)).Labs,
  ]),
) as Record<
  Locale,
  { checkWork: string; resetLab: string; startLab: string; results: { passed: string } }
>;

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

test.skip(process.env.E2E_REAL_STACK !== '1', 'needs the real stack (set E2E_REAL_STACK=1)');
test.use({ baseURL: WEB_URL });
test.describe.configure({ mode: 'serial', timeout: 240_000 });

async function signIn(page: Page): Promise<void> {
  await page.goto('/en/sign-in');
  await page.getByLabel('Email address').fill(EMAIL);
  await page.getByLabel('Password', { exact: true }).fill(PASSWORD);
  await page.getByRole('button', { name: 'Sign in' }).click();
  await expect(page).not.toHaveURL(/sign-in/);
}

async function openLab(page: Page, locale: Locale, labId: string): Promise<void> {
  await page.goto(`/${locale}/labs/${encodeURIComponent(labId)}`);
  await expect(page.locator('html')).toHaveAttribute('lang', locale);
  const check = page.getByRole('button', { name: MESSAGES[locale].checkWork });
  if (!(await check.isVisible())) {
    // No Active_Session yet: start one. An existing one is reused (Req 13.3).
    await page.getByRole('button', { name: MESSAGES[locale].startLab }).click();
  }
  await expect(check).toBeVisible({ timeout: 90_000 });
  await expect(check).toHaveAttribute('aria-disabled', 'false', { timeout: 90_000 });
}

/** Runs "Check my work" and waits until every expected pass message is on screen. */
async function checkAndExpectPass(page: Page, locale: Locale, expected: string[]): Promise<void> {
  const check = page.getByRole('button', { name: MESSAGES[locale].checkWork });
  await expect(check).toHaveAttribute('aria-disabled', 'false', { timeout: 30_000 });
  await check.click();
  const results = page.getByTestId('lab-results');
  const items = results.getByRole('listitem');
  await expect(items).toHaveCount(expected.length, { timeout: 120_000 });
  for (const message of expected) {
    await expect(items.filter({ hasText: message })).toHaveCount(1);
  }
  await expect(items.filter({ hasText: MESSAGES[locale].results.passed })).toHaveCount(
    expected.length,
  );
}

function terminalText(page: Page): Locator {
  return page.getByTestId('terminal-container');
}

async function runInTerminal(page: Page, command: string): Promise<void> {
  await terminalText(page).click();
  await page.keyboard.type(command, { delay: 15 });
  await page.keyboard.press('Enter');
}

/** Waits for the shell prompt: the terminal has connected and printed something. */
async function waitForShell(page: Page): Promise<void> {
  await expect(page.locator('[data-testid="terminal-container"] .xterm-rows')).toContainText(/\S/, {
    timeout: 60_000,
  });
}

test.describe('Seed_Labs (Req 32.2, 32.3)', () => {
  test.beforeEach(async ({ page }) => {
    await signIn(page);
  });

  for (const locale of LOCALES) {
    test(`terminal lab "Fix the shared folder" passes with ${locale} feedback`, async ({
      page,
    }) => {
      await openLab(page, locale, TERMINAL_LAB);
      await waitForShell(page);
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

    test(`code lab "World-writable entries" passes with ${locale} feedback`, async ({ page }) => {
      await openLab(page, locale, CODE_LAB);
      const editors = page.getByTestId('code-editor');
      await expect(editors).toBeVisible();
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
      await checkAndExpectPass(
        page,
        locale,
        passMessages('lab-check.world-writable-entries.', locale),
      );
    });
  }
});

test.describe('Refresh recovery (Req 13.3, 13.6)', () => {
  test('a file created before a refresh is found in the same Sandbox', async ({ page }) => {
    await signIn(page);
    const name = `refresh-${Date.now()}.txt`;
    await openLab(page, 'en', TERMINAL_LAB);
    await waitForShell(page);

    // Output markers are computed by the shell, so the typed command line (which holds
    // `$(...)`) can never satisfy the match: only real output does.
    await runInTerminal(page, `echo "created" > /tmp/${name} && echo "HOST_$(hostname)_END"`);
    await expect(terminalText(page)).toContainText(/HOST_[0-9A-Za-z.-]+_END/, { timeout: 30_000 });
    const hostBefore = (await terminalText(page).innerText()).match(
      /HOST_([0-9A-Za-z.-]+)_END/,
    )![1];

    await page.reload();
    await expect(page.locator('html')).toHaveAttribute('lang', 'en');
    await waitForShell(page);

    await runInTerminal(
      page,
      `test -f /tmp/${name} && echo "FOUND_$((6*7))" ; echo "HOST_$(hostname)_END"`,
    );
    await expect(terminalText(page)).toContainText('FOUND_42', { timeout: 30_000 });
    const hosts = [
      ...(await terminalText(page).innerText()).matchAll(/HOST_([0-9A-Za-z.-]+)_END/g),
    ].map((m) => m[1]);
    expect(hosts.at(-1)).toBe(hostBefore);
  });
});
