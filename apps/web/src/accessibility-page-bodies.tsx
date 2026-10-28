'use client';

// Test-only page bodies mirroring the `<main>` content of the two server pages
// that render their body inline rather than through a dedicated client
// component: the landing page (`app/[locale]/page.tsx`) and the empty dashboard
// (`app/[locale]/dashboard/page.tsx`).
//
// The axe-core accessibility scan (task 24.4, Req 17.5) renders real client
// components outside the Next.js server runtime. The landing and dashboard
// server components call server-only APIs (`setRequestLocale`, `cookies`,
// `redirect`) around a small body built from next-intl keys. These components
// reproduce exactly that body — same elements, same translation keys — so the
// scan evaluates the identical markup a visitor sees, without invoking the
// server-only wrappers. The auth pages, by contrast, already have dedicated
// client body components (`sign-up-form.tsx`, …) and are scanned directly.

import { useTranslations } from 'next-intl';
import Link from 'next/link';

import { type Locale } from './locale.js';

interface BodyProps {
  readonly locale: Locale;
}

/**
 * The landing page body (mirrors `LandingContent` in `app/[locale]/page.tsx`).
 * It is locale-aware: its links are prefixed with the active locale.
 * Links are locale-prefixed exactly as the next-intl navigation `Link` renders
 * them, using the active locale.
 */
export function LandingBody({ locale }: BodyProps) {
  const t = useTranslations('Landing');
  return (
    <section>
      <h1>{t('title')}</h1>
      <p>{t('intro')}</p>
      <p>
        <Link href={`/${locale}/sign-up`}>{t('getStarted')}</Link>
      </p>
      <p>
        <Link href={`/${locale}/sign-in`}>{t('alreadyHaveAccount')}</Link>
      </p>
    </section>
  );
}

/**
 * The authenticated dashboard body (mirrors `DashboardContent` in
 * `app/[locale]/dashboard/page.tsx`). Its content carries no locale-dependent
 * links, so it needs no props. The access gate is covered elsewhere
 * (Property 25); here we scan the content a User with an active Session sees.
 */
export function DashboardBody() {
  const t = useTranslations('Dashboard');
  return (
    <section>
      <h1>{t('title')}</h1>
      <p>{t('empty')}</p>
    </section>
  );
}
