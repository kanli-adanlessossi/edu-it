'use client';

// Client half of the password-reset page. It wires the generated-client
// `AuthApi` adapter to the shared `AuthForm` and renders surrounding copy from
// next-intl keys (Req 17.2, 17.3, 15.7). Without a `token` it shows the
// reset-request step (Req 9.1); with a `token` from the emailed link it shows
// the new-password step, which posts the token and the new password and then
// navigates to the dashboard on the new Session (api-route-wiring Req 14.7).

import { useRouter } from 'next/navigation';
import { useMemo, useState } from 'react';
import { useTranslations } from 'next-intl';

import { createAuthApi } from '../../../api-client.js';
import type { AuthApi, AuthFormFields } from '../../../auth-forms.js';
import { AuthForm, type AuthFormField } from '../../../components/auth-form.js';
import { Link } from '../../../i18n/navigation.js';
import { type Locale } from '../../../locale.js';

interface ResetPasswordFormProps {
  readonly locale: Locale;
  /** The reset token from the emailed link, when the page was opened from it. */
  readonly token?: string;
}

export function ResetPasswordForm({ locale, token }: ResetPasswordFormProps) {
  const t = useTranslations('Auth');
  const api = useMemo(() => createAuthApi(locale), [locale]);
  const router = useRouter();
  const [linkInvalid, setLinkInvalid] = useState(false);
  const hasToken = token !== undefined && token.length > 0;

  // Adapt the completion call to the `AuthApi` port so the shared form drives it.
  const completeApi = useMemo<AuthApi>(
    () => ({
      async submit(_page, fields: AuthFormFields) {
        const outcome = await api.completePasswordReset(token ?? '', fields.newPassword ?? '');
        if (outcome.status === 'invalid') {
          setLinkInvalid(true);
          return {};
        }
        return { redirectTo: outcome.redirectTo };
      },
    }),
    [api, token],
  );

  if (hasToken && linkInvalid) {
    return (
      <section>
        <h1>{t('resetPassword.invalidTitle')}</h1>
        <p role="alert">{t('resetPassword.invalid')}</p>
        <p>
          <Link href="/reset-password">{t('resetPassword.requestNew')}</Link>
        </p>
      </section>
    );
  }

  if (hasToken) {
    const newPasswordFields: AuthFormField[] = [
      {
        name: 'newPassword',
        label: t('common.newPasswordLabel'),
        type: 'password',
        required: true,
        autoComplete: 'new-password',
      },
    ];
    return (
      <section>
        <h1>{t('resetPassword.title')}</h1>
        <p>{t('resetPassword.setNewInstructions')}</p>
        <AuthForm
          page="reset-password"
          api={completeApi}
          fields={newPasswordFields}
          submitLabel={t('resetPassword.setNewSubmit')}
          onSuccess={(redirectTo) => {
            // A rejected link leaves the form in place until the invalid state renders.
            if (redirectTo !== undefined) router.push(`/${locale}${redirectTo}`);
          }}
        />
      </section>
    );
  }

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
