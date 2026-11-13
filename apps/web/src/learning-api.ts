// Adapter binding the generated OpenAPI client to the `LearningApi` port used by
// the dashboard, track, and module pages (Req 19.6). The pages never hand-write
// fetch code; every call flows through the generated client, and every payload
// is validated with Zod before it reaches a page.
import type { ZodType } from 'zod';

import { createApiClient } from './create-api-client.js';
import type { GeneratedClient } from './generated/api-client.js';
import { routing } from './i18n/routing.js';
import { isLocale } from './locale.js';
import { SESSION_COOKIE } from './session-shared.js';
import {
  enrollmentPageSchema,
  enrollmentSchema,
  progressSchema,
  trackPageSchema,
  type Enrollment,
  type LearningApi,
  type Progress,
  type ReviewScheduleEntry,
  type Track,
} from './learning.js';

/** Raised when the API answers with a non-2xx status or an unexpected payload. */
export class LearningApiError extends Error {
  constructor(readonly status: number) {
    super(`Learning API request failed (${status})`);
    this.name = 'LearningApiError';
  }
}

/**
 * Build the {@link LearningApi} over the generated client. `sessionReference`
 * is the opaque session cookie value, forwarded server-side (A4).
 */
export function createLearningApi(
  locale: string,
  sessionReference: string,
  client?: GeneratedClient,
): LearningApi {
  const lang = isLocale(locale) ? locale : routing.defaultLocale;
  const generated = client ?? createApiClient();
  const headers = {
    accept: 'application/json',
    'accept-language': lang,
    cookie: `${SESSION_COOKIE}=${sessionReference}`,
  };

  async function send<T>(
    method: string,
    path: string,
    schema: ZodType<T>,
    body?: unknown,
  ): Promise<T> {
    const response = await generated.call(method, path, {
      headers: body === undefined ? headers : { ...headers, 'content-type': 'application/json' },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      cache: 'no-store',
    });
    if (!response.ok) throw new LearningApiError(response.status);
    const parsed = schema.safeParse(await response.json());
    if (!parsed.success) throw new LearningApiError(502);
    return parsed.data;
  }

  return {
    async listEnrollments(): Promise<readonly Enrollment[]> {
      return (await send('GET', '/enrollments?limit=50', enrollmentPageSchema)).items;
    },
    getProgress(trackId: string): Promise<Progress> {
      return send(
        'GET',
        `/progress?track_id=${encodeURIComponent(trackId)}&locale=${lang}`,
        progressSchema,
      );
    },
    getReviewSchedule(): Promise<readonly ReviewScheduleEntry[]> {
      // The API publishes no schedule listing (GET /v1/reviews/schedule is out of scope for
      // api-route-wiring, design decision 7; task 13.2). Do not call it: the route would 404.
      // The dashboard shows no due items until a listing endpoint exists.
      return Promise.resolve([]);
    },
    enroll(trackId: string): Promise<Enrollment> {
      return send('POST', '/enrollments', enrollmentSchema, { track_id: trackId });
    },
    async listTracks(): Promise<readonly Track[]> {
      // One page is enough for the seed catalog; MAX_PAGE_SIZE bounds it server-side.
      return (await send('GET', `/tracks?locale=${lang}&limit=50`, trackPageSchema)).items;
    },
  };
}
