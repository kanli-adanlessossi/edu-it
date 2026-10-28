// Minimal pure-logic module so the apps/web lint, format, typecheck, and test
// commands have real TypeScript source to operate on. It models the two
// supported locales (fr, en) that the Web_App serves under its `/fr` and `/en`
// route trees. The bilingual routing and language switcher behavior themselves
// are implemented by a later task; this is only the smallest stable primitive.

export const LOCALES = ['fr', 'en'] as const;

export type Locale = (typeof LOCALES)[number];

/** Returns true when `value` is one of the supported locales. */
export function isLocale(value: string): value is Locale {
  return (LOCALES as readonly string[]).includes(value);
}

/** Returns the opposite supported locale. */
export function otherLocale(locale: Locale): Locale {
  return locale === 'fr' ? 'en' : 'fr';
}
