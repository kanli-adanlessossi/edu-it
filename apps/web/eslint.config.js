// ESLint flat config for apps/web (TypeScript).
//
// Provides the `lint` command for the monorepo's uniform task runner. A lint
// violation makes `eslint .` exit non-zero and print the offending file path
// with the rule id and line/column; a clean tree exits zero (Req 1.1, 1.4, 1.7).
import js from '@eslint/js';
import tseslint from 'typescript-eslint';
import globals from 'globals';
import { eduItPlugin } from './eslint-rules/no-jsx-literal.js';

export default tseslint.config(
  {
    // src/generated holds the auto-generated OpenAPI client (task 22); it is a
    // build artifact regenerated on every build and is not hand-maintained.
    ignores: [
      'node_modules/**',
      '.next/**',
      'dist/**',
      'coverage/**',
      'src/generated/**',
      'openapi/**',
    ],
  },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ['**/*.{ts,tsx}'],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: 'module',
      globals: {
        ...globals.node,
        ...globals.browser,
      },
    },
  },
  {
    // The custom JSX-string-literal rule (Req 15.7). It fires on `.tsx` files
    // (the only files that contain JSX) and reports, with file path and line,
    // any user-facing string literal placed directly in JSX instead of a
    // next-intl translation key.
    files: ['**/*.tsx'],
    plugins: {
      'edu-it': eduItPlugin,
    },
    rules: {
      'edu-it/no-jsx-literal': 'error',
    },
  },
  {
    // Node build scripts (task 22 client generation) and the custom ESLint
    // rule module. They run under Node with ES modules and use only Node
    // built-ins and JSDoc types.
    files: ['scripts/**/*.mjs', 'eslint-rules/**/*.js'],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: 'module',
      globals: {
        ...globals.node,
      },
    },
  },
);
