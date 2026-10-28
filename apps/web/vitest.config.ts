import { defineConfig } from 'vitest/config';

// Vitest configuration providing the `test` command for the task runner.
// A failing test (or property-based counterexample) makes `vitest run` exit
// non-zero and name the failing test and location; an all-green run exits zero
// (Req 1.1, 1.6, 1.7).
export default defineConfig({
  test: {
    // src holds the app's TypeScript tests (including the `.tsx` accessibility
    // scan, task 24.4); eslint-rules holds the custom lint rule and its
    // RuleTester fixtures (task 23.6), which also run under vitest.
    include: ['src/**/*.test.ts', 'src/**/*.test.tsx', 'eslint-rules/**/*.test.ts'],
    environment: 'node',
  },
});
