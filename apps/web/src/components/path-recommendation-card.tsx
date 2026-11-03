'use client';

// Dashboard card for the latest path recommendation (ai-content Req 17.7, 17.8).
//
// An ordered list of the recommended concepts, a note on where the suggestion comes from,
// and a "Dismiss" button. Dismissal goes through the `PathRecommendationApi` port; once the
// API confirms, the card is replaced by a short status message that receives focus so a
// keyboard or screen reader user is not left on a button that no longer exists. A failed
// dismissal keeps the card and announces an error. All copy comes from next-intl keys.

import { useEffect, useMemo, useRef, useState, useId } from 'react';
import { useTranslations } from 'next-intl';

import {
  SOURCE_LLM,
  SOURCE_RULE_BASED,
  type PathRecommendation,
  type PathRecommendationApi,
} from '../path-recommendation.js';
import { createPathRecommendationApi } from '../path-recommendation-api.js';

export interface PathRecommendationCardProps {
  readonly locale: string;
  readonly recommendation: PathRecommendation;
  /** Override for tests; defaults to the generated-client adapter. */
  readonly api?: PathRecommendationApi;
}

export function PathRecommendationCard({
  locale,
  recommendation,
  api: apiOverride,
}: PathRecommendationCardProps) {
  const api = useMemo(
    () => apiOverride ?? createPathRecommendationApi(locale),
    [apiOverride, locale],
  );
  const t = useTranslations('PathRecommendation');
  const uid = useId();
  const headingId = `${uid}-heading`;
  const [phase, setPhase] = useState<'shown' | 'sending' | 'dismissed'>('shown');
  const [failed, setFailed] = useState(false);
  const statusRef = useRef<HTMLParagraphElement>(null);

  useEffect(() => {
    if (phase === 'dismissed') statusRef.current?.focus();
  }, [phase]);

  async function dismiss() {
    setPhase('sending');
    setFailed(false);
    try {
      await api.dismiss(recommendation.stable_id);
      setPhase('dismissed');
    } catch {
      setFailed(true);
      setPhase('shown');
    }
  }

  if (phase === 'dismissed') {
    return (
      <p ref={statusRef} role="status" tabIndex={-1}>
        {t('dismissed')}
      </p>
    );
  }

  const sourceNote =
    recommendation.source === SOURCE_LLM
      ? t('sourceLlm')
      : recommendation.source === SOURCE_RULE_BASED
        ? t('sourceRuleBased')
        : null;

  return (
    <section aria-labelledby={headingId}>
      <h2 id={headingId}>{t('heading')}</h2>
      <p>{t('intro')}</p>
      <ol>
        {recommendation.ordering.map((conceptId) => (
          <li key={conceptId}>{conceptId}</li>
        ))}
      </ol>
      {sourceNote !== null ? <p>{sourceNote}</p> : null}
      {failed ? <p role="alert">{t('dismissError')}</p> : null}
      <button type="button" onClick={dismiss} disabled={phase === 'sending'}>
        {t('dismiss')}
      </button>
    </section>
  );
}
