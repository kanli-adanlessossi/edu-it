// Email-verification page under `/fr/verify-email` and `/en/verify-email`
// (Req 17.1). It tells the User a verification link was sent and lets them
// re-request it. The resend submits exclusively through the generated client
// via the `AuthApi` adapter (Req 17.2); on failure the email is preserved and
// an error shown (Req 17.3).

import type { Metadata } from 'next';
import { setRequestLocale } from 'next-intl/server';
import { notFound } from 'next/navigation';

import { isLocale } from '../../../locale.js';
import { pageMetadata } from '../page-metadata.js';
import { VerifyEmailResend } from './verify-email-resend.js';

interface VerifyEmailPageProps {
  params: Promise<{ locale: string }>;
}

export function generateMetadata({ params }: VerifyEmailPageProps): Promise<Metadata> {
  return pageMetadata(params, 'verifyEmail');
}

export default async function VerifyEmailPage({ params }: VerifyEmailPageProps) {
  const { locale } = await params;
  if (!isLocale(locale)) {
    notFound();
  }
  setRequestLocale(locale);
  return <VerifyEmailResend locale={locale} />;
}
