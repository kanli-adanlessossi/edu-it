// Landing page served under both `/fr` and `/en` (Req 17.1). It introduces the
// application and links to sign-up and sign-in. All copy comes from next-intl
// keys, so the page carries no user-facing JSX literal (Req 15.7).

import type { Metadata } from 'next';
import { setRequestLocale } from 'next-intl/server';
import { useTranslations } from 'next-intl';
import { notFound } from 'next/navigation';

import { isLocale } from '../../locale.js';
import { Link } from '../../i18n/navigation.js';
import { pageMetadata } from './page-metadata.js';

interface LandingPageProps {
  params: Promise<{ locale: string }>;
}

export function generateMetadata({ params }: LandingPageProps): Promise<Metadata> {
  return pageMetadata(params, 'landing');
}

export default async function LandingPage({ params }: LandingPageProps) {
  const { locale } = await params;
  if (!isLocale(locale)) {
    notFound();
  }
  setRequestLocale(locale);
  return <LandingContent />;
}

function LandingContent() {
  const t = useTranslations('Landing');
  return (
    <section>
      <h1>{t('title')}</h1>
      <p>{t('intro')}</p>
      <p>
        <Link href="/sign-up">{t('getStarted')}</Link>
      </p>
      <p>
        <Link href="/sign-in">{t('alreadyHaveAccount')}</Link>
      </p>
    </section>
  );
}
