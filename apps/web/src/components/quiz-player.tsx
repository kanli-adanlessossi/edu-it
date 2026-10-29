'use client';

// Quiz player with feedback and progressive hints (Req 19.4). Submission goes
// through the `QuizApi` port (generated client) with an Idempotency-Key that is
// stable per attempt so a retry replays rather than double-counts (Req 18.1).
// All copy comes from next-intl keys (Req 19.6).

import { useMemo, useRef, useState, type FormEvent } from 'react';
import { useTranslations } from 'next-intl';

import {
  PracticeApiError,
  buildSelections,
  canRevealHint,
  visibleHints,
  type QuizApi,
  type QuizAttemptResult,
  type QuizView,
} from '../practice.js';
import { createQuizApi } from '../practice-api.js';

interface QuizPlayerProps {
  readonly quiz: QuizView;
  readonly locale: string;
  /** Override for tests; defaults to the generated-client adapter. */
  readonly api?: QuizApi;
}

export function QuizPlayer({ locale, quiz, api: apiOverride }: QuizPlayerProps) {
  const api = useMemo(() => apiOverride ?? createQuizApi(locale), [apiOverride, locale]);
  const t = useTranslations('Practice.quiz');
  const [chosen, setChosen] = useState<Record<string, number>>({});
  const [hintLevels, setHintLevels] = useState<Record<string, number>>({});
  const [result, setResult] = useState<QuizAttemptResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const idempotencyKey = useRef<string>(crypto.randomUUID());

  async function revealHint(questionId: string) {
    setError(null);
    try {
      const hint = await api.revealHint(quiz.stableId, questionId);
      setHintLevels((prev) => ({ ...prev, [questionId]: hint.hintLevel }));
    } catch (e) {
      setError(e instanceof PracticeApiError && e.detail ? e.detail : t('genericError'));
    }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const selections = buildSelections(
        quiz.questions.map((q) => q.stableId),
        chosen,
      );
      setResult(
        await api.attempt(quiz.stableId, selections, {
          idempotencyKey: idempotencyKey.current,
          ...(quiz.contentVersion !== undefined ? { contentVersion: quiz.contentVersion } : {}),
        }),
      );
    } catch (e) {
      setError(e instanceof PracticeApiError && e.detail ? e.detail : t('genericError'));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-labelledby="quiz-title">
      <h1 id="quiz-title">{quiz.title}</h1>
      {error !== null ? <p role="alert">{error}</p> : null}
      <form onSubmit={submit}>
        {quiz.questions.map((question, index) => {
          const level = hintLevels[question.stableId] ?? 0;
          const shown = visibleHints(question.hints, level);
          const hintsId = `hints-${question.stableId}`;
          return (
            <fieldset key={question.stableId} disabled={result !== null}>
              <legend>{t('questionLabel', { number: index + 1, prompt: question.prompt })}</legend>
              {question.options.map((option) => {
                const id = `${question.stableId}-${option.optionId}`;
                return (
                  <div key={option.optionId}>
                    <input
                      type="radio"
                      id={id}
                      name={question.stableId}
                      checked={chosen[question.stableId] === option.optionId}
                      onChange={() =>
                        setChosen((prev) => ({ ...prev, [question.stableId]: option.optionId }))
                      }
                    />
                    <label htmlFor={id}>{option.text}</label>
                  </div>
                );
              })}
              {canRevealHint(question.hints, level) && result === null ? (
                <button
                  type="button"
                  onClick={() => void revealHint(question.stableId)}
                  aria-describedby={shown.length > 0 ? hintsId : undefined}
                >
                  {t('showHint', { level: level + 1 })}
                </button>
              ) : null}
              {shown.length > 0 ? (
                <ol id={hintsId} aria-live="polite" aria-label={t('hintsLabel')}>
                  {shown.map((hint, i) => (
                    <li key={i}>{hint}</li>
                  ))}
                </ol>
              ) : null}
              {result !== null ? (
                <p>
                  {result.countedForMastery[question.stableId] === false
                    ? t('notCounted')
                    : t('counted')}
                </p>
              ) : null}
            </fieldset>
          );
        })}
        {result === null ? (
          <button type="submit" disabled={busy}>
            {t('submit')}
          </button>
        ) : null}
      </form>
      {result !== null ? (
        <div role="status" aria-live="polite">
          <h2>{t('resultTitle')}</h2>
          <p>{t('score', { score: result.score })}</p>
          <p>{t('bestScore', { score: result.bestScore })}</p>
          {result.replayed ? <p>{t('replayed')}</p> : null}
        </div>
      ) : null}
    </section>
  );
}
