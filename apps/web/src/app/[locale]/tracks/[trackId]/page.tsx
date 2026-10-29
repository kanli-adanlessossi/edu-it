// Track page under `/fr/tracks/:trackId` and `/en/tracks/:trackId`: shows each
// module's lock state (Req 19.2). Data comes from the generated client (Req 19.6).
import type { Metadata } from 'next';
import { getTranslations, setRequestLocale } from 'next-intl/server';
import { notFound } from 'next/navigation';

import { ModuleList } from '../../../../components/module-list.js';
import { LearningApiError } from '../../../../learning-api.js';
import { requireLearningApi } from '../../../../learning-server.js';
import { completedLessonCount, parseRouteId, unlockedPercent } from '../../../../learning.js';
import { isLocale } from '../../../../locale.js';

export const dynamic = 'force-dynamic';

interface TrackPageProps {
  params: Promise<{ locale: string; trackId: string }>;
}

export async function generateMetadata({ params }: TrackPageProps): Promise<Metadata> {
  const { locale } = await params;
  const resolved = isLocale(locale) ? locale : 'fr';
  const t = await getTranslations({ locale: resolved, namespace: 'Meta' });
  return { title: t('track') };
}

export default async function TrackPage({ params }: TrackPageProps) {
  const { locale, trackId: rawTrackId } = await params;
  if (!isLocale(locale)) notFound();
  const trackId = parseRouteId(rawTrackId);
  if (trackId === undefined) notFound();
  setRequestLocale(locale);

  const api = await requireLearningApi(locale);
  const t = await getTranslations({ locale, namespace: 'Learning' });

  let progress;
  try {
    progress = await api.getProgress(trackId);
  } catch (error) {
    if (error instanceof LearningApiError && (error.status === 404 || error.status === 409)) {
      notFound();
    }
    return (
      <section>
        <h1>{t('track.title')}</h1>
        <p role="alert">{t('loadError')}</p>
      </section>
    );
  }

  return (
    <section>
      <h1>{t('track.title')}</h1>
      <p>
        {t('track.summary', {
          percent: unlockedPercent(progress.modules),
          lessons: completedLessonCount(progress.modules),
        })}
      </p>
      <h2>{t('track.modules')}</h2>
      <ModuleList locale={locale} trackId={trackId} modules={progress.modules} />
    </section>
  );
}
