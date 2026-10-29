'use client';

// Review session page body (Req 19.5): answers the concept's due review
// questions and shows the pass/fail outcome and next due date (Req 18.4).

import { useMemo, useState, type FormEvent } from 'react';
import { useFormatter, useTranslations } from 'next-intl';

import {
  PracticeApiError,
  buildSelections,
  type ReviewApi,
  type ReviewQuestionView,
  type ReviewResult,
} from '../practice.js';
import { createReviewApi } from '../practice-api.js';

interface ReviewSessionProps {
  readonly conceptId: string;
  readonly questions: readonly ReviewQuestionView[];
  readonly locale: string;
  /** Override for tests; defaults to the generated-client adapter. */
  readonly api?: ReviewApi;
}

export function ReviewSession({
  locale,
  conceptId,
  questions,
  api: apiOverride,
}: ReviewSessionProps) {
  const api = useMemo(() => apiOverride ?? createReviewApi(locale), [apiOverride, locale]);
  const t = useTranslations('Practice.review');
  const format = useFormatter();
  const [chosen, setChosen] = useState<Record<string, number>>({});
  const [result, setResult] = useState<ReviewResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      setResult(
        await api.submit(
          conceptId,
          buildSelections(
            questions.map((q) => q.stableId),
            chosen,
          ),
        ),
      );
    } catch (e) {
      setError(e instanceof PracticeApiError && e.detail ? e.detail : t('genericError'));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-labelledby="review-title">
      <h1 id="review-title">{t('title')}</h1>
      {error !== null ? <p role="alert">{error}</p> : null}
      <form onSubmit={submit}>
        {questions.map((question, index) => (
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
          </fieldset>
        ))}
        {result === null ? (
          <button type="submit" disabled={busy}>
            {t('submit')}
          </button>
        ) : null}
      </form>
      {result !== null ? (
        <div role="status" aria-live="polite">
          <h2>{result.passed ? t('passed') : t('failed')}</h2>
          <p>{t('score', { score: result.score })}</p>
          <p>
            {t('nextDue', {
              date: format.dateTime(new Date(result.dueAt), { dateStyle: 'long' }),
            })}
          </p>
        </div>
      ) : null}
    </section>
  );
}
