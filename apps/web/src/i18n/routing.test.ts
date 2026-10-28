import { describe, expect, it } from 'vitest';

import { DEFAULT_LOCALE, routing } from './routing.js';
import { generateStaticParams } from '../app/[locale]/layout.js';
import { LOCALES } from '../locale.js';

// Unit tests proving the `/fr` and `/en` route trees exist for exactly the two
// supported locales (Req 15.1). Two independent sources must agree on exactly
// `fr` and `en`:
//   1. the next-intl routing config (`routing.locales`), which drives the
//      `/fr` and `/en` prefixes, and
//   2. `generateStaticParams` on the `[locale]` layout, which statically
//      generates exactly one route tree per supported locale.
// If either drifted (a third locale, a dropped locale, a different default),
// these assertions would fail and the `test` command would exit non-zero.

const EXPECTED_LOCALES = ['fr', 'en'] as const;

describe('routing covers exactly fr and en (Req 15.1)', () => {
  it('routing.locales equals exactly ["fr", "en"]', () => {
    // Order-sensitive equality: the configured locale list is precisely the two
    // supported locales, no more and no fewer.
    expect([...routing.locales]).toEqual([...EXPECTED_LOCALES]);
  });

  it('routing.locales is the single LOCALES source of truth', () => {
    // routing reuses LOCALES rather than duplicating the set, so the `/fr` and
    // `/en` prefixes stay in lockstep with every other locale-aware module.
    expect([...routing.locales]).toEqual([...LOCALES]);
  });

  it('defaults to a supported locale with an always-on prefix', () => {
    // `localePrefix: 'always'` is what guarantees an explicit `/fr` or `/en`
    // segment on every page (no prefix-less tree).
    expect(routing.localePrefix).toBe('always');
    expect(routing.defaultLocale).toBe(DEFAULT_LOCALE);
    expect([...routing.locales]).toContain(routing.defaultLocale);
  });
});

describe('generateStaticParams yields exactly the fr and en route trees (Req 15.1)', () => {
  it('emits one param per supported locale and nothing else', () => {
    const params = generateStaticParams();
    const generated = params.map((p) => p.locale);

    // Exactly two route trees are generated, for exactly `fr` and `en`.
    expect(generated).toEqual([...EXPECTED_LOCALES]);
    // No duplicate locale segments.
    expect(new Set(generated).size).toBe(generated.length);
  });

  it('agrees with the routing config on the locale set', () => {
    const generated = generateStaticParams().map((p) => p.locale);
    expect(new Set(generated)).toEqual(new Set(routing.locales));
  });
});
