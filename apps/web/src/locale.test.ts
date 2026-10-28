import { describe, expect, it } from 'vitest';
import fc from 'fast-check';
import { isLocale, LOCALES, otherLocale, type Locale } from './locale.js';

// Unit tests: specific examples and edge cases that exercise the exit-status
// contract of the `test` command (green run exits zero; a failure would exit
// non-zero and name the failing test).
describe('isLocale', () => {
  it('accepts the two supported locales', () => {
    expect(isLocale('fr')).toBe(true);
    expect(isLocale('en')).toBe(true);
  });

  it('rejects unsupported or malformed values', () => {
    expect(isLocale('de')).toBe(false);
    expect(isLocale('FR')).toBe(false);
    expect(isLocale('')).toBe(false);
  });
});

describe('otherLocale', () => {
  it('maps each locale to the other one', () => {
    expect(otherLocale('fr')).toBe('en');
    expect(otherLocale('en')).toBe('fr');
  });
});

// Property-based tests (fast-check, the pinned PBT library) covering universal
// properties of the locale primitives across all inputs. These are local
// tooling-verification properties, not numbered design correctness properties.
describe('locale properties', () => {
  it('otherLocale is an involution over supported locales', () => {
    fc.assert(
      fc.property(fc.constantFrom<Locale>(...LOCALES), (locale) => {
        return otherLocale(otherLocale(locale)) === locale;
      }),
      { numRuns: 100 },
    );
  });

  it('isLocale is true exactly for the supported locale set', () => {
    fc.assert(
      fc.property(fc.string(), (value) => {
        return isLocale(value) === (LOCALES as readonly string[]).includes(value);
      }),
      { numRuns: 100 },
    );
  });
});
