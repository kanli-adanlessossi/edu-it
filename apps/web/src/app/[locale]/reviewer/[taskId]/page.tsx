// Reviewer task detail under `/fr/reviewer/[taskId]` and `/en/reviewer/[taskId]`
// (ai-content Req 15.2, 15.3, 15.9).

import type { Metadata } from 'next';
import { notFound } from 'next/navigation';
import { getTranslations } from 'next-intl/server';

import { ReviewTaskDetailView } from '../../../../components/review-task-detail.js';
import { ReviewerApiError, type ReviewTaskDetail } from '../../../../review-tasks.js';
import { createReviewerApi } from '../../../../review-tasks-api.js';
import { pageMetadata } from '../../page-metadata.js';
import { requirePracticeContext } from '../../practice-page.js';

export const dynamic = 'force-dynamic';

interface ReviewerTaskPageProps {
  params: Promise<{ locale: string; taskId: string }>;
}

export function generateMetadata({ params }: ReviewerTaskPageProps): Promise<Metadata> {
  return pageMetadata(params, 'reviewerTask');
}

export default async function ReviewerTaskPage({ params }: ReviewerTaskPageProps) {
  const { locale: rawLocale, taskId } = await params;
  const { locale, cookie } = await requirePracticeContext(rawLocale);

  let detail: ReviewTaskDetail;
  try {
    detail = await createReviewerApi(locale, cookie ? { cookie } : {}).get(taskId);
  } catch (e) {
    if (e instanceof ReviewerApiError && e.kind === 'notFound') notFound();
    const t = await getTranslations({ locale, namespace: 'Reviewer' });
    return (
      <section>
        <h1>{t('detail.title')}</h1>
        <p role="alert">
          {e instanceof ReviewerApiError && e.kind === 'expertise'
            ? t('errors.expertise')
            : t('errors.generic')}
        </p>
      </section>
    );
  }
  return <ReviewTaskDetailView locale={locale} detail={detail} />;
}
