// Shared server-side helpers for the quiz, review, and project pages
// (Req 19.4, 19.5, 19.6): locale validation, session gating (Req 17.4), cookie
// forwarding to the API, and localized document titles.

import type { Metadata } from 'next';
import { cookies } from 'next/headers';
import { notFound, redirect } from 'next/navigation';
import { getTranslations, setRequestLocale } from 'next-intl/server';

import { resolveDashboardAccess } from '../../dashboard-access.js';
import { isLocale, type Locale } from '../../locale.js';
import { SESSION_COOKIE, resolveSessionState } from '../../session.js';

export type PracticeTitleKey = 'quiz' | 'review' | 'project';

/** Validate the locale segment, gate on an active Session, return the cookie header. */
export async function requirePracticeContext(
  rawLocale: string,
): Promise<{ locale: Locale; cookie: string | undefined }> {
  if (!isLocale(rawLocale)) {
    notFound();
  }
  setRequestLocale(rawLocale);
  const access = resolveDashboardAccess(await resolveSessionState(), rawLocale);
  if (access.kind === 'redirect') {
    redirect(access.to);
  }
  const reference = (await cookies()).get(SESSION_COOKIE)?.value;
  return {
    locale: rawLocale,
    cookie: reference ? `${SESSION_COOKIE}=${reference}` : undefined,
  };
}

export async function practiceMetadata(
  params: Promise<{ locale: string }>,
  key: PracticeTitleKey,
): Promise<Metadata> {
  const { locale } = await params;
  const resolved = isLocale(locale) ? locale : 'fr';
  setRequestLocale(resolved);
  const t = await getTranslations({ locale: resolved, namespace: 'Practice.meta' });
  return { title: t(key) };
}
