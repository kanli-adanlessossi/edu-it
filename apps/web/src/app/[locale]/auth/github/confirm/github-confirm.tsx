'use client';

// Client half of the GitHub link-confirmation page. Confirm posts to
// `POST /auth/github/confirm` and navigates to the dashboard on the new Session;
// decline posts to `POST /auth/github/decline` and shows a neutral message.
// Both go through the generated client (Req 14.9).

import { useRouter } from 'next/navigation';
import { useMemo, useState } from 'react';
import { useTranslations } from 'next-intl';

import { createAuthApi } from '../../../../../api-client.js';
import { Link } from '../../../../../i18n/navigation.js';
import { type Locale } from '../../../../../locale.js';

interface GitHubConfirmProps {
  readonly locale: Locale;
}

type ConfirmState = 'idle' | 'working' | 'declined' | 'error';

export function GitHubConfirm({ locale }: GitHubConfirmProps) {
  const t = useTranslations('Auth.githubConfirm');
  const api = useMemo(() => createAuthApi(locale), [locale]);
  const router = useRouter();
  const [state, setState] = useState<ConfirmState>('idle');

  async function confirm() {
    setState('working');
    try {
      const { redirectTo } = await api.confirmGitHubLink();
      router.push(`/${locale}${redirectTo ?? '/dashboard'}`);
    } catch {
      setState('error');
    }
  }

  async function decline() {
    setState('working');
    try {
      await api.declineGitHubLink();
      setState('declined');
    } catch {
      setState('error');
    }
  }

  if (state === 'declined') {
    return (
      <section>
        <h1>{t('title')}</h1>
        <p role="status">{t('declined')}</p>
        <p>
          <Link href="/sign-in">{t('signInLink')}</Link>
        </p>
      </section>
    );
  }

  return (
    <section>
      <h1>{t('title')}</h1>
      {state === 'error' ? <p role="alert">{t('error')}</p> : null}
      <p>{t('instructions')}</p>
      <button type="button" onClick={confirm} disabled={state === 'working'}>
        {t('confirm')}
      </button>
      <button type="button" onClick={decline} disabled={state === 'working'}>
        {t('decline')}
      </button>
      {state === 'error' ? (
        <p>
          <Link href="/sign-in">{t('signInLink')}</Link>
        </p>
      ) : null}
    </section>
  );
}
