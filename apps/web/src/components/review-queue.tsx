'use client';

// Reviewer queue list (ai-content Req 15.1, 15.9, 15.10): the open tasks of the
// reviewer's track, oldest first, with a cursor-paginated "load more".

import { useMemo, useState } from 'react';
import Link from 'next/link';
import { useFormatter, useTranslations } from 'next-intl';

import { ReviewerApiError, type ReviewTaskPage, type ReviewerApi } from '../review-tasks.js';
import { createReviewerApi } from '../review-tasks-api.js';

interface ReviewQueueProps {
  readonly locale: string;
  readonly initial: ReviewTaskPage;
  /** Override for tests; defaults to the generated-client adapter. */
  readonly api?: ReviewerApi;
}

export function ReviewQueue({ locale, initial, api: apiOverride }: ReviewQueueProps) {
  const api = useMemo(() => apiOverride ?? createReviewerApi(locale), [apiOverride, locale]);
  const t = useTranslations('Reviewer');
  const format = useFormatter();
  const [items, setItems] = useState(initial.items);
  const [cursor, setCursor] = useState(initial.nextCursor);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function loadMore() {
    if (cursor === null) return;
    setBusy(true);
    setError(null);
    try {
      const page = await api.list(cursor);
      setItems((prev) => [...prev, ...page.items]);
      setCursor(page.nextCursor);
    } catch (e) {
      setError(
        e instanceof ReviewerApiError && e.kind === 'expertise'
          ? t('errors.expertise')
          : t('errors.generic'),
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-labelledby="reviewer-queue-title">
      <h1 id="reviewer-queue-title">{t('queue.title')}</h1>
      {error !== null ? <p role="alert">{error}</p> : null}
      {items.length === 0 ? (
        <p role="status">{t('queue.empty')}</p>
      ) : (
        <ul aria-label={t('queue.listLabel')}>
          {items.map((task) => (
            <li key={task.stableId}>
              <Link href={`/${locale}/reviewer/${encodeURIComponent(task.stableId)}`}>
                {t('queue.open', { id: task.targetStableId })}
              </Link>{' '}
              <span>{t.has(`kind.${task.kind}`) ? t(`kind.${task.kind}`) : task.kind}</span>{' '}
              <span>{t('queue.track', { track: task.trackId })}</span>{' '}
              <time dateTime={task.createdAt}>
                {format.dateTime(new Date(task.createdAt), {
                  dateStyle: 'medium',
                  timeStyle: 'short',
                })}
              </time>
            </li>
          ))}
        </ul>
      )}
      {cursor !== null ? (
        <button type="button" onClick={() => void loadMore()} disabled={busy}>
          {t('queue.loadMore')}
        </button>
      ) : null}
    </section>
  );
}
