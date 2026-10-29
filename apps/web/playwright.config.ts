import { defineConfig } from '@playwright/test';

// Playwright + axe-core accessibility run over every learner page under /fr and
// /en (task 24.2, Req 30.9, 19.1-19.5). The web app is served in dev mode and
// pointed at a deterministic mock API so no database is needed.
const WEB_PORT = 3100;
const API_PORT = 4010;

export default defineConfig({
  testDir: './e2e',
  testMatch: '**/*.spec.ts',
  timeout: 120_000,
  reporter: 'list',
  use: {
    baseURL: `http://localhost:${WEB_PORT}`,
    browserName: 'chromium',
  },
  webServer: [
    {
      command: `node e2e/mock-api.mjs ${API_PORT}`,
      url: `http://localhost:${API_PORT}/v1/auth/session`,
      reuseExistingServer: true,
    },
    {
      command: `npx next dev --webpack -p ${WEB_PORT}`,
      url: `http://localhost:${WEB_PORT}/en`,
      reuseExistingServer: true,
      timeout: 180_000,
      env: { NEXT_PUBLIC_API_BASE_URL: `http://localhost:${API_PORT}/v1` },
    },
  ],
});
