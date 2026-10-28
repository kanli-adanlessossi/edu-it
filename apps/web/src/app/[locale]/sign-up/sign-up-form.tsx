'use client';

// Client half of the sign-up page. It wires the generated-client `AuthApi`
// adapter to the shared `AuthForm` and renders the surrounding copy from
// next-intl keys (Req 17.2, 17.3, 15.7).

import { useMemo } from 'react';
import { useTranslations } from 'next-intl';

import { createAuthApi } from '../../../api-client.js';
import { AuthForm, type AuthFormField } from '../../../components/auth-form.js';
import { type Locale } from '../../../locale.js';

interface SignUpFormProps {
  readonly locale: Locale;
}

export function SignUpForm({ locale }: SignUpFormProps) {
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
    {
      name: 'password',
      label: t('common.passwordLabel'),
      type: 'password',
      required: true,
      autoComplete: 'new-password',
    },
  ];

  return (
    <section>
      <h1>{t('signUp.title')}</h1>
      <AuthForm page="sign-up" api={api} fields={fields} submitLabel={t('signUp.submit')} />
    </section>
  );
}
