// Learner dashboard under `/fr/dashboard` and `/en/dashboard` (Req 19.1, 19.6).
//
// Reachable ONLY by a User with an active Session; otherwise redirected to
// sign-in (Req 17.4). Shows progress per enrolled track and the concepts due and
// overdue for review. All data flows through the generated client.

import type { Metadata } from 'next';
import { getTranslations, setRequestLocale } from 'next-intl/server';
import Link from 'next/link';
import { notFound } from 'next/navigation';

import { requireLearningApi } from '../../../learning-server.js';
import {
  completedLessonCount,
  splitDueOverdue,
  unlockedPercent,
  type Progress,
} from '../../../learning.js';
import { isLocale } from '../../../locale.js';
import { pageMetadata } from '../page-metadata.js';
import { enrollAction } from './enroll-action.js';

export const dynamic = 'force-dynamic';

interface DashboardPageProps {
  params: Promise<{ locale: string }>;
  searchParams: Promise<{ error?: string }>;
}

export function generateMetadata({ params }: DashboardPageProps): Promise<Metadata> {
  return pageMetadata(params, 'dashboard');
}

export default async function DashboardPage({ params, searchParams }: DashboardPageProps) {
  const { locale } = await params;
  if (!isLocale(locale)) notFound();
  setRequestLocale(locale);
  const { error } = await searchParams;

  const api = await requireLearningApi(locale);
  const t = await getTranslations({ locale, namespace: 'Learning' });
  const title = (await getTranslations({ locale, namespace: 'Dashboard' }))('title');

  let progressByTrack: Progress[] = [];
  let schedule: Awaited<ReturnType<typeof api.getReviewSchedule>> = [];
  let loadFailed = false;
  try {
    const enrollments = await api.listEnrollments();
    progressByTrack = await Promise.all(enrollments.map((e) => api.getProgress(e.track_id)));
    schedule = await api.getReviewSchedule();
  } catch {
    loadFailed = true;
  }
  const { due, overdue } = splitDueOverdue(schedule, new Date());

  return (
    <section>
      <h1>{title}</h1>
      {loadFailed ? <p role="alert">{t('loadError')}</p> : null}
      {error === 'invalid' || error === 'enroll' ? (
        <p role="alert">{t(error === 'invalid' ? 'enroll.invalid' : 'enroll.failed')}</p>
      ) : null}

      <h2>{t('dashboard.progress')}</h2>
      {progressByTrack.length === 0 ? (
        <p>{t('dashboard.noEnrollments')}</p>
      ) : (
        <ul>
          {progressByTrack.map((p) => (
            <li key={p.track_id}>
              <Link href={`/${locale}/tracks/${encodeURIComponent(p.track_id)}`}>{p.track_id}</Link>{' '}
              {t('track.summary', {
                percent: unlockedPercent(p.modules),
                lessons: completedLessonCount(p.modules),
              })}
            </li>
          ))}
        </ul>
      )}

      <h2>{t('dashboard.due')}</h2>
      {due.length === 0 ? (
        <p>{t('dashboard.noneDue')}</p>
      ) : (
        <ul>
          {due.map((e) => (
            <li key={e.concept_id}>{e.concept_id}</li>
          ))}
        </ul>
      )}

      <h2>{t('dashboard.overdue')}</h2>
      {overdue.length === 0 ? (
        <p>{t('dashboard.noneOverdue')}</p>
      ) : (
        <ul>
          {overdue.map((e) => (
            <li key={e.concept_id}>{e.concept_id}</li>
          ))}
        </ul>
      )}

      <h2>{t('enroll.heading')}</h2>
      <form action={enrollAction}>
        <input type="hidden" name="locale" value={locale} />
        <label htmlFor="trackId">{t('enroll.trackLabel')}</label>{' '}
        <input id="trackId" name="trackId" type="text" required maxLength={128} />{' '}
        <button type="submit">{t('enroll.submit')}</button>
      </form>
    </section>
  );
}
