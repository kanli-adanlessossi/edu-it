// Key-parity check for the FR/EN next-intl message catalogs (Req 15.5, 15.6).
//
// The bilingual shell ships one message catalog per locale (`fr`, `en`), owned
// by and shipped with `apps/web`. The Key_Parity_Test asserts that every
// translation key present in either catalog exists in both; a key present in
// only one catalog fails the check and is reported together with the catalog it
// is absent from. A catalog that is entirely absent is also reported.
//
// This module is deliberately pure and framework-free: it takes already-parsed
// catalog objects and returns a structured result. That keeps it directly
// unit-testable and lets Property 23 target it over randomly generated FR/EN
// key sets without touching next-intl or the filesystem.

import { LOCALES, type Locale } from '../locale.js';

/**
 * A message catalog is an arbitrarily nested object whose leaf values are the
 * translated strings. Nested groups (e.g. `common.signIn`) are addressed by
 * their dotted key path.
 */
export interface MessageCatalog {
  readonly [key: string]: string | MessageCatalog;
}

/** One translation key that is present in one catalog but absent from another. */
export interface MissingKey {
  /** The dotted key path that is not shared by both catalogs. */
  readonly key: string;
  /** The locale whose catalog is missing `key`. */
  readonly absentFrom: Locale;
}

/** A locale whose catalog is entirely absent (not provided at all). */
export interface AbsentCatalog {
  readonly locale: Locale;
}

/**
 * Outcome of the key-parity check.
 *
 * `passed` is true only when every key present in either catalog exists in
 * both AND neither catalog is entirely absent. When it is false, `missingKeys`
 * lists exactly the symmetric-difference keys (each with the catalog it is
 * absent from) and `absentCatalogs` lists any locale whose catalog was not
 * provided at all.
 */
export interface KeyParityResult {
  readonly passed: boolean;
  /** Symmetric-difference keys, sorted by `key` then `absentFrom` for stable reporting. */
  readonly missingKeys: readonly MissingKey[];
  /** Locales whose catalog was entirely absent, in `LOCALES` order. */
  readonly absentCatalogs: readonly AbsentCatalog[];
}

/** The pair of catalogs to compare, keyed by locale. A value may be absent. */
export type CatalogsByLocale = {
  readonly [L in Locale]?: MessageCatalog;
};

/**
 * Flattens a (possibly nested) catalog into the set of its dotted leaf key
 * paths. Only leaf (string-valued) keys are compared, so a group key never
 * collides with a leaf key.
 */
export function flattenKeys(catalog: MessageCatalog, prefix = ''): Set<string> {
  const keys = new Set<string>();
  for (const [key, value] of Object.entries(catalog)) {
    const path = prefix === '' ? key : `${prefix}.${key}`;
    if (typeof value === 'string') {
      keys.add(path);
    } else {
      for (const nested of flattenKeys(value, path)) {
        keys.add(nested);
      }
    }
  }
  return keys;
}

/**
 * Checks key parity across the supplied per-locale catalogs.
 *
 * Passes only when both the `fr` and `en` catalogs are present and share an
 * identical set of (flattened) translation keys. Otherwise reports the
 * symmetric-difference keys with the catalog each is absent from, and reports
 * any locale whose catalog is entirely absent (Req 15.5, 15.6).
 */
export function checkKeyParity(catalogs: CatalogsByLocale): KeyParityResult {
  const absentCatalogs: AbsentCatalog[] = [];
  for (const locale of LOCALES) {
    if (catalogs[locale] === undefined) {
      absentCatalogs.push({ locale });
    }
  }

  // Keys are only compared between catalogs that are actually present. A key in
  // a present catalog is "absent from" any other locale whose catalog is
  // entirely missing, so an absent catalog still surfaces every counterpart
  // key as a missing key.
  const keysByLocale = new Map<Locale, Set<string>>();
  for (const locale of LOCALES) {
    const catalog = catalogs[locale];
    keysByLocale.set(locale, catalog === undefined ? new Set() : flattenKeys(catalog));
  }

  const missingKeys: MissingKey[] = [];
  for (const locale of LOCALES) {
    const own = keysByLocale.get(locale) ?? new Set();
    for (const other of LOCALES) {
      if (other === locale) {
        continue;
      }
      const otherKeys = keysByLocale.get(other) ?? new Set();
      for (const key of own) {
        if (!otherKeys.has(key)) {
          missingKeys.push({ key, absentFrom: other });
        }
      }
    }
  }

  missingKeys.sort((a, b) =>
    a.key === b.key ? a.absentFrom.localeCompare(b.absentFrom) : a.key.localeCompare(b.key),
  );

  return {
    passed: absentCatalogs.length === 0 && missingKeys.length === 0,
    missingKeys,
    absentCatalogs,
  };
}

/**
 * Formats a failing {@link KeyParityResult} into a human-readable report naming
 * each missing key and the catalog it is absent from, plus any absent catalog.
 * Returns an empty string when the result passed.
 */
export function formatKeyParityReport(result: KeyParityResult): string {
  if (result.passed) {
    return '';
  }
  const lines: string[] = [];
  for (const { locale } of result.absentCatalogs) {
    lines.push(`Catalog for locale "${locale}" is absent.`);
  }
  for (const { key, absentFrom } of result.missingKeys) {
    lines.push(`Key "${key}" is absent from the "${absentFrom}" catalog.`);
  }
  return lines.join('\n');
}
