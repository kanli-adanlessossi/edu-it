// Lesson reader under `/fr/.../lessons/[lessonId]` and `/en/...` (Req 19.3, 19.6).
//
// The API serves MDX source per locale (falling back to English); this Server
// Component owns the render and passes the source ONLY through the component
// allowlist (ADR A5). Source that references anything off-list shows a
// localized error instead of rendering.
import type { Metadata } from 'next';
import { getTranslations, setRequestLocale } from 'next-intl/server';
import { notFound } from 'next/navigation';

import { renderLessonMdx } from '../../../../../../../../components/lesson-mdx.js';
import { fetchLesson } from '../../../../../../../../lesson-api.js';
import { isLocale } from '../../../../../../../../locale.js';
import { UnknownMdxComponentError } from '../../../../../../../../mdx-allowlist.js';

export const dynamic = 'force-dynamic';

interface LessonPageProps {
  params: Promise<{ locale: string; trackId: string; moduleId: string; lessonId: string }>;
}

export async function generateMetadata({ params }: LessonPageProps): Promise<Metadata> {
  const { locale, lessonId } = await params;
  if (!isLocale(locale)) {
    return {};
  }
  const lesson = await fetchLesson(lessonId, locale).catch(() => undefined);
  return { title: lesson ? `${lesson.title} — EDU-IT` : 'EDU-IT' };
}

export default async function LessonPage({ params }: LessonPageProps) {
  const { locale, lessonId } = await params;
  if (!isLocale(locale)) {
    notFound();
  }
  setRequestLocale(locale);
  const t = await getTranslations({ locale, namespace: 'Lesson' });

  const lesson = await fetchLesson(lessonId, locale);
  if (lesson === undefined) {
    notFound();
  }

  let content;
  try {
    content = await renderLessonMdx(lesson.body);
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
    </article>
  );
}
