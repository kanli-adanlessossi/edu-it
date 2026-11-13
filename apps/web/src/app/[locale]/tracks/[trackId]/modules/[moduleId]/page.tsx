// Module page under `/fr/tracks/:trackId/modules/:moduleId` and the `/en`
// equivalent: shows the module's lock state and its lessons, each with the quiz and labs
// reachable from it (Req 19.2). A locked module lists its lessons without links.
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
  const lessons = module.lessons ?? [];
  const completed = new Set(module.completions.map((c) => c.lesson_id));
  const trackPath = `/${locale}/tracks/${encodeURIComponent(trackId)}`;
  const lessonHref = (lessonId: string): string =>
    `${trackPath}/modules/${encodeURIComponent(moduleId)}/lessons/${encodeURIComponent(lessonId)}`;
  return (
    <section data-lock-state={lock}>
      <h1>{module.title || t('module.title')}</h1>
      <p>{t(lock === 'locked' ? 'lock.locked' : 'lock.unlocked')}</p>
      {lock === 'locked' ? <p>{t('module.lockedHint')}</p> : null}
      {lessons.length > 0 ? (
        <>
          <h2>{t('module.lessons')}</h2>
          <ol className="lesson-list">
            {lessons.map((lesson) => (
              <li key={lesson.lesson_id}>
                {lock === 'locked' ? (
                  <span>{lesson.title}</span>
                ) : (
                  <Link href={lessonHref(lesson.lesson_id)}>{lesson.title}</Link>
                )}
                {completed.has(lesson.lesson_id) ? (
                  <span className="badge">{t('module.lessonDone')}</span>
                ) : null}
                {lock === 'unlocked' && (lesson.quiz_id || (lesson.lab_ids ?? []).length > 0) ? (
                  <span className="lesson-actions">
                    {lesson.quiz_id ? (
                      <Link href={`/${locale}/quizzes/${encodeURIComponent(lesson.quiz_id)}`}>
                        {t('module.takeQuiz')}
                      </Link>
                    ) : null}
                    {(lesson.lab_ids ?? []).map((labId, index) => {
                      const title = lesson.labs?.find((lab) => lab.lab_id === labId)?.title;
                      return (
                        <Link key={labId} href={`/${locale}/labs/${encodeURIComponent(labId)}`}>
                          {title
                            ? t('module.labNamed', { title })
                            : t('module.openLab', { number: index + 1 })}
                        </Link>
                      );
                    })}
                  </span>
                ) : null}
              </li>
            ))}
          </ol>
        </>
      ) : (
        <>
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
        </>
      )}
      <p>
        <Link href={trackPath}>{t('module.backToTrack')}</Link>
      </p>
    </section>
  );
}
