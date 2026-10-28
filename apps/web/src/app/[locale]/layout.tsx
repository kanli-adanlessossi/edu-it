// Root layout for the `/fr` and `/en` route trees (Req 15.1). Every page is
// nested under `[locale]`, so the only route segments that resolve are the two
// supported locales; `setRequestLocale` makes the active locale available to
// Server Components, and `NextIntlClientProvider` passes the catalog to any
// client components (such as the language switcher).

import type { Metadata } from 'next';
import type { ReactNode } from 'react';
import { notFound } from 'next/navigation';
import { NextIntlClientProvider } from 'next-intl';
import { getMessages, getTranslations, setRequestLocale } from 'next-intl/server';

import { isLocale, LOCALES } from '../../locale.js';
import { LanguageSwitcher } from '../../components/language-switcher.js';
import { AppShell } from '../../components/app-shell.js';

/**
 * Statically generate exactly the two supported locale segments (Req 15.1), so
 * `/fr` and `/en` are the only locale route trees that exist.
 */
export function generateStaticParams(): Array<{ locale: string }> {
  return LOCALES.map((locale) => ({ locale }));
}

/**
 * Give every page under the tree a localized document `<title>` so no page
 * ships without one (WCAG 2.4.2; axe-core `document-title`, Req 17.5). Pages
 * that set their own `title` override this default; the site name is kept as a
 * template suffix for any that do not.
 */
export async function generateMetadata({
  params,
}: {
  params: Promise<{ locale: string }>;
}): Promise<Metadata> {
  const { locale } = await params;
  const resolved = isLocale(locale) ? locale : 'fr';
  setRequestLocale(resolved);
  const t = await getTranslations({ locale: resolved, namespace: 'Meta' });
  // A sensible default title for any page that does not set its own; each
  // foundational page below overrides this with its own localized title.
  return { title: t('landing') };
}

interface LocaleLayoutProps {
  children: ReactNode;
  params: Promise<{ locale: string }>;
}

export default async function LocaleLayout({ children, params }: LocaleLayoutProps) {
  const { locale } = await params;
  // Reject any segment that is not exactly `fr` or `en` (Req 15.1).
  if (!isLocale(locale)) {
    notFound();
  }

  setRequestLocale(locale);
  const messages = await getMessages();

  return (
    <html lang={locale}>
      <body>
        <NextIntlClientProvider locale={locale} messages={messages}>
          <AppShell switcher={<LanguageSwitcher />}>{children}</AppShell>
        </NextIntlClientProvider>
      </body>
    </html>
  );
}
