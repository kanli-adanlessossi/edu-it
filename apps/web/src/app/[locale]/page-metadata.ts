// Shared `generateMetadata` helper for the foundational pages (Req 17.5).
//
// Every page built this phase must carry a document `<title>` so it reports no
// serious axe-core `document-title` violation (WCAG 2.4.2). Each page's title is
// localized from the `Meta` catalog, so it reads correctly under both `/fr` and
// `/en`. This helper centralizes the locale resolution and lookup so each page
// only names its own `Meta` key.

import type { Metadata } from 'next';
import { getTranslations, setRequestLocale } from 'next-intl/server';

import { isLocale } from '../../locale.js';

/** A `Meta` catalog key naming a page's localized document title. */
type MetaTitleKey = 'landing' | 'signUp' | 'signIn' | 'verifyEmail' | 'resetPassword' | 'dashboard';

/**
 * Build a page's localized metadata (its document `<title>`) from the `Meta`
 * catalog for the request locale, falling back to the default locale for an
 * unrecognized segment.
 */
export async function pageMetadata(
  params: Promise<{ locale: string }>,
  titleKey: MetaTitleKey,
): Promise<Metadata> {
  const { locale } = await params;
  const resolved = isLocale(locale) ? locale : 'fr';
  setRequestLocale(resolved);
  const t = await getTranslations({ locale: resolved, namespace: 'Meta' });
  return { title: t(titleKey) };
}
