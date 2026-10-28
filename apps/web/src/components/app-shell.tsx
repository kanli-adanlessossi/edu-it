'use client';

// Application shell wrapping every page under `/fr` and `/en` (Req 17.1).
//
// The shell provides the consistent chrome every foundational page sits in: a
// skip link to the main content, a banner header carrying the app name, the
// primary navigation, and the language switcher, plus the `<main>` content
// landmark. Using semantic landmarks (`header`/`nav`/`main`) and an
// accessible-name-bearing skip link keeps the shell free of serious or critical
// axe-core violations (Req 17.5). All copy comes from next-intl keys, never JSX
// literals (Req 15.7).
//
// Navigation links are locale-prefixed with `next/link` directly (rather than
// the next-intl navigation helper) so the shell can render in both Server- and
// Node-test contexts; the active locale from `useLocale` builds the `/fr` or
// `/en` prefix.

import { Suspense, type ReactNode } from 'react';
import { useLocale, useTranslations } from 'next-intl';
import Link from 'next/link';

import { isLocale, type Locale } from '../locale.js';

interface AppShellProps {
  /** The page content rendered in the main landmark. */
  readonly children: ReactNode;
  /** The language switcher control, injected so the shell stays locale-agnostic. */
  readonly switcher: ReactNode;
}

const MAIN_CONTENT_ID = 'main-content';

export function AppShell({ children, switcher }: AppShellProps) {
  const t = useTranslations('Shell');
  const rawLocale = useLocale();
  const locale: Locale = isLocale(rawLocale) ? rawLocale : 'fr';

  // Build a locale-prefixed href, e.g. `/fr` or `/fr/sign-in` (Req 17.1).
  const href = (path: string): string => (path === '/' ? `/${locale}` : `/${locale}${path}`);

  return (
    <>
      <a href={`#${MAIN_CONTENT_ID}`}>{t('skipToContent')}</a>
      <header>
        <Link href={href('/')}>{t('appName')}</Link>
        <nav aria-label={t('nav.home')}>
          <ul>
            <li>
              <Link href={href('/')}>{t('nav.home')}</Link>
            </li>
            <li>
              <Link href={href('/sign-in')}>{t('nav.signIn')}</Link>
            </li>
            <li>
              <Link href={href('/sign-up')}>{t('nav.signUp')}</Link>
            </li>
            <li>
              <Link href={href('/dashboard')}>{t('nav.dashboard')}</Link>
            </li>
          </ul>
        </nav>
        {/* The switcher reads `useSearchParams`; a Suspense boundary lets a
            dynamically rendered page (e.g. the gated dashboard) still include
            the shell without a client-side-rendering bailout. */}
        <Suspense>{switcher}</Suspense>
      </header>
      <main id={MAIN_CONTENT_ID}>{children}</main>
    </>
  );
}
