// Lab_View page under `/fr/labs/[labId]` and `/en/labs/[labId]` (hands-on-labs Req 15.1, 15.11).
//
// The Server Component gates on an active Session, loads the lab in the active
// locale, and hands it to the client `LabView`, which owns the session, the
// terminal or editor, grading, and the timer.

import type { Metadata } from 'next';
import { getTranslations, setRequestLocale } from 'next-intl/server';

import { LabView } from '../../../../components/lab-view.js';
import { fetchLab } from '../../../../lab-api.js';
import { isLocale } from '../../../../locale.js';
import { requirePracticeContext } from '../../practice-page.js';

export const dynamic = 'force-dynamic';

interface LabPageProps {
  params: Promise<{ locale: string; labId: string }>;
}

export async function generateMetadata({ params }: LabPageProps): Promise<Metadata> {
  const { locale } = await params;
  if (!isLocale(locale)) {
    return {};
  }
  setRequestLocale(locale);
  const t = await getTranslations({ locale, namespace: 'Labs' });
  return { title: `${t('title')} — EDU-IT` };
}

export default async function LabPage({ params }: LabPageProps) {
  const { locale: rawLocale, labId } = await params;
  const { locale, cookie } = await requirePracticeContext(rawLocale);
  const lab = await fetchLab(labId, locale, cookie).catch(() => undefined);
  if (lab === undefined || lab === null) {
    const t = await getTranslations({ locale, namespace: 'Labs.errors' });
    return <p role="alert">{lab === null ? t('notFound') : t('loadFailed')}</p>;
  }
  return <LabView locale={locale} lab={lab} />;
}
