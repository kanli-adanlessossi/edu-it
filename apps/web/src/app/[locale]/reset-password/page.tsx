// Password-reset page under `/fr/reset-password` and `/en/reset-password`
// (Req 17.1). It submits exclusively through the generated client via the
// `AuthApi` adapter (Req 17.2); on failure the email is preserved, any password
// cleared, and an error shown (Req 17.3).

import type { Metadata } from 'next';
import { setRequestLocale } from 'next-intl/server';
import { notFound } from 'next/navigation';

import { isLocale } from '../../../locale.js';
import { pageMetadata } from '../page-metadata.js';
import { ResetPasswordForm } from './reset-password-form.js';

interface ResetPasswordPageProps {
  params: Promise<{ locale: string }>;
}

export function generateMetadata({ params }: ResetPasswordPageProps): Promise<Metadata> {
  return pageMetadata(params, 'resetPassword');
}

export default async function ResetPasswordPage({ params }: ResetPasswordPageProps) {
  const { locale } = await params;
  if (!isLocale(locale)) {
    notFound();
  }
  setRequestLocale(locale);
  return <ResetPasswordForm locale={locale} />;
}
