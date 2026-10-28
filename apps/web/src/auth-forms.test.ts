import { describe, expect, it } from 'vitest';

import {
  AUTH_PAGES,
  AuthSubmitError,
  isAuthPage,
  isPasswordField,
  preserveNonPasswordFields,
  submitAuthForm,
  type AuthApi,
} from './auth-forms.js';

// Targeted unit tests for the pure auth-form submission logic (task 24.1). They
// cover the specific examples and edge cases behind the field-preservation
// behavior on a failed submission (Req 17.2, 17.3).

describe('isAuthPage', () => {
  it('accepts the four foundational auth pages', () => {
    for (const page of AUTH_PAGES) {
      expect(isAuthPage(page)).toBe(true);
    }
    expect(AUTH_PAGES).toEqual(['sign-up', 'sign-in', 'verify-email', 'reset-password']);
  });

  it('rejects unknown page names', () => {
    expect(isAuthPage('dashboard')).toBe(false);
    expect(isAuthPage('')).toBe(false);
  });
});

describe('isPasswordField', () => {
  it('treats known secret field names as password fields', () => {
    expect(isPasswordField('password')).toBe(true);
    expect(isPasswordField('newPassword')).toBe(true);
    expect(isPasswordField('confirmPassword')).toBe(true);
  });

  it('treats non-secret field names as non-password', () => {
    expect(isPasswordField('email')).toBe(false);
    expect(isPasswordField('username')).toBe(false);
    expect(isPasswordField('Password')).toBe(false); // case-sensitive
  });
});

describe('preserveNonPasswordFields', () => {
  it('keeps non-password values and clears password values', () => {
    const preserved = preserveNonPasswordFields({
      email: 'learner@example.test',
      username: 'learner',
      password: 's3cret',
      newPassword: 'another',
    });
    expect(preserved).toEqual({
      email: 'learner@example.test',
      username: 'learner',
      password: '',
      newPassword: '',
    });
  });

  it('does not mutate the input object', () => {
    const input = { email: 'a@b.test', password: 'x' };
    const preserved = preserveNonPasswordFields(input);
    expect(input.password).toBe('x');
    expect(preserved.password).toBe('');
  });
});

describe('submitAuthForm', () => {
  const okApi: AuthApi = { submit: () => Promise.resolve({ redirectTo: '/fr/dashboard' }) };

  it('returns success with the redirect target when the API accepts', async () => {
    const result = await submitAuthForm(
      okApi,
      'sign-in',
      { email: 'a@b.test', password: 'pw' },
      'fallback',
    );
    expect(result).toEqual({ status: 'success', redirectTo: '/fr/dashboard' });
  });

  it('preserves the email, clears the password, and surfaces the translated detail', async () => {
    const api: AuthApi = {
      submit: () => Promise.reject(new AuthSubmitError('Invalid credentials', 'en')),
    };
    const result = await submitAuthForm(
      api,
      'sign-in',
      { email: 'a@b.test', password: 'pw' },
      'fallback',
    );
    expect(result.status).toBe('error');
    if (result.status === 'error') {
      expect(result.fields.email).toBe('a@b.test');
      expect(result.fields.password).toBe('');
      expect(result.error).toBe('Invalid credentials');
    }
  });

  it('falls back to the generic message on a non-auth failure or empty detail', async () => {
    const api: AuthApi = { submit: () => Promise.reject(new Error('network down')) };
    const result = await submitAuthForm(api, 'sign-up', { email: 'a@b.test' }, 'Generic error');
    expect(result.status).toBe('error');
    if (result.status === 'error') {
      // Internal failure messages never leak; the fallback is shown instead.
      expect(result.error).toBe('Generic error');
    }
  });
});
