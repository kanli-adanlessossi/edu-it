// Server-side helper shared by the dashboard, track, and module pages: gate on
// an active Session (Req 17.4) and hand back a ready `LearningApi`.
import { cookies } from 'next/headers';
import { redirect } from 'next/navigation';

import type { GeneratedClient } from './generated/api-client.js';
import { resolveDashboardAccess } from './dashboard-access.js';
import { createLearningApi } from './learning-api.js';
import { type LearningApi } from './learning.js';
import { type Locale } from './locale.js';
import { resolveSessionState } from './session.js';
import { SESSION_COOKIE } from './session-shared.js';

/**
 * Redirect visitors without an active Session; otherwise return the opaque session reference
 * to forward to the API on server-side calls.
 */
export async function requireSessionReference(locale: Locale): Promise<string> {
  const access = resolveDashboardAccess(await resolveSessionState(), locale);
  if (access.kind === 'redirect') {
    redirect(access.to);
  }
  return (await cookies()).get(SESSION_COOKIE)?.value ?? '';
}

/** Redirect visitors without an active Session; otherwise return the API port. */
export async function requireLearningApi(
  locale: Locale,
  client?: GeneratedClient,
): Promise<LearningApi> {
  return createLearningApi(locale, await requireSessionReference(locale), client);
}
