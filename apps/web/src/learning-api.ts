// Adapter binding the generated OpenAPI client to the `LearningApi` port used by
// the dashboard, track, and module pages (Req 19.6). The pages never hand-write
// fetch code; every call flows through the generated client, and every payload
// is validated with Zod before it reaches a page.
import type { ZodType } from 'zod';

import { GeneratedClient } from './generated/api-client.js';
import { routing } from './i18n/routing.js';
import { isLocale } from './locale.js';
import { SESSION_COOKIE } from './session.js';
import {
  enrollmentPageSchema,
  enrollmentSchema,
  progressSchema,
  reviewScheduleSchema,
  type Enrollment,
  type LearningApi,
  type Progress,
  type ReviewScheduleEntry,
} from './learning.js';

function apiBaseUrl(): string {
  const fromEnv = process.env.NEXT_PUBLIC_API_BASE_URL;
  return fromEnv && fromEnv.length > 0 ? fromEnv : 'http://localhost:8000/v1';
}

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
  const generated = client ?? new GeneratedClient({ baseUrl: apiBaseUrl() });
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
      return send('GET', `/progress?track_id=${encodeURIComponent(trackId)}`, progressSchema);
    },
    async getReviewSchedule(): Promise<readonly ReviewScheduleEntry[]> {
      try {
        return (await send('GET', '/reviews/schedule', reviewScheduleSchema)).items;
      } catch (error) {
        // The API does not yet publish a schedule listing; show no due items
        // rather than failing the whole dashboard.
        if (error instanceof LearningApiError && (error.status === 404 || error.status === 405)) {
          return [];
        }
        throw error;
      }
    },
    enroll(trackId: string): Promise<Enrollment> {
      return send('POST', '/enrollments', enrollmentSchema, { track_id: trackId });
    },
  };
}
