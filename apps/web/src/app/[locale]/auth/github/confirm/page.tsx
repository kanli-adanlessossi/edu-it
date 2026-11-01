// GitHub link-confirmation page under `/fr/auth/github/confirm` and
// `/en/auth/github/confirm` (api-route-wiring Req 14.9). The API callback
// redirects here when the GitHub email matches an existing account; the User
// confirms or declines through the generated client.

import type { Metadata } from 'next';
import { setRequestLocale } from 'next-intl/server';
import { notFound } from 'next/navigation';

import { isLocale } from '../../../../../locale.js';
import { pageMetadata } from '../../../page-metadata.js';
import { GitHubConfirm } from './github-confirm.js';

interface GitHubConfirmPageProps {
  params: Promise<{ locale: string }>;
}

export function generateMetadata({ params }: GitHubConfirmPageProps): Promise<Metadata> {
  return pageMetadata(params, 'githubConfirm');
}

export default async function GitHubConfirmPage({ params }: GitHubConfirmPageProps) {
  const { locale } = await params;
  if (!isLocale(locale)) {
    notFound();
  }
  setRequestLocale(locale);
  return <GitHubConfirm locale={locale} />;
}
