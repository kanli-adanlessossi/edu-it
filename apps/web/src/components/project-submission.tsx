'use client';

// Project submission form (Req 19.5, 20): a rubric self-assessment covering
// every criterion plus at least one evidence item. Validated with Zod via
// `validateProjectForm` before submitting through the `ProjectApi` port.

import { useMemo, useState, type FormEvent } from 'react';
import { useTranslations } from 'next-intl';

import {
  PracticeApiError,
  validateProjectForm,
  type ProjectApi,
  type ProjectFormError,
  type ProjectSubmissionResult,
  type ProjectView,
} from '../practice.js';
import { createProjectApi } from '../practice-api.js';

interface ProjectSubmissionProps {
  readonly project: ProjectView;
  readonly locale: string;
  /** Override for tests; defaults to the generated-client adapter. */
  readonly api?: ProjectApi;
}

export function ProjectSubmission({ locale, project, api: apiOverride }: ProjectSubmissionProps) {
  const api = useMemo(() => apiOverride ?? createProjectApi(locale), [apiOverride, locale]);
  const t = useTranslations('Practice.project');
  const [points, setPoints] = useState<Record<string, string>>({});
  const [evidence, setEvidence] = useState<string[]>(['']);
  const [errors, setErrors] = useState<readonly ProjectFormError[]>([]);
  const [apiError, setApiError] = useState<string | null>(null);
  const [result, setResult] = useState<ProjectSubmissionResult | null>(null);
  const [busy, setBusy] = useState(false);

  const criterionError = (id: string) => errors.find((e) => e.criterionId === id);
  const evidenceError = errors.some((e) => e.code === 'evidenceRequired');

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setApiError(null);
    const validated = validateProjectForm(project, points, evidence);
    if (!validated.ok) {
      setErrors(validated.errors);
      return;
    }
    setErrors([]);
    setBusy(true);
    try {
      setResult(await api.submit(validated.input));
    } catch (e) {
      setApiError(e instanceof PracticeApiError && e.detail ? e.detail : t('genericError'));
    } finally {
      setBusy(false);
    }
  }

  if (result !== null) {
    return (
      <section aria-labelledby="project-title">
        <h1 id="project-title">{project.title}</h1>
        <p role="status">
          {result.status === 'pending' ? t('pending') : t('reviewed')}
          {result.reviewOutcome !== null ? ` ${result.reviewOutcome}` : ''}
        </p>
      </section>
    );
  }

  return (
    <section aria-labelledby="project-title">
      <h1 id="project-title">{project.title}</h1>
      <p>{project.description}</p>
      {apiError !== null ? <p role="alert">{apiError}</p> : null}
      <form onSubmit={submit} noValidate>
        <fieldset>
          <legend>{t('rubricLegend')}</legend>
          {project.rubric.map((criterion) => {
            const id = `criterion-${criterion.stableId}`;
            const err = criterionError(criterion.stableId);
            return (
              <div key={criterion.stableId}>
                <label htmlFor={id}>
                  {t('criterionLabel', { title: criterion.title, max: criterion.maxPoints })}
                </label>
                <input
                  id={id}
                  type="number"
                  inputMode="numeric"
                  min={0}
                  max={criterion.maxPoints}
                  value={points[criterion.stableId] ?? ''}
                  aria-invalid={err !== undefined}
                  aria-describedby={err ? `${id}-error` : undefined}
                  onChange={(e) =>
                    setPoints((prev) => ({ ...prev, [criterion.stableId]: e.target.value }))
                  }
                />
                {err ? (
                  <p id={`${id}-error`} role="alert">
                    {err.code === 'criterionMissing'
                      ? t('criterionMissing')
                      : t('criterionOutOfRange', { max: criterion.maxPoints })}
                  </p>
                ) : null}
              </div>
            );
          })}
        </fieldset>
        <fieldset>
          <legend>{t('evidenceLegend')}</legend>
          {evidence.map((item, index) => {
            const id = `evidence-${index}`;
            return (
              <div key={index}>
                <label htmlFor={id}>{t('evidenceLabel', { number: index + 1 })}</label>
                <input
                  id={id}
                  type="text"
                  value={item}
                  required
                  aria-invalid={evidenceError && index === 0}
                  aria-describedby={evidenceError && index === 0 ? 'evidence-error' : undefined}
                  onChange={(e) =>
                    setEvidence((prev) => prev.map((v, i) => (i === index ? e.target.value : v)))
                  }
                />
              </div>
            );
          })}
          {evidenceError ? (
            <p id="evidence-error" role="alert">
              {t('evidenceRequired')}
            </p>
          ) : null}
          <button type="button" onClick={() => setEvidence((prev) => [...prev, ''])}>
            {t('addEvidence')}
          </button>
        </fieldset>
        <button type="submit" disabled={busy}>
          {t('submit')}
        </button>
      </form>
    </section>
  );
}
