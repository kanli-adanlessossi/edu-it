// End-to-end learner journey against the REAL stack (task 21.1; Req 32.1, 32.3).
//
// Sign up, verify the email, sign in, enroll, pass the quiz, open the lab, run a
// command in the terminal, pass every Check, submit the project, and observe
// module "Processes" unlock. No mock API is involved: the web app talks to the
// real API, the real worker consumes `lab.provision` / `lab.grade`, and the
// sandbox is a real Docker container (SANDBOX_PROVIDER=docker, development only).
//
// Run with `npm run test:e2e:real` once the stack is up (see playwright.e2e.config.ts
// for the environment variables). The default `npm run test:e2e` never runs this file.
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { expect, test, type Page } from '@playwright/test';

const TRACK_ID = process.env.E2E_TRACK_ID ?? 'track.linux-fundamentals';
const LAB_ID = process.env.E2E_LAB_ID ?? 'lab.fix-the-shared-folder';
const QUIZ_ID = process.env.E2E_QUIZ_ID ?? 'quiz.reading-permissions';
const PROJECT_ID = process.env.E2E_PROJECT_ID ?? 'project.harden-shared-directory';
const MAIL_LOG = process.env.E2E_MAIL_LOG;
const CONTENT_ROOT = resolve(process.cwd(), '../../content/linux-fundamentals');

interface Localized {
  en: string;
}
interface QuestionRow {
  provenance: { stable_id: string };
  quiz_stable_id: string;
  prompt_text: Localized;
}
interface OptionRow {
  question_stable_id: string;
  is_correct: boolean;
  text: Localized;
}

function readContent<T>(file: string): T[] {
  return JSON.parse(readFileSync(resolve(CONTENT_ROOT, file), 'utf8')) as T[];
}

/** The prompt and correct-answer text of each question of the quiz, from the seed content. */
function correctAnswers(quizId: string): Array<{ prompt: string; answer: string }> {
  const options = readContent<OptionRow>('question_options.json');
  return readContent<QuestionRow>('questions.json')
    .filter((q) => q.quiz_stable_id === quizId)
    .map((q) => {
      const correct = options.filter(
        (o) => o.question_stable_id === q.provenance.stable_id && o.is_correct,
      );
      expect(correct, `one correct option for ${q.provenance.stable_id}`).toHaveLength(1);
      return { prompt: q.prompt_text.en, answer: correct[0]!.text.en };
    });
}

/** Polls the captured console-mailer output for the verification link sent to `email`. */
async function verificationLink(email: string): Promise<string> {
  expect(MAIL_LOG, 'E2E_MAIL_LOG must name the file the API console mailer writes to').toBeTruthy();
  const { readFile } = await import('node:fs/promises');
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

async function typeInTerminal(page: Page, command: string) {
  const textarea = page.locator('[data-testid="terminal-container"] textarea');
  await textarea.focus();
  await page.keyboard.type(command);
  await page.keyboard.press('Enter');
}

test('learner journey: sign up to Processes unlocked', async ({ page }) => {
  test.setTimeout(10 * 60_000);
  const email = `journey-${Date.now()}@example.test`;
  const password = 'Correct-Horse-Battery-9';

  // Sign up, then open the emailed verification link.
  await page.goto('/en/sign-up');
  await page.getByLabel('Email address').fill(email);
  await page.getByLabel('Password', { exact: true }).fill(password);
  await page.getByRole('button', { name: 'Sign up', exact: true }).click();
  await page.waitForURL(/\/en\/(verify-email|dashboard)/);
  if (page.url().includes('verify-email')) {
    await page.goto(await verificationLink(email));
  }

  // Sign in (a no-op redirect when the verification already opened a session).
  if (!page.url().includes('/dashboard')) {
    await page.goto('/en/sign-in');
    await page.getByLabel('Email address').fill(email);
    await page.getByLabel('Password', { exact: true }).fill(password);
    await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  }
  await page.waitForURL(/\/en\/dashboard/);

  // Enroll; "Processes" starts locked.
  await page.getByLabel('Track identifier').fill(TRACK_ID);
  await page.getByRole('button', { name: 'Enroll', exact: true }).click();
  await page.goto(`/en/tracks/${encodeURIComponent(TRACK_ID)}`);
  const processes = page.locator('li[data-lock-state]').filter({ hasText: /processes/i });
  await expect(processes).toHaveAttribute('data-lock-state', 'locked');

  // Pass the quiz (every correct answer, so the concept reaches Practiced).
  await page.goto(`/en/quizzes/${encodeURIComponent(QUIZ_ID)}`);
  for (const { prompt, answer } of correctAnswers(QUIZ_ID)) {
    const question = page
      .locator('fieldset')
      .filter({ has: page.locator('legend', { hasText: prompt }) });
    await question.getByLabel(answer, { exact: true }).check();
  }
  await page.getByRole('button', { name: 'Submit answers' }).click();
  await expect(page.getByRole('status').filter({ hasText: 'Your result' })).toBeVisible();

  // Open the lab on a real Docker sandbox and use the terminal.
  await page.goto(`/en/labs/${encodeURIComponent(LAB_ID)}`);
  await page.getByRole('button', { name: 'Start lab' }).click();
  const check = page.getByRole('button', { name: 'Check my work' });
  await expect(check).toBeVisible({ timeout: 60_000 });
  await expect(page.getByTestId('terminal-connection')).not.toHaveText('', { timeout: 60_000 });

  await typeInTerminal(page, 'echo e2e-marker-$((6*7))');
  await expect(page.getByTestId('terminal-container')).toContainText('e2e-marker-42', {
    timeout: 30_000,
  });

  // Solve the lab: sub-folders, group with setgid, no world-write on the planted file.
  await typeInTerminal(page, 'mkdir /workspace/team/docs /workspace/team/scripts');
  await typeInTerminal(page, 'chgrp devs /workspace/team && chmod g+s /workspace/team');
  await typeInTerminal(page, 'chmod o-w /workspace/team/shared-notes.txt');

  // Pass the Checks (the grading request is accepted at most once per 10 s).
  await check.click();
  const results = page.getByTestId('lab-results');
  await expect(results.getByText('All required tasks are complete.')).toBeVisible({
    timeout: 60_000,
  });
  await expect(results.locator('li')).toHaveCount(4);
  await expect(results.locator('li', { hasText: 'Passed' })).toHaveCount(4);

  // Submit the project: full marks on every criterion plus one evidence item.
  await page.goto(`/en/projects/${encodeURIComponent(PROJECT_ID)}`);
  const points = page.locator('input[type="number"]');
  await expect(points.first()).toBeVisible();
  for (const input of await points.all()) {
    await input.fill((await input.getAttribute('max')) ?? '0');
  }
  await page.getByLabel('Evidence item 1').fill('https://example.test/evidence/setup.sh');
  await page.getByRole('button', { name: 'Submit project' }).click();
  await expect(page.getByRole('status').filter({ hasText: 'Submitted' })).toBeVisible();

  // Both prerequisite concepts are Practiced: "Processes" unlocks (the worker may lag a little).
  await expect(async () => {
    await page.goto(`/en/tracks/${encodeURIComponent(TRACK_ID)}`);
    await expect(processes).toHaveAttribute('data-lock-state', 'unlocked', { timeout: 2_000 });
  }).toPass({ timeout: 60_000 });
});
