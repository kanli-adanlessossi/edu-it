// Quiz player page under `/fr/quizzes/[quizId]` and `/en/quizzes/[quizId]` (Req 19.4, 19.6).

import type { Metadata } from 'next';
import { getTranslations } from 'next-intl/server';

import { QuizPlayer } from '../../../../components/quiz-player.js';
import { loadQuiz } from '../../../../practice-api.js';
import { practiceMetadata, requirePracticeContext } from '../../practice-page.js';

export const dynamic = 'force-dynamic';

interface QuizPageProps {
  params: Promise<{ locale: string; quizId: string }>;
}

export function generateMetadata({ params }: QuizPageProps): Promise<Metadata> {
  return practiceMetadata(params, 'quiz');
}

export default async function QuizPage({ params }: QuizPageProps) {
  const { locale: rawLocale, quizId } = await params;
  const { locale, cookie } = await requirePracticeContext(rawLocale);
  const quiz = await loadQuiz(quizId, locale, cookie).catch(() => null);
  if (quiz === null) {
    const t = await getTranslations({ locale, namespace: 'Practice.quiz' });
    return <p role="alert">{t('unavailable')}</p>;
  }
  return <QuizPlayer locale={locale} quiz={quiz} />;
}
