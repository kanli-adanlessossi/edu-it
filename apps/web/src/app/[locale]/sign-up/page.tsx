// Sign-up page under `/fr/sign-up` and `/en/sign-up` (Req 17.1). It collects an
// email and password and submits exclusively through the generated client via
// the `AuthApi` adapter (Req 17.2); on failure the client form preserves the
// email, clears the password, and shows the error (Req 17.3).

import type { Metadata } from 'next';
import { setRequestLocale } from 'next-intl/server';
import { notFound } from 'next/navigation';

import { isLocale } from '../../../locale.js';
import { pageMetadata } from '../page-metadata.js';
import { SignUpForm } from './sign-up-form.js';

interface SignUpPageProps {
  params: Promise<{ locale: string }>;
}

export function generateMetadata({ params }: SignUpPageProps): Promise<Metadata> {
  return pageMetadata(params, 'signUp');
}

export default async function SignUpPage({ params }: SignUpPageProps) {
  const { locale } = await params;
  if (!isLocale(locale)) {
    notFound();
  }
  setRequestLocale(locale);
  return <SignUpForm locale={locale} />;
}
