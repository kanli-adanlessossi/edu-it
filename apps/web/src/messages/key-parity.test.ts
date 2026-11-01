import { describe, expect, it } from 'vitest';

import en from './en.json';
import fr from './fr.json';
import { checkKeyParity, flattenKeys, type MessageCatalog } from './key-parity.js';

// Key_Parity_Test over the shipped catalogs (api-route-wiring Req 14.11, 17.6):
// fr and en must have identical keys, including the verify-email strings.
describe('shipped message catalogs', () => {
  it('have identical key sets', () => {
    const result = checkKeyParity({ fr: fr as MessageCatalog, en: en as MessageCatalog });
    expect(result.absentCatalogs).toEqual([]);
    expect(result.missingKeys).toEqual([]);
    expect(result.passed).toBe(true);
  });

  it('cover every verify-email string used by the page', () => {
    const required = [
      'resent',
      'verifying',
      'successTitle',
      'success',
      'signInLink',
      'invalidTitle',
      'invalid',
      'verifyError',
    ].map((k) => `Auth.verifyEmail.${k}`);
    for (const catalog of [fr, en]) {
      const keys = flattenKeys(catalog as MessageCatalog);
      for (const key of required) expect(keys.has(key)).toBe(true);
    }
  });

  it('cover every reset-password, GitHub link, and GitHub confirm string (Req 14.11)', () => {
    const required = [
      'Auth.signIn.githubLink',
      ...['setNewInstructions', 'setNewSubmit', 'invalidTitle', 'invalid', 'requestNew'].map(
        (k) => `Auth.resetPassword.${k}`,
      ),
      ...['title', 'instructions', 'confirm', 'decline', 'declined', 'signInLink', 'error'].map(
        (k) => `Auth.githubConfirm.${k}`,
      ),
      'Meta.githubConfirm',
    ];
    for (const catalog of [fr, en]) {
      const keys = flattenKeys(catalog as MessageCatalog);
      for (const key of required) expect(keys.has(key), key).toBe(true);
    }
  });
});
