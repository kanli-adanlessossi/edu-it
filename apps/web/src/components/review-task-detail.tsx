'use client';

// Reviewer task detail (ai-content Req 15.2, 15.3, 15.9): the variant in French
// and English, its provenance and every finding, with the actions approve, edit,
// request changes and reject.

import { useMemo, useState, type FormEvent } from 'react';
import Link from 'next/link';
import { useFormatter, useTranslations } from 'next-intl';

import {
  BODY_LOCALES,
  COMMENT_MAX_CHARS,
  ReviewerApiError,
  buildEditedBody,
  isBodyChanged,
  isBodyComplete,
  validateComment,
  type BodyLocale,
  type DecisionAction,
  type ReviewTaskDetail,
  type ReviewerApi,
  type VariantBody,
} from '../review-tasks.js';
import { createReviewerApi } from '../review-tasks-api.js';

interface ReviewTaskDetailViewProps {
  readonly locale: string;
  readonly detail: ReviewTaskDetail;
  /** Override for tests; defaults to the generated-client adapter. */
  readonly api?: ReviewerApi;
}

type Outcome =
  | { readonly kind: 'decided'; readonly action: DecisionAction }
  | { readonly kind: 'edited'; readonly newTaskId: string };

export function ReviewTaskDetailView({
  locale,
  detail,
  api: apiOverride,
}: ReviewTaskDetailViewProps) {
  const api = useMemo(() => apiOverride ?? createReviewerApi(locale), [apiOverride, locale]);
  const t = useTranslations('Reviewer');
  const format = useFormatter();
  const { task, variant, revision } = detail;
  const original = revision?.body ?? null;

  const [comment, setComment] = useState('');
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<VariantBody | null>(original);
  const [error, setError] = useState<string | null>(null);
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const [busy, setBusy] = useState(false);

  const open = task.state === 'open' && outcome === null;

  function describe(e: unknown): string {
    if (e instanceof ReviewerApiError) {
      switch (e.kind) {
        case 'expertise':
          return t('errors.expertise');
        case 'alreadyDecided':
          return t('errors.alreadyDecided');
        case 'notFound':
          return t('errors.notFound');
        case 'invalid':
          return t('errors.invalid');
        case 'unauthenticated':
          return t('errors.unauthenticated');
        default:
          break;
      }
    }
    return t('errors.generic');
  }

  async function decide(action: DecisionAction) {
    const problem = validateComment(action, comment);
    if (problem !== null) {
      setError(
        problem === 'required'
          ? t('errors.commentRequired')
          : t('errors.commentTooLong', { max: COMMENT_MAX_CHARS }),
      );
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api.decide(action, task.stableId, comment);
      setOutcome({ kind: 'decided', action });
    } catch (e) {
      setError(describe(e));
    } finally {
      setBusy(false);
    }
  }

  async function saveEdit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (draft === null || original === null) return;
    if (!isBodyComplete(draft)) {
      setError(t('errors.editIncomplete'));
      return;
    }
    if (!isBodyChanged(original, draft)) {
      setError(t('errors.editUnchanged'));
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const created = await api.edit(task.stableId, buildEditedBody(draft));
      setOutcome({ kind: 'edited', newTaskId: created.stableId });
      setEditing(false);
    } catch (e) {
      setError(describe(e));
    } finally {
      setBusy(false);
    }
  }

  function patchDraft(update: (body: VariantBody) => VariantBody) {
    setDraft((prev) => (prev === null ? prev : update(prev)));
  }

  const fmt = (iso: string | null): string =>
    iso === null
      ? t('detail.none')
      : format.dateTime(new Date(iso), { dateStyle: 'medium', timeStyle: 'short' });

  return (
    <section aria-labelledby="reviewer-task-title">
      <p>
        <Link href={`/${locale}/reviewer`}>{t('detail.back')}</Link>
      </p>
      <h1 id="reviewer-task-title">{t('detail.title')}</h1>
      <p>
        {t('detail.taskState', {
          state: t.has(`state.${task.state}`) ? t(`state.${task.state}`) : task.state,
        })}
      </p>
      {task.reason !== null ? <p>{t('detail.reason', { reason: task.reason })}</p> : null}
      {error !== null ? <p role="alert">{error}</p> : null}
      {outcome !== null ? (
        <div role="status" aria-live="polite">
          {outcome.kind === 'decided' ? (
            <p>{t(`outcome.${outcome.action}`)}</p>
          ) : (
            <p>
              {t('outcome.edit')}{' '}
              <Link href={`/${locale}/reviewer/${encodeURIComponent(outcome.newTaskId)}`}>
                {t('outcome.openNewTask')}
              </Link>
            </p>
          )}
        </div>
      ) : null}

      {revision === null || original === null ? (
        <p role="status">{t('detail.noRevision')}</p>
      ) : (
        <>
          <h2>{t('detail.variant')}</h2>
          {variant !== null ? <p>{t('detail.parent', { id: variant.parentQuestionId })}</p> : null}
          {editing && draft !== null ? (
            <form onSubmit={(e) => void saveEdit(e)}>
              {BODY_LOCALES.map((l) => (
                <fieldset key={l}>
                  <legend>{t(`locale.${l}`)}</legend>
                  <EditField
                    id={`prompt-${l}`}
                    label={t('detail.prompt')}
                    lang={l}
                    value={draft.prompt[l]}
                    onChange={(value) =>
                      patchDraft((b) => ({ ...b, prompt: { ...b.prompt, [l]: value } }))
                    }
                  />
                  {draft.options.map((option, index) => (
                    <EditField
                      key={index}
                      id={`option-${index}-${l}`}
                      label={t('detail.optionLabel', { number: index + 1 })}
                      lang={l}
                      value={option.text[l]}
                      onChange={(value) =>
                        patchDraft((b) => ({
                          ...b,
                          options: b.options.map((o, i) =>
                            i === index ? { ...o, text: { ...o.text, [l]: value } } : o,
                          ),
                        }))
                      }
                    />
                  ))}
                  <EditField
                    id={`explanation-${l}`}
                    label={t('detail.explanation')}
                    lang={l}
                    value={draft.explanation[l]}
                    onChange={(value) =>
                      patchDraft((b) => ({ ...b, explanation: { ...b.explanation, [l]: value } }))
                    }
                  />
                </fieldset>
              ))}
              <button type="submit" disabled={busy}>
                {t('actions.saveEdit')}
              </button>{' '}
              <button
                type="button"
                disabled={busy}
                onClick={() => {
                  setEditing(false);
                  setDraft(original);
                  setError(null);
                }}
              >
                {t('actions.cancelEdit')}
              </button>
            </form>
          ) : (
            BODY_LOCALES.map((l) => (
              <section key={l} lang={l} aria-labelledby={`variant-${l}`}>
                <h3 id={`variant-${l}`}>{t(`locale.${l}`)}</h3>
                <p>{original.prompt[l]}</p>
                <ol aria-label={t('detail.options')}>
                  {original.options.map((option, index) => (
                    <li key={index}>
                      {option.text[l]}
                      {option.isCorrect ? ` (${t('detail.correct')})` : ''}
                    </li>
                  ))}
                </ol>
                <p>{t('detail.explanationLine', { text: original.explanation[l] })}</p>
              </section>
            ))
          )}

          <h2>{t('provenance.title')}</h2>
          <dl>
            <dt>{t('provenance.origin')}</dt>
            <dd>{revision.origin ?? t('detail.none')}</dd>
            <dt>{t('provenance.model')}</dt>
            <dd>{revision.model ?? t('detail.none')}</dd>
            <dt>{t('provenance.prompt')}</dt>
            <dd>
              {revision.promptId === null
                ? t('detail.none')
                : `${revision.promptId} v${revision.promptVersion ?? ''}`}
            </dd>
            <dt>{t('provenance.generatedAt')}</dt>
            <dd>{fmt(revision.generationDate)}</dd>
            <dt>{t('provenance.reviewedBy')}</dt>
            <dd>{revision.reviewedBy ?? t('detail.none')}</dd>
            <dt>{t('provenance.reviewedAt')}</dt>
            <dd>{fmt(revision.reviewedAt)}</dd>
            <dt>{t('provenance.contentVersion')}</dt>
            <dd>{revision.contentVersion}</dd>
          </dl>

          <h2>{t('findings.title')}</h2>
          {revision.findings.length === 0 ? (
            <p>{t('findings.none')}</p>
          ) : (
            <ul aria-label={t('findings.title')}>
              {revision.findings.map((f, index) => (
                <li key={`${f.kind}-${f.target}-${index}`}>
                  <strong>
                    {t.has(`findings.kind.${f.kind}`) ? t(`findings.kind.${f.kind}`) : f.kind}
                  </strong>
                  {f.target.length > 0 ? ` (${f.target})` : ''}
                  {f.detail.length > 0 ? `: ${f.detail}` : ''}
                </li>
              ))}
            </ul>
          )}
        </>
      )}

      {open && !editing ? (
        <div>
          <h2>{t('actions.title')}</h2>
          <label htmlFor="review-comment">{t('actions.commentLabel')}</label>
          <br />
          <textarea
            id="review-comment"
            value={comment}
            maxLength={COMMENT_MAX_CHARS}
            rows={4}
            onChange={(e) => setComment(e.target.value)}
          />
          <p>
            <button type="button" disabled={busy} onClick={() => void decide('approve')}>
              {t('actions.approve')}
            </button>{' '}
            <button
              type="button"
              disabled={busy || original === null}
              onClick={() => {
                setEditing(true);
                setError(null);
              }}
            >
              {t('actions.edit')}
            </button>{' '}
            <button type="button" disabled={busy} onClick={() => void decide('requestChanges')}>
              {t('actions.requestChanges')}
            </button>{' '}
            <button type="button" disabled={busy} onClick={() => void decide('reject')}>
              {t('actions.reject')}
            </button>
          </p>
        </div>
      ) : null}
    </section>
  );
}

interface EditFieldProps {
  readonly id: string;
  readonly label: string;
  readonly lang: BodyLocale;
  readonly value: string;
  readonly onChange: (value: string) => void;
}

function EditField({ id, label, lang, value, onChange }: EditFieldProps) {
  return (
    <div>
      <label htmlFor={id}>{label}</label>
      <br />
      <textarea
        id={id}
        lang={lang}
        rows={3}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
    </div>
  );
}
