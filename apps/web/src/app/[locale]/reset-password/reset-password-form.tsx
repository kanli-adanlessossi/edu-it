'use client';

// Client half of the password-reset page. It wires the generated-client
// `AuthApi` adapter to the shared `AuthForm` and renders surrounding copy from
// next-intl keys (Req 17.2, 17.3, 15.7). This is the reset-request step: the
// User enters their email and the API enqueues a reset link (Req 9.1).

import { useMemo } from 'react';
import { useTranslations } from 'next-intl';

import { createAuthApi } from '../../../api-client.js';
import { AuthForm, type AuthFormField } from '../../../components/auth-form.js';
import { type Locale } from '../../../locale.js';

interface ResetPasswordFormProps {
  readonly locale: Locale;
}

export function ResetPasswordForm({ locale }: ResetPasswordFormProps) {
  const t = useTranslations('Auth');
  const api = useMemo(() => createAuthApi(locale), [locale]);

  const fields: AuthFormField[] = [
    {
      name: 'email',
      label: t('common.emailLabel'),
      type: 'email',
      required: true,
      autoComplete: 'email',
    },
  ];

  return (
    <section>
      <h1>{t('resetPassword.title')}</h1>
      <p>{t('resetPassword.requestInstructions')}</p>
      <AuthForm
        page="reset-password"
        api={api}
        fields={fields}
        submitLabel={t('resetPassword.submit')}
      />
    </section>
  );
}
