// Reviewer queue under `/fr/reviewer` and `/en/reviewer` (ai-content Req 15.1, 15.9).
//
// Reachable only with an active Session. The API decides expertise: a user who
// is not a reviewer gets a 403 problem, shown as a localized message.

import type { Metadata } from 'next';
import { getTranslations } from 'next-intl/server';

import { ReviewQueue } from '../../../components/review-queue.js';
import { ReviewerApiError, type ReviewTaskPage } from '../../../review-tasks.js';
import { createReviewerApi } from '../../../review-tasks-api.js';
import { pageMetadata } from '../page-metadata.js';
import { requirePracticeContext } from '../practice-page.js';

export const dynamic = 'force-dynamic';

interface ReviewerPageProps {
  params: Promise<{ locale: string }>;
}

export function generateMetadata({ params }: ReviewerPageProps): Promise<Metadata> {
  return pageMetadata(params, 'reviewer');
}

export default async function ReviewerPage({ params }: ReviewerPageProps) {
  const { locale: rawLocale } = await params;
  const { locale, cookie } = await requirePracticeContext(rawLocale);
  const t = await getTranslations({ locale, namespace: 'Reviewer' });

  let initial: ReviewTaskPage;
  try {
    initial = await createReviewerApi(locale, cookie ? { cookie } : {}).list();
  } catch (e) {
    const forbidden = e instanceof ReviewerApiError && e.kind === 'expertise';
    return (
      <section>
        <h1>{t('queue.title')}</h1>
        <p role="alert">{forbidden ? t('errors.expertise') : t('errors.generic')}</p>
      </section>
    );
  }
  return <ReviewQueue locale={locale} initial={initial} />;
}
