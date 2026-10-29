// Server-side helper shared by the dashboard, track, and module pages: gate on
// an active Session (Req 17.4) and hand back a ready `LearningApi`.
import { cookies } from 'next/headers';
import { redirect } from 'next/navigation';

import { resolveDashboardAccess } from './dashboard-access.js';
import { createLearningApi } from './learning-api.js';
import { type LearningApi } from './learning.js';
import { type Locale } from './locale.js';
import { SESSION_COOKIE, resolveSessionState } from './session.js';

/** Redirect visitors without an active Session; otherwise return the API port. */
export async function requireLearningApi(locale: Locale): Promise<LearningApi> {
  const access = resolveDashboardAccess(await resolveSessionState(), locale);
  if (access.kind === 'redirect') {
    redirect(access.to);
  }
  const reference = (await cookies()).get(SESSION_COOKIE)?.value ?? '';
  return createLearningApi(locale, reference);
}
