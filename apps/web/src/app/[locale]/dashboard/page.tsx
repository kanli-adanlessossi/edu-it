// Empty dashboard under `/fr/dashboard` and `/en/dashboard` (Req 17.1, 17.4).
//
// The dashboard is reachable ONLY by a User with an active Session; a visitor
// with no active Session is redirected to the sign-in page (Req 17.4). The
// server-side Session is authoritative (A4): this Server Component resolves the
// opaque session reference carried by the request through the generated client,
// then applies the pure `resolveDashboardAccess` gating decision (the invariant
// exercised by Property 25). No dashboard content is rendered unless the gate
// allows it.

import type { Metadata } from 'next';
import { setRequestLocale } from 'next-intl/server';
import { useTranslations } from 'next-intl';
import { notFound, redirect } from 'next/navigation';

import { isLocale, type Locale } from '../../../locale.js';
import { resolveDashboardAccess } from '../../../dashboard-access.js';
import { resolveSessionState } from '../../../session.js';
import { pageMetadata } from '../page-metadata.js';

// The dashboard reads the per-request session cookie to gate access (Req 17.4),
// so it is rendered dynamically per request rather than statically prerendered.
export const dynamic = 'force-dynamic';

interface DashboardPageProps {
  params: Promise<{ locale: string }>;
}

export function generateMetadata({ params }: DashboardPageProps): Promise<Metadata> {
  return pageMetadata(params, 'dashboard');
}

export default async function DashboardPage({ params }: DashboardPageProps) {
  const { locale } = await params;
  if (!isLocale(locale)) {
    notFound();
  }
  setRequestLocale(locale);

  // Resolve the request's session state server-side, then gate (Req 17.4).
  const state = await resolveSessionState();
  const access = resolveDashboardAccess(state, locale);
  if (access.kind === 'redirect') {
    redirect(access.to);
  }

  return <DashboardContent locale={locale} />;
}

function DashboardContent({ locale }: { locale: Locale }) {
  setRequestLocale(locale);
  const t = useTranslations('Dashboard');
  return (
    <section>
      <h1>{t('title')}</h1>
      <p>{t('empty')}</p>
    </section>
  );
}
