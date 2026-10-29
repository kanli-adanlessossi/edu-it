// Review session page under `/fr/reviews/[conceptId]` and `/en/reviews/[conceptId]`
// (Req 19.5, 19.6).

import type { Metadata } from 'next';
import { getTranslations } from 'next-intl/server';

import { ReviewSession } from '../../../../components/review-session.js';
import { loadReviewQuestions } from '../../../../practice-api.js';
import { practiceMetadata, requirePracticeContext } from '../../practice-page.js';

export const dynamic = 'force-dynamic';

interface ReviewPageProps {
  params: Promise<{ locale: string; conceptId: string }>;
}

export function generateMetadata({ params }: ReviewPageProps): Promise<Metadata> {
  return practiceMetadata(params, 'review');
}

export default async function ReviewPage({ params }: ReviewPageProps) {
  const { locale: rawLocale, conceptId } = await params;
  const { locale, cookie } = await requirePracticeContext(rawLocale);
  const questions = await loadReviewQuestions(conceptId, locale, cookie).catch(() => null);
  if (questions === null || questions.length === 0) {
    const t = await getTranslations({ locale, namespace: 'Practice.review' });
    return <p role="status">{t('nothingDue')}</p>;
  }
  return <ReviewSession locale={locale} conceptId={conceptId} questions={questions} />;
}
