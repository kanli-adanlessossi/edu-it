'use client';

// "Report a problem" control for question, lesson and lab views (ai-content Req 18.1).
//
// A disclosure button opens a small form: a required category and an optional
// comment. The report goes through the `ProblemReportApi` port (generated client)
// with an Idempotency-Key that stays the same across retries of one report, so a
// retry replays instead of duplicating. The comment is never echoed back. All copy
// comes from next-intl keys. Stable accessible names for tests: the button
// "Report a problem" / "Signaler un problème", the form (aria-label), the combobox
// labelled "What is the problem?", the textbox labelled "Details (optional)".

import { useMemo, useRef, useState, useId, type FormEvent } from 'react';
import { useTranslations } from 'next-intl';

import {
  PROBLEM_REPORT_CATEGORIES,
  PROBLEM_REPORT_COMMENT_MAX_CHARS,
  ProblemReportApiError,
  isProblemReportCategory,
  normaliseComment,
  type ProblemReportApi,
  type ProblemReportItemKind,
} from '../problem-report.js';
import { createProblemReportApi } from '../problem-report-api.js';

export interface ReportProblemProps {
  readonly locale: string;
  readonly itemKind: ProblemReportItemKind;
  readonly itemId: string;
  /** The version the learner is looking at; without it nothing can be reported. */
  readonly contentVersion: string | undefined;
  /** 1-based position, used to tell several questions on one page apart. */
  readonly number?: number;
  /** Override for tests; defaults to the generated-client adapter. */
  readonly api?: ProblemReportApi;
}

type Phase = 'closed' | 'editing' | 'sending' | 'sent';

/** Renders nothing until the viewed content version is known (nothing can be reported without it). */
export function ReportProblem(props: ReportProblemProps) {
  const { contentVersion } = props;
  if (contentVersion === undefined || contentVersion === '') return null;
  return <ReportProblemControl {...props} contentVersion={contentVersion} />;
}

function ReportProblemControl({
  locale,
  itemKind,
  itemId,
  contentVersion,
  number,
  api: apiOverride,
}: ReportProblemProps & { readonly contentVersion: string }) {
  const api = useMemo(() => apiOverride ?? createProblemReportApi(locale), [apiOverride, locale]);
  const t = useTranslations('ProblemReport');
  const uid = useId();
  const [phase, setPhase] = useState<Phase>('closed');
  const [category, setCategory] = useState('');
  const [comment, setComment] = useState('');
  const [message, setMessage] = useState<{ kind: 'error' | 'sent'; text: string } | null>(null);
  const idempotencyKey = useRef<string>(crypto.randomUUID());
  const openRef = useRef<HTMLButtonElement>(null);

  const formId = `${uid}-form`;
  const categoryId = `${uid}-category`;
  const commentId = `${uid}-comment`;
  const hintId = `${uid}-hint`;
  const errorId = `${uid}-error`;
  const item =
    itemKind === 'question'
      ? t('itemQuestion', { number: number ?? 1 })
      : itemKind === 'lesson'
        ? t('itemLesson')
        : t('itemLab');
  const busy = phase === 'sending';
  const categoryMissing = message?.kind === 'error' && category === '';

  function close() {
    setPhase('closed');
    setMessage(null);
    // Return focus to the control that opened the form.
    queueMicrotask(() => openRef.current?.focus());
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!isProblemReportCategory(category)) {
      setMessage({ kind: 'error', text: t('categoryRequired') });
      return;
    }
    const text = normaliseComment(comment);
    setPhase('sending');
    setMessage(null);
    try {
      const result = await api.submit(
        {
          itemKind,
          itemId,
          contentVersion,
          category,
          ...(text !== undefined ? { comment: text } : {}),
        },
        idempotencyKey.current,
      );
      setMessage({ kind: 'sent', text: result.replayed ? t('alreadySent') : t('sent') });
      setPhase('sent');
      setCategory('');
      setComment('');
      // A later report is a new report, so it gets a new key.
      idempotencyKey.current = crypto.randomUUID();
    } catch (e) {
      setMessage({
        kind: 'error',
        text: e instanceof ProblemReportApiError && e.message ? e.message : t('genericError'),
      });
      setPhase('editing');
    }
  }

  return (
    <div>
      {phase === 'closed' || phase === 'sent' ? (
        <button
          ref={openRef}
          type="button"
          aria-label={itemKind === 'question' ? t('formLabel', { item }) : undefined}
          aria-expanded={false}
          aria-controls={formId}
          onClick={() => {
            setMessage(null);
            setPhase('editing');
          }}
        >
          {t('open')}
        </button>
      ) : null}
      {phase === 'sent' && message?.kind === 'sent' ? (
        <p role="status" aria-live="polite">
          {message.text}
        </p>
      ) : null}
      {phase === 'editing' || phase === 'sending' ? (
        <form id={formId} aria-label={t('formLabel', { item })} onSubmit={submit} noValidate>
          <div>
            <label htmlFor={categoryId}>{t('categoryLabel')}</label>
            <select
              id={categoryId}
              value={category}
              required
              aria-invalid={categoryMissing}
              aria-describedby={message?.kind === 'error' ? errorId : undefined}
              onChange={(e) => setCategory(e.target.value)}
            >
              <option value="">{t('categoryPlaceholder')}</option>
              {PROBLEM_REPORT_CATEGORIES.map((value) => (
                <option key={value} value={value}>
                  {t(`category.${value}`)}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label htmlFor={commentId}>{t('commentLabel')}</label>
            <textarea
              id={commentId}
              value={comment}
              maxLength={PROBLEM_REPORT_COMMENT_MAX_CHARS}
              aria-describedby={hintId}
              onChange={(e) => setComment(e.target.value)}
            />
            <p id={hintId}>{t('commentHint', { max: PROBLEM_REPORT_COMMENT_MAX_CHARS })}</p>
          </div>
          {message?.kind === 'error' ? (
            <p id={errorId} role="alert">
              {message.text}
            </p>
          ) : null}
          <button type="submit" disabled={busy}>
            {t('submit')}
          </button>
          <button type="button" onClick={close} disabled={busy}>
            {t('cancel')}
          </button>
        </form>
      ) : null}
    </div>
  );
}
