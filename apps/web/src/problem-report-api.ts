// Adapter binding the generated OpenAPI client to the problem report port
// (ai-content Req 18.1, 18.2). `POST /problem-reports` needs an Idempotency-Key;
// the CSRF header is added by `createApiClient`. A non-2xx answer (RFC 9457
// problem document) becomes a `ProblemReportApiError` with the translated detail.

import { createApiClient } from './create-api-client.js';
import type { GeneratedClient } from './generated/api-client.js';
import { routing } from './i18n/routing.js';
import { isLocale, type Locale } from './locale.js';
import {
  ProblemReportApiError,
  type ProblemReportApi,
  type ProblemReportInput,
  type ProblemReportResult,
} from './problem-report.js';

type Json = Record<string, unknown>;

export function createProblemReportApi(
  localeInput: string,
  client?: GeneratedClient,
): ProblemReportApi {
  const locale: Locale = isLocale(localeInput) ? localeInput : routing.defaultLocale;
  const http = client ?? createApiClient();
  return {
    async submit(input: ProblemReportInput, idempotencyKey: string): Promise<ProblemReportResult> {
      const response = await http.call('POST', '/problem-reports', {
        credentials: 'include',
        headers: {
          'content-type': 'application/json',
          'accept-language': locale,
          'idempotency-key': idempotencyKey,
        },
        body: JSON.stringify({
          item_kind: input.itemKind,
          item_id: input.itemId,
          content_version: input.contentVersion,
          category: input.category,
          ...(input.comment !== undefined ? { comment: input.comment } : {}),
        }),
      });
      if (!response.ok) {
        let detail = '';
        try {
          const body = (await response.json()) as { detail?: unknown; title?: unknown };
          if (typeof body.detail === 'string') detail = body.detail;
          else if (typeof body.title === 'string') detail = body.title;
        } catch {
          // Non-JSON error body: no learner-facing detail to surface.
        }
        throw new ProblemReportApiError(detail, response.status);
      }
      const body = (await response.json()) as Json;
      return {
        stableId: typeof body.stable_id === 'string' ? body.stable_id : '',
        replayed: body.replayed === true,
      };
    },
  };
}
