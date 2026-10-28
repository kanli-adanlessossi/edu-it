// next-intl routing configuration: the Web_App serves its pages under a `/fr`
// route tree and an `/en` route tree and supports exactly the `fr` and `en`
// locales (Req 15.1).
//
// The supported locale set is reused from the single source of truth in
// `../locale.ts` (`LOCALES = ['fr', 'en']`) rather than duplicated, so routing,
// the switcher, and the preference round-trip all agree on exactly two locales.

import { defineRouting } from 'next-intl/routing';

import { LOCALES } from '../locale.js';

/** Locale rendered when none is present in the request (per Req 19.4 default). */
export const DEFAULT_LOCALE = 'fr';

/**
 * Routing for exactly `fr` and `en`. `localePrefix: 'always'` keeps every page
 * under an explicit `/fr` or `/en` segment (Req 15.1), so there is no
 * prefix-less route tree and the language switcher's URL algebra always has a
 * locale segment to rewrite (Req 15.2).
 */
export const routing = defineRouting({
  locales: LOCALES,
  defaultLocale: DEFAULT_LOCALE,
  localePrefix: 'always',
});
