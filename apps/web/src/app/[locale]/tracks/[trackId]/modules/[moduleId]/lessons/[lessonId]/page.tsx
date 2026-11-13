// Lesson reader under `/fr/.../lessons/[lessonId]` and `/en/...` (Req 19.3, 19.6).
//
// The API serves MDX source per locale (falling back to English); this Server
// Component owns the render and passes the source ONLY through the component
// allowlist (ADR A5). Source that references anything off-list shows a
// localized error instead of rendering.
import type { Metadata } from 'next';
import { getTranslations, setRequestLocale } from 'next-intl/server';
import { cookies } from 'next/headers';
import Link from 'next/link';
import { notFound } from 'next/navigation';

import { ReportProblem } from '../../../../../../../../components/report-problem.js';
import { renderLessonMdx } from '../../../../../../../../components/lesson-mdx.js';
import { fetchLesson, stripLeadingTitle } from '../../../../../../../../lesson-api.js';
import { requireSessionReference } from '../../../../../../../../learning-server.js';
import { isLocale } from '../../../../../../../../locale.js';
import { UnknownMdxComponentError } from '../../../../../../../../mdx-allowlist.js';
import { SESSION_COOKIE } from '../../../../../../../../session-shared.js';

export const dynamic = 'force-dynamic';

interface LessonPageProps {
  params: Promise<{ locale: string; trackId: string; moduleId: string; lessonId: string }>;
}

export async function generateMetadata({ params }: LessonPageProps): Promise<Metadata> {
  const { locale, lessonId } = await params;
  if (!isLocale(locale)) {
    return {};
  }
  // The API requires a Session; forward the visitor's cookie (the page itself redirects a visitor
  // without one).
  const reference = (await cookies()).get(SESSION_COOKIE)?.value;
  const lesson = await fetchLesson(lessonId, locale, undefined, reference).catch(() => undefined);
  return { title: lesson ? `${lesson.title} — EDU-IT` : 'EDU-IT' };
}

export default async function LessonPage({ params }: LessonPageProps) {
  const { locale, trackId, moduleId, lessonId } = await params;
  if (!isLocale(locale)) {
    notFound();
  }
  setRequestLocale(locale);
  const reference = await requireSessionReference(locale);
  const t = await getTranslations({ locale, namespace: 'Lesson' });

  const lesson = await fetchLesson(lessonId, locale, undefined, reference);
  if (lesson === undefined) {
    notFound();
  }

  const labIds = lesson.lab_ids ?? [];
  const labTitles = new Map((lesson.labs ?? []).map((lab) => [lab.lab_id, lab.title]));
  let content;
  try {
    // The page prints the title; drop the body's identical opening heading so it shows once.
    content = await renderLessonMdx(stripLeadingTitle(lesson.body, lesson.title));
  } catch (error) {
    if (error instanceof UnknownMdxComponentError) {
      return (
        <article>
          <h1>{lesson.title}</h1>
          <p role="alert">{t('unsupportedContent')}</p>
        </article>
      );
    }
    throw error;
  }

  return (
    <article lang={lesson.locale}>
      <h1>{lesson.title}</h1>
      {lesson.locale !== locale ? <p>{t('fallbackNotice')}</p> : null}
      {content}
      <ReportProblem
        locale={locale}
        itemKind="lesson"
        itemId={lesson.stable_id}
        contentVersion={lesson.content_version}
      />
      {lesson.quiz_id ? (
        <p>
          <Link href={`/${locale}/quizzes/${encodeURIComponent(lesson.quiz_id)}`}>
            {t('takeQuiz')}
          </Link>
        </p>
      ) : null}
      {labIds.length > 0 ? (
        <section aria-labelledby="lesson-labs-heading">
          <h2 id="lesson-labs-heading">{t('labsHeading')}</h2>
          <ul>
            {labIds.map((labId, index) => (
              <li key={labId}>
                <Link href={`/${locale}/labs/${encodeURIComponent(labId)}`}>
                  {labTitles.get(labId)
                    ? t('labNamed', { title: labTitles.get(labId) ?? '' })
                    : t('openLab', { number: index + 1 })}
                </Link>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
      <p>
        <Link
          href={`/${locale}/tracks/${encodeURIComponent(trackId)}/modules/${encodeURIComponent(moduleId)}`}
        >
          {t('backToModule')}
        </Link>
      </p>
    </article>
  );
}
