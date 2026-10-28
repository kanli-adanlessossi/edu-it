import { describe, expect, it } from 'vitest';
import fc from 'fast-check';
import {
  checkKeyParity,
  flattenKeys,
  type CatalogsByLocale,
  type MessageCatalog,
} from './key-parity.js';
import { LOCALES, type Locale } from '../locale.js';

// Property-based test (fast-check, the pinned PBT library) for the FR/EN
// key-parity check. This is numbered design correctness Property 23.
//
// Property 23 asserts a biconditional over arbitrary pairs of (possibly nested)
// message catalogs: `checkKeyParity` passes IF AND ONLY IF both catalogs are
// present and their flattened dotted-path key sets are identical. When it does
// not pass, the reported `missingKeys` must equal the symmetric difference of
// the present catalogs' key sets (each key tagged with the locale it is absent
// from) and `absentCatalogs` must list exactly the absent locales.

/**
 * A reference catalog built directly from an explicit set of dotted leaf paths.
 * Each path is materialized as a nested object with a string leaf, so the
 * catalog's flattened key set is exactly `paths` (barring prefix collisions,
 * which the generator below avoids). This lets the property control key sets
 * precisely while still exercising arbitrary nesting.
 */
function catalogFromPaths(paths: readonly string[]): MessageCatalog {
  const root: Record<string, string | MessageCatalog> = {};
  for (const path of paths) {
    const segments = path.split('.');
    let node = root;
    for (let i = 0; i < segments.length - 1; i += 1) {
      const segment = segments[i]!;
      const existing = node[segment];
      if (typeof existing === 'object') {
        node = existing as Record<string, string | MessageCatalog>;
      } else {
        const next: Record<string, string | MessageCatalog> = {};
        node[segment] = next;
        node = next;
      }
    }
    node[segments[segments.length - 1]!] = 'value';
  }
  return root as MessageCatalog;
}

/** A single dotted key path made of 1-3 short, distinct-per-segment segments. */
const keyPathArb = fc
  .array(fc.constantFrom('a', 'b', 'c', 'd', 'e', 'common', 'nav', 'signIn', 'signOut', 'title'), {
    minLength: 1,
    maxLength: 3,
  })
  .map((segments) => segments.join('.'));

/**
 * A set of key paths in which no path is a strict prefix of another. Prefix
 * collisions (e.g. `a` and `a.b`) would make a leaf and a group compete for the
 * same object slot, which is not a meaningful catalog shape; filtering them out
 * keeps the generated flattened key set exactly equal to the chosen paths.
 */
const keySetArb = fc.uniqueArray(keyPathArb, { minLength: 0, maxLength: 6 }).filter((paths) => {
  for (const a of paths) {
    for (const b of paths) {
      if (a !== b && (a + '.').startsWith(b + '.')) {
        return false;
      }
    }
  }
  return true;
});

/** Either a catalog built from a key set, or `undefined` (an absent catalog). */
const optionalCatalogArb: fc.Arbitrary<MessageCatalog | undefined> = fc.option(
  keySetArb.map(catalogFromPaths),
  { nil: undefined },
);

describe('Feature: foundations, Property 23: Key parity holds exactly when the catalogs share key sets', () => {
  it('passes iff both catalogs are present with identical key sets; else reports the symmetric difference and absent locales', () => {
    fc.assert(
      fc.property(optionalCatalogArb, optionalCatalogArb, (fr, en) => {
        const catalogs: CatalogsByLocale = {
          ...(fr !== undefined ? { fr } : {}),
          ...(en !== undefined ? { en } : {}),
        };

        const result = checkKeyParity(catalogs);

        // Independently compute the oracle: the per-locale key sets (empty when
        // the catalog is absent) and which locales are absent entirely.
        const keysByLocale = new Map<Locale, Set<string>>();
        const absentLocales: Locale[] = [];
        for (const locale of LOCALES) {
          const catalog = catalogs[locale];
          keysByLocale.set(locale, catalog === undefined ? new Set() : flattenKeys(catalog));
          if (catalog === undefined) {
            absentLocales.push(locale);
          }
        }

        const frKeys = keysByLocale.get('fr')!;
        const enKeys = keysByLocale.get('en')!;
        const identicalKeySets =
          frKeys.size === enKeys.size && [...frKeys].every((key) => enKeys.has(key));
        const bothPresent = absentLocales.length === 0;

        // Biconditional: pass exactly when both present and key sets identical.
        expect(result.passed).toBe(bothPresent && identicalKeySets);

        // absentCatalogs lists exactly the absent locales (in LOCALES order).
        expect(result.absentCatalogs.map((c) => c.locale)).toEqual(absentLocales);

        // missingKeys equals the symmetric difference: each key present in one
        // locale but not the other, tagged with the locale it is absent from.
        const expectedMissing: { key: string; absentFrom: Locale }[] = [];
        for (const locale of LOCALES) {
          const own = keysByLocale.get(locale)!;
          for (const other of LOCALES) {
            if (other === locale) {
              continue;
            }
            const otherKeys = keysByLocale.get(other)!;
            for (const key of own) {
              if (!otherKeys.has(key)) {
                expectedMissing.push({ key, absentFrom: other });
              }
            }
          }
        }
        expectedMissing.sort((a, b) =>
          a.key === b.key ? a.absentFrom.localeCompare(b.absentFrom) : a.key.localeCompare(b.key),
        );

        expect(result.missingKeys.map((m) => ({ key: m.key, absentFrom: m.absentFrom }))).toEqual(
          expectedMissing,
        );
      }),
      { numRuns: 200 },
    );
  });
});
