// next-intl per-request configuration. Resolves the active locale for the
// current request to exactly `fr` or `en` (Req 15.1), falling back to the
// default locale when the request carries an absent or unsupported locale, and
// loads that locale's message catalog.
//
// The per-locale message catalogs themselves (and their key-parity check and
// the JSX-literal lint rule) are owned by task 23.2; this config loads whichever
// catalog the resolved locale names.

import { getRequestConfig } from 'next-intl/server';

import { isLocale, type Locale } from '../locale.js';
import { routing } from './routing.js';

/**
 * Load the message catalog for a supported locale. Catalogs live in
 * `src/messages/{locale}.json`; they are populated by task 23.2 and this loader
 * reads whichever one the resolved locale names.
 */
async function loadMessages(locale: Locale): Promise<Record<string, unknown>> {
  const catalog = (await import(`../messages/${locale}.json`)) as {
    default: Record<string, unknown>;
  };
  return catalog.default;
}

export default getRequestConfig(async ({ requestLocale }) => {
  const requested = await requestLocale;
  // Resolve to a supported locale, defaulting when absent or unsupported
  // (Req 15.1): only the two locales in `routing.locales` are ever rendered.
  const locale: Locale =
    requested !== undefined && isLocale(requested) ? requested : routing.defaultLocale;

  return {
    locale,
    messages: await loadMessages(locale),
  };
});
