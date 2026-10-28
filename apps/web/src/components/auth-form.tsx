'use client';

// Client-side auth form used by the sign-up, sign-in, and password-reset pages
// (Req 17.2, 17.3). It renders the supplied fields, submits EXCLUSIVELY through
// the generated client (adapted to the `AuthApi` port), and on a failed
// submission re-renders the page with non-password values preserved, password
// fields cleared, and a learner-facing error indication shown (Req 17.3).
//
// The field-preservation decision is the pure `submitAuthForm` algebra from
// `../auth-forms.ts` (targeted by Property 24); this component only wires that
// algebra to React state and the DOM. All user-facing copy comes from next-intl
// keys, never JSX literals (Req 15.7).

import { useState, type FormEvent } from 'react';
import { useTranslations } from 'next-intl';

import { submitAuthForm, type AuthApi, type AuthFormFields, type AuthPage } from '../auth-forms.js';

/** One rendered field of an auth form. */
export interface AuthFormField {
  /** The field name; must match the API's expected field name. */
  readonly name: string;
  /** Translated label for the field. */
  readonly label: string;
  /** Input type; `password` fields are cleared on a failed submission. */
  readonly type: 'email' | 'password' | 'text';
  /** Whether the browser should require a value before submission. */
  readonly required?: boolean;
  /** Autocomplete hint for assistive tech and password managers. */
  readonly autoComplete?: string;
}

interface AuthFormProps {
  /** The auth page this form submits to (Req 17.2). */
  readonly page: AuthPage;
  /** The API port the generated client satisfies; submissions go here only. */
  readonly api: AuthApi;
  /** The fields to render, in order. */
  readonly fields: readonly AuthFormField[];
  /** Translated label for the submit button. */
  readonly submitLabel: string;
  /** Called with the redirect target when the API accepts the submission. */
  readonly onSuccess?: (redirectTo?: string) => void;
}

export function AuthForm({ page, api, fields, submitLabel, onSuccess }: AuthFormProps) {
  const t = useTranslations('Auth.common');
  const [values, setValues] = useState<AuthFormFields>(() =>
    Object.fromEntries(fields.map((field) => [field.name, ''])),
  );
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);

    const result = await submitAuthForm(api, page, values, t('genericError'));

    if (result.status === 'success') {
      onSuccess?.(result.redirectTo);
      setSubmitting(false);
      return;
    }

    // Failed submission: preserve non-password values, clear passwords, show
    // the error indication (Req 17.3). `result.fields` already reflects this.
    setValues(result.fields);
    setError(result.error);
    setSubmitting(false);
  }

  const errorId = `${page}-error`;

  return (
    <form onSubmit={handleSubmit} noValidate aria-describedby={error ? errorId : undefined}>
      {error !== null ? (
        <p id={errorId} role="alert">
          {error}
        </p>
      ) : null}
      {fields.map((field) => {
        const fieldId = `${page}-${field.name}`;
        return (
          <div key={field.name}>
            <label htmlFor={fieldId}>{field.label}</label>
            <input
              id={fieldId}
              name={field.name}
              type={field.type}
              required={field.required}
              autoComplete={field.autoComplete}
              value={values[field.name] ?? ''}
              onChange={(event) =>
                setValues((prev) => ({ ...prev, [field.name]: event.target.value }))
              }
            />
          </div>
        );
      })}
      <button type="submit" disabled={submitting}>
        {submitLabel}
      </button>
    </form>
  );
}
