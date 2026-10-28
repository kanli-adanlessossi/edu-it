// Sign-in page under `/fr/sign-in` and `/en/sign-in` (Req 17.1). It submits
// exclusively through the generated client via the `AuthApi` adapter (Req 17.2);
// on failure the email is preserved, the password cleared, and an error shown
// (Req 17.3). It is also the page blocked dashboard visitors are redirected to
// (Req 17.4).

import type { Metadata } from 'next';
import { setRequestLocale } from 'next-intl/server';
import { notFound } from 'next/navigation';

import { isLocale } from '../../../locale.js';
import { pageMetadata } from '../page-metadata.js';
import { SignInForm } from './sign-in-form.js';

interface SignInPageProps {
  params: Promise<{ locale: string }>;
}

export function generateMetadata({ params }: SignInPageProps): Promise<Metadata> {
  return pageMetadata(params, 'signIn');
}

export default async function SignInPage({ params }: SignInPageProps) {
  const { locale } = await params;
  if (!isLocale(locale)) {
    notFound();
  }
  setRequestLocale(locale);
  return <SignInForm locale={locale} />;
}
