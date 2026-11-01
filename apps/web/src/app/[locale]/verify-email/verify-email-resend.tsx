'use client';

// Client half of the email-verification page. Without a `token` it shows the
// "check your email" instructions and a resend form. With a `token` (from the
// link in the email) it verifies once through the generated-client adapter and
// shows success, or the invalid-or-expired state together with the resend form
// (api-route-wiring Req 14.6, 14.8). Re-requesting the email is allowed
// (Req 24.3); on failure the email is preserved and an error shown.

import { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslations } from 'next-intl';

import { createAuthApi } from '../../../api-client.js';
import { AuthForm, type AuthFormField } from '../../../components/auth-form.js';
import { Link } from '../../../i18n/navigation.js';
import { type Locale } from '../../../locale.js';

interface VerifyEmailResendProps {
  readonly locale: Locale;
  /** The verification token from the emailed link, when the page was opened from it. */
  readonly token?: string;
}

type VerifyState = 'idle' | 'verifying' | 'verified' | 'invalid' | 'error' | 'resent';

export function VerifyEmailResend({ locale, token }: VerifyEmailResendProps) {
  const t = useTranslations('Auth');
  const api = useMemo(() => createAuthApi(locale), [locale]);
  const hasToken = token !== undefined && token.length > 0;
  const [state, setState] = useState<VerifyState>(hasToken ? 'verifying' : 'idle');
  // A token is single-use: guard against effect re-runs (e.g. Strict Mode).
  const started = useRef(false);

  useEffect(() => {
    if (!hasToken || started.current) return;
    started.current = true;
    api.verifyEmail(token).then(
      (outcome) => setState(outcome),
      () => setState('error'),
    );
  }, [api, hasToken, token]);

  const fields: AuthFormField[] = [
    {
      name: 'email',
      label: t('common.emailLabel'),
      type: 'email',
      required: true,
      autoComplete: 'email',
    },
  ];

  if (state === 'verifying') {
    return (
      <section>
        <h1>{t('verifyEmail.title')}</h1>
        <p role="status">{t('verifyEmail.verifying')}</p>
      </section>
    );
  }

  if (state === 'resent') {
    return (
      <section>
        <h1>{t('verifyEmail.title')}</h1>
        <p role="status">{t('verifyEmail.resent')}</p>
      </section>
    );
  }

  if (state === 'verified') {
    return (
      <section>
        <h1>{t('verifyEmail.successTitle')}</h1>
        <p role="status">{t('verifyEmail.success')}</p>
        <p>
          <Link href="/sign-in">{t('verifyEmail.signInLink')}</Link>
        </p>
      </section>
    );
  }

  const failed = state === 'invalid' || state === 'error';

  return (
    <section>
      <h1>{failed ? t('verifyEmail.invalidTitle') : t('verifyEmail.title')}</h1>
      {failed ? (
        <p role="alert">
          {state === 'invalid' ? t('verifyEmail.invalid') : t('verifyEmail.verifyError')}
        </p>
      ) : (
        <p>{t('verifyEmail.instructions')}</p>
      )}
      <AuthForm
        page="verify-email"
        api={api}
        fields={fields}
        submitLabel={t('verifyEmail.resend')}
        onSuccess={() => setState('resent')}
      />
    </section>
  );
}
