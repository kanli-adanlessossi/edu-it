'use client';

// Client half of the email-verification page. It shows the "check your email"
// instructions and a resend form wired to the generated-client `AuthApi`
// adapter (Req 17.2, 17.3, 15.7). Re-requesting the verification email is
// allowed (Req 24.3); on failure the email is preserved and an error shown.

import { useMemo } from 'react';
import { useTranslations } from 'next-intl';

import { createAuthApi } from '../../../api-client.js';
import { AuthForm, type AuthFormField } from '../../../components/auth-form.js';
import { type Locale } from '../../../locale.js';

interface VerifyEmailResendProps {
  readonly locale: Locale;
}

export function VerifyEmailResend({ locale }: VerifyEmailResendProps) {
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
      <h1>{t('verifyEmail.title')}</h1>
      <p>{t('verifyEmail.instructions')}</p>
      <AuthForm
        page="verify-email"
        api={api}
        fields={fields}
        submitLabel={t('verifyEmail.resend')}
      />
    </section>
  );
}
