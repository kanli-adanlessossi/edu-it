'use client';

// Client half of the sign-in page. It wires the generated-client `AuthApi`
// adapter to the shared `AuthForm` and renders surrounding copy from next-intl
// keys (Req 17.2, 17.3, 15.7).

import { useMemo } from 'react';
import { useTranslations } from 'next-intl';

import { createAuthApi } from '../../../api-client.js';
import { AuthForm, type AuthFormField } from '../../../components/auth-form.js';
import { Link } from '../../../i18n/navigation.js';
import { type Locale } from '../../../locale.js';

interface SignInFormProps {
  readonly locale: Locale;
}

export function SignInForm({ locale }: SignInFormProps) {
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
      autoComplete: 'current-password',
    },
  ];

  return (
    <section>
      <h1>{t('signIn.title')}</h1>
      <AuthForm page="sign-in" api={api} fields={fields} submitLabel={t('signIn.submit')} />
      <p>
        <Link href="/reset-password">{t('signIn.forgotPassword')}</Link>
      </p>
      <p>
        <Link href="/sign-up">{t('signIn.needAccount')}</Link>
      </p>
    </section>
  );
}
