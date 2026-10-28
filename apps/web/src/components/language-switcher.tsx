'use client';

// Language switcher UI (Req 15.2). Activating it moves the User to the
// other-locale view of the current page, preserving the path and query
// parameters. The URL rewrite itself is the pure `switchLocalePath` algebra
// from `../language-switcher.ts` (targeted by Property 21); this component only
// reads the current locale and URL and renders a link to the rewritten target.

import { useLocale, useTranslations } from 'next-intl';
import { usePathname, useSearchParams } from 'next/navigation';
import Link from 'next/link';

import { isLocale, otherLocale, type Locale } from '../locale.js';
import { switchLocalePath } from '../language-switcher.js';

export function LanguageSwitcher() {
  const t = useTranslations('LanguageSwitcher');
  const rawLocale = useLocale();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  // `useLocale` is typed as string; narrow it to a supported Locale, defaulting
  // defensively (the middleware guarantees a supported locale in practice).
  const current: Locale = isLocale(rawLocale) ? rawLocale : 'fr';
  const target = otherLocale(current);

  // `usePathname` from next-intl-aware routing includes the locale prefix, and
  // the query string is reattached so the switch preserves it (Req 15.2).
  const query = searchParams.toString();
  const pathWithQuery = query.length > 0 ? `${pathname}?${query}` : pathname;
  const href = switchLocalePath(pathWithQuery, current);

  const label = target === 'en' ? t('switchToEnglish') : t('switchToFrench');

  return (
    <Link href={href} hrefLang={target} aria-label={label}>
      {label}
    </Link>
  );
}
