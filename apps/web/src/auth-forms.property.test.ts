import fc from 'fast-check';
import { describe, expect, it } from 'vitest';

import {
  AuthSubmitError,
  isPasswordField,
  submitAuthForm,
  type AuthApi,
  type AuthFormFields,
  type AuthPage,
  AUTH_PAGES,
} from './auth-forms.js';
import { LOCALES, type Locale } from './locale.js';

// Property-based test for auth-page submission failure field preservation
// (task 24.2).
//
// Feature: foundations, Property 24: Auth-page submission failure preserves non-password fields
//
// Property 24 (design.md): *For any* auth-page submission that fails or returns
// an error, the page is preserved with each non-password field equal to what
// the User entered, every password field cleared, and an error indication
// shown.
//
// Validates: Requirements 17.3

/** A field name that is never a password field, so its value must be preserved. */
const nonPasswordName = fc
  .string({ minLength: 1, maxLength: 12 })
  .map((raw) => raw.replace(/[^a-zA-Z0-9_]/g, 'x'))
  .filter((name) => name.length > 0 && !isPasswordField(name));

/** A password field name whose value must be cleared on failure. */
const passwordName = fc.constantFrom(
  'password',
  'newPassword',
  'currentPassword',
  'confirmPassword',
  'passwordConfirmation',
);

const fieldValue = fc.string({ minLength: 0, maxLength: 20 });

/**
 * An arbitrary set of submitted form fields containing at least one password
 * field (so the clearing behavior is always exercised) and zero or more
 * non-password fields with distinct names.
 */
const submittedFields: fc.Arbitrary<{
  fields: AuthFormFields;
  nonPassword: Record<string, string>;
  passwords: string[];
}> = fc
  .record({
    nonPasswordPairs: fc.uniqueArray(fc.tuple(nonPasswordName, fieldValue), {
      minLength: 0,
      maxLength: 5,
      selector: ([name]) => name,
    }),
    passwordPairs: fc.uniqueArray(fc.tuple(passwordName, fieldValue), {
      minLength: 1,
      maxLength: 3,
      selector: ([name]) => name,
    }),
  })
  .map(({ nonPasswordPairs, passwordPairs }) => {
    const nonPassword = Object.fromEntries(nonPasswordPairs);
    const fields: Record<string, string> = { ...nonPassword };
    for (const [name, value] of passwordPairs) {
      fields[name] = value;
    }
    return {
      fields,
      nonPassword,
      passwords: passwordPairs.map(([name]) => name),
    };
  });

const authPage: fc.Arbitrary<AuthPage> = fc.constantFrom(...AUTH_PAGES);
const localeArb: fc.Arbitrary<Locale> = fc.constantFrom(...LOCALES);

/** An API port whose submit always fails, modeling any failed/errored submission. */
function failingApi(error: unknown): AuthApi {
  return {
    submit() {
      return Promise.reject(error);
    },
  };
}

describe('Feature: foundations, Property 24: Auth-page submission failure preserves non-password fields', () => {
  it('preserves non-password fields, clears passwords, and shows an error on any failure', async () => {
    await fc.assert(
      fc.asyncProperty(
        submittedFields,
        authPage,
        localeArb,
        // The failure can be a translated AuthSubmitError or an arbitrary throw
        // (network/unexpected), so the property holds regardless of error shape.
        fc.oneof(
          fc.string({ minLength: 1, maxLength: 30 }).map((m) => ({ kind: 'auth' as const, m })),
          fc.constant({ kind: 'raw' as const, m: '' }),
        ),
        async ({ fields, nonPassword, passwords }, page, locale, err) => {
          const cause =
            err.kind === 'auth' ? new AuthSubmitError(err.m, locale) : new Error('network');
          const fallback = 'Fallback error';

          const result = await submitAuthForm(failingApi(cause), page, fields, fallback);

          // The page is preserved (same page) and reports an error.
          expect(result.status).toBe('error');
          if (result.status !== 'error') {
            return;
          }
          expect(result.page).toBe(page);

          // Every non-password field keeps exactly what the User entered.
          for (const [name, value] of Object.entries(nonPassword)) {
            expect(result.fields[name]).toBe(value);
          }

          // Every password field is cleared (present as a key, empty value).
          for (const name of passwords) {
            expect(result.fields[name]).toBe('');
          }

          // An error indication is shown: the translated detail when present,
          // otherwise the fallback — never empty.
          const expected = err.kind === 'auth' && err.m.length > 0 ? err.m : fallback;
          expect(result.error).toBe(expected);
          expect(result.error.length).toBeGreaterThan(0);
        },
      ),
      { numRuns: 200 },
    );
  });

  it('reports success without a field snapshot when the submission is accepted', async () => {
    await fc.assert(
      fc.asyncProperty(submittedFields, authPage, async ({ fields }, page) => {
        const api: AuthApi = { submit: () => Promise.resolve({ redirectTo: '/x' }) };
        const result = await submitAuthForm(api, page, fields, 'Fallback error');
        expect(result.status).toBe('success');
        if (result.status === 'success') {
          expect(result.redirectTo).toBe('/x');
        }
      }),
      { numRuns: 100 },
    );
  });
});
