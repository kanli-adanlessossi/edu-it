import { describe, expect, it } from 'vitest';
import fc from 'fast-check';
import { LOCALES, type Locale } from './locale.js';
import { isLegalPage, LEGAL_PAGES, resolveLegalPage, type LegalPage } from './legal-pages.js';

// Unit tests for serving the FR/EN privacy and terms placeholder pages per the
// requested locale (Req 14.5). These are the targeted unit tests the design
// lists for the placeholder pages, not a numbered design correctness property.

describe('isLegalPage', () => {
  it('accepts the known legal pages', () => {
    expect(isLegalPage('privacy')).toBe(true);
    expect(isLegalPage('terms')).toBe(true);
  });

  it('rejects unknown page names', () => {
    expect(isLegalPage('about')).toBe(false);
    expect(isLegalPage('Privacy')).toBe(false);
    expect(isLegalPage('')).toBe(false);
  });
});

describe('resolveLegalPage serves per requested locale', () => {
  it('serves the French privacy placeholder for fr', () => {
    const content = resolveLegalPage('privacy', 'fr');
    expect(content.page).toBe('privacy');
    expect(content.locale).toBe('fr');
    expect(content.title).toBe('Politique de confidentialité');
    expect(content.body.length).toBeGreaterThan(0);
  });

  it('serves the English privacy placeholder for en', () => {
    const content = resolveLegalPage('privacy', 'en');
    expect(content.page).toBe('privacy');
    expect(content.locale).toBe('en');
    expect(content.title).toBe('Privacy Policy');
    expect(content.body.length).toBeGreaterThan(0);
  });

  it('serves the French terms placeholder for fr', () => {
    const content = resolveLegalPage('terms', 'fr');
    expect(content.page).toBe('terms');
    expect(content.locale).toBe('fr');
    expect(content.title).toBe("Conditions d'utilisation");
    expect(content.body.length).toBeGreaterThan(0);
  });

  it('serves the English terms placeholder for en', () => {
    const content = resolveLegalPage('terms', 'en');
    expect(content.page).toBe('terms');
    expect(content.locale).toBe('en');
    expect(content.title).toBe('Terms of Service');
    expect(content.body.length).toBeGreaterThan(0);
  });
});

// Property-based coverage: for every (page, locale) pair the resolver returns
// non-empty, locale-matched placeholder content, and distinct locales yield
// distinct copy so the served page actually follows the requested locale.
describe('legal page placeholder properties', () => {
  it('returns non-empty content echoing the requested page and locale', () => {
    fc.assert(
      fc.property(
        fc.constantFrom<LegalPage>(...LEGAL_PAGES),
        fc.constantFrom<Locale>(...LOCALES),
        (page, locale) => {
          const content = resolveLegalPage(page, locale);
          return (
            content.page === page &&
            content.locale === locale &&
            content.title.length > 0 &&
            content.body.length > 0
          );
        },
      ),
      { numRuns: 100 },
    );
  });

  it('serves distinct FR and EN copy for the same page', () => {
    fc.assert(
      fc.property(fc.constantFrom<LegalPage>(...LEGAL_PAGES), (page) => {
        const fr = resolveLegalPage(page, 'fr');
        const en = resolveLegalPage(page, 'en');
        return fr.title !== en.title && fr.body !== en.body;
      }),
      { numRuns: 100 },
    );
  });
});
