import fc from 'fast-check';
import { describe, expect, it } from 'vitest';
import { switchLocalePath, withLocalePath } from './language-switcher.js';
import { isLocale, LOCALES, otherLocale, type Locale } from './locale.js';

// Property-based test for the pure language-switcher URL algebra (task 23.3).
//
// Feature: foundations, Property 21: The language switcher preserves path and query and round-trips
//
// Property 21 (design.md): *For any* page path and set of query parameters,
// activating the language switcher changes only the locale segment while
// preserving the path and query parameters, and switching locale twice returns
// to the original URL.
//
// Validates: Requirements 15.2

/**
 * A URL path segment that is never itself a supported locale, so a generated
 * path's "non-locale" portion cannot be mistaken for a leading locale segment.
 * Segments avoid `/`, `?`, and `#` so they don't bleed into the structural
 * delimiters the algebra splits on.
 */
const nonLocaleSegment = fc
  .string({ minLength: 1, maxLength: 8 })
  .map((raw) => raw.replace(/[/?#]/g, 'x'))
  .filter((seg) => seg.length > 0 && !isLocale(seg));

/** The part of the path after the (optional) leading locale: `/a/b/c`, or `''`. */
const restArbitrary = fc
  .array(nonLocaleSegment, { minLength: 0, maxLength: 4 })
  .map((segments) => (segments.length === 0 ? '' : `/${segments.join('/')}`));

/** A query string including its leading `?`, or `''` when absent. */
const queryArbitrary = fc
  .array(
    fc.tuple(
      fc.string({ minLength: 1, maxLength: 5 }).map((k) => k.replace(/[/?#&=]/g, 'x')),
      fc.string({ minLength: 0, maxLength: 5 }).map((v) => v.replace(/[/?#&=]/g, 'x')),
    ),
    { minLength: 0, maxLength: 3 },
  )
  .map((pairs) => (pairs.length === 0 ? '' : `?${pairs.map(([k, v]) => `${k}=${v}`).join('&')}`));

/** A fragment including its leading `#`, or `''` when absent. */
const fragmentArbitrary = fc
  .string({ minLength: 0, maxLength: 8 })
  .map((raw) => raw.replace(/[#?]/g, 'x'))
  .map((frag) => (frag.length === 0 ? '' : `#${frag}`));

const localeArbitrary: fc.Arbitrary<Locale> = fc.constantFrom(...LOCALES);

/**
 * Generate an arbitrary application path together with the structural pieces it
 * was built from. `hasLocale` controls whether a leading locale segment is
 * present, exercising both the "replace" and "prepend" branches of the algebra.
 */
const pathArbitrary = fc.record({
  hasLocale: fc.boolean(),
  leadingLocale: localeArbitrary,
  rest: restArbitrary,
  query: queryArbitrary,
  hash: fragmentArbitrary,
});

describe('Feature: foundations, Property 21: The language switcher preserves path and query and round-trips', () => {
  it('changes only the locale segment and round-trips on a double switch', () => {
    fc.assert(
      fc.property(pathArbitrary, localeArbitrary, (parts, currentLocale) => {
        const { hasLocale, leadingLocale, rest, query, hash } = parts;
        const pathWithQuery = `${hasLocale ? `/${leadingLocale}` : ''}${rest === '' && !hasLocale ? '/' : rest}${query}${hash}`;

        // The origin locale is the one carried by the path, or the current
        // locale when the path carries none.
        const from = hasLocale ? leadingLocale : currentLocale;
        const target = otherLocale(from);

        const switched = switchLocalePath(pathWithQuery, currentLocale);

        // 1. The result sits under the other locale's route tree.
        expect(switched.startsWith(`/${target}`)).toBe(true);
        const afterLocale = switched.slice(`/${target}`.length);
        // The character following the locale segment is a boundary, never a
        // continuation of some longer segment (e.g. not `/england`).
        if (afterLocale.length > 0) {
          expect(['/', '?', '#']).toContain(afterLocale[0]);
        }

        // 2. The non-locale path, query, and fragment are preserved exactly —
        // `withLocalePath(..., from)` reconstructs the canonical original.
        const expectedRest = rest === '' ? '' : rest;
        expect(switched).toBe(`/${target}${expectedRest}${query}${hash}`);

        // 3. Switching twice returns to the canonical original URL.
        const back = switchLocalePath(switched, target);
        expect(back).toBe(withLocalePath(pathWithQuery, from));
        // And a third application just toggles again (involution of the pair).
        expect(switchLocalePath(back, from)).toBe(switched);
      }),
      { numRuns: 200 },
    );
  });
});
