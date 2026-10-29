// Module page under `/fr/tracks/:trackId/modules/:moduleId` and the `/en`
// equivalent: shows the module's lock state and completed lessons (Req 19.2).
import type { Metadata } from 'next';
import { getTranslations, setRequestLocale } from 'next-intl/server';
import { notFound } from 'next/navigation';
import Link from 'next/link';

import { LearningApiError } from '../../../../../../learning-api.js';
import { requireLearningApi } from '../../../../../../learning-server.js';
import { deriveLockState, findModule, parseRouteId } from '../../../../../../learning.js';
import { isLocale } from '../../../../../../locale.js';

export const dynamic = 'force-dynamic';

interface ModulePageProps {
  params: Promise<{ locale: string; trackId: string; moduleId: string }>;
}

export async function generateMetadata({ params }: ModulePageProps): Promise<Metadata> {
  const { locale } = await params;
  const resolved = isLocale(locale) ? locale : 'fr';
  const t = await getTranslations({ locale: resolved, namespace: 'Meta' });
  return { title: t('module') };
}

export default async function ModulePage({ params }: ModulePageProps) {
  const { locale, trackId: rawTrackId, moduleId: rawModuleId } = await params;
  if (!isLocale(locale)) notFound();
  const trackId = parseRouteId(rawTrackId);
  const moduleId = parseRouteId(rawModuleId);
  if (trackId === undefined || moduleId === undefined) notFound();
  setRequestLocale(locale);

  const api = await requireLearningApi(locale);
  const t = await getTranslations({ locale, namespace: 'Learning' });

  let module;
  try {
    module = findModule(await api.getProgress(trackId), moduleId);
  } catch (error) {
    if (error instanceof LearningApiError && (error.status === 404 || error.status === 409)) {
      notFound();
    }
    return (
      <section>
        <h1>{t('module.title')}</h1>
        <p role="alert">{t('loadError')}</p>
      </section>
    );
  }
  if (module === undefined) notFound();

  const lock = deriveLockState(module);
  return (
    <section data-lock-state={lock}>
      <h1>{t('module.title')}</h1>
      <p>{t(lock === 'locked' ? 'lock.locked' : 'lock.unlocked')}</p>
      {lock === 'locked' ? <p>{t('module.lockedHint')}</p> : null}
      <h2>{t('module.completedLessons')}</h2>
      {module.completions.length === 0 ? (
        <p>{t('module.noCompletions')}</p>
      ) : (
        <ul>
          {module.completions.map((c) => (
            <li key={c.lesson_id}>{c.lesson_id}</li>
          ))}
        </ul>
      )}
      <p>
        <Link href={`/${locale}/tracks/${encodeURIComponent(trackId)}`}>
          {t('module.backToTrack')}
        </Link>
      </p>
    </section>
  );
}
