import { defineConfig } from '@playwright/test';

// Real-stack end-to-end journeys (hands-on-labs tasks 21.1, 21.2; Req 32.1-32.3).
//
// Unlike playwright.config.ts (deterministic mock API, accessibility only), this
// config starts nothing: it targets an already running real stack made of the
// web app, the API, the worker, Redis, PostgreSQL, and the local Docker daemon
// with the Docker_Provider (development only, never production):
//
//   SANDBOX_PROVIDER=docker  EDU_IT_ENV=development  MAILER_BACKEND=console
//   (api stdout/stderr tee'd to the file named by E2E_MAIL_LOG)
//   lab images built (`cd lab-images && docker buildx bake --load`)
//   seed content synced (`edu-it-content sync`)
//
// Environment: E2E_WEB_URL (default http://localhost:3000), E2E_MAIL_LOG (required
// by the journey), and optional E2E_TRACK_ID, E2E_LAB_ID, E2E_QUIZ_ID, E2E_PROJECT_ID.
export default defineConfig({
  testDir: './e2e/real',
  testMatch: '**/*.e2e.ts',
  timeout: 10 * 60_000,
  workers: 1,
  reporter: 'list',
  // Bound every step: without these, a click or navigation waits for the whole test
  // timeout, which hid the lab-route 401 as a ten-minute hang (api-route-wiring 13.3).
  expect: { timeout: 15_000 },
  use: {
    baseURL: process.env.E2E_WEB_URL ?? 'http://localhost:3000',
    browserName: 'chromium',
    actionTimeout: 30_000,
    navigationTimeout: 60_000,
  },
});
