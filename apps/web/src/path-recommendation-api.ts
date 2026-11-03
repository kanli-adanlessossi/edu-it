// Adapter binding the generated OpenAPI client to the path recommendation port
// (ai-content Req 17.7, 17.8). Every call goes through `createApiClient`, which adds the
// CSRF header on the unsafe dismissal. `get` also runs on the server, where the opaque
// session reference is forwarded as a cookie (A4); in the browser the cookie jar is used.

import { createApiClient } from './create-api-client.js';
import type { GeneratedClient } from './generated/api-client.js';
import { routing } from './i18n/routing.js';
import { isLocale } from './locale.js';
import {
  PathRecommendationApiError,
  pathRecommendationSchema,
  type PathRecommendation,
  type PathRecommendationApi,
} from './path-recommendation.js';
import { SESSION_COOKIE } from './session.js';

export function createPathRecommendationApi(
  localeInput: string,
  options: { readonly sessionReference?: string; readonly client?: GeneratedClient } = {},
): PathRecommendationApi {
  const locale = isLocale(localeInput) ? localeInput : routing.defaultLocale;
  const http = options.client ?? createApiClient();
  const headers: Record<string, string> = {
    accept: 'application/json',
    'accept-language': locale,
    ...(options.sessionReference !== undefined
      ? { cookie: `${SESSION_COOKIE}=${options.sessionReference}` }
      : {}),
  };
  return {
    async get(): Promise<PathRecommendation | null> {
      const response = await http.call('GET', '/path-recommendation', {
        credentials: 'include',
        headers,
        cache: 'no-store',
      });
      if (response.status === 204) return null;
      if (!response.ok) throw new PathRecommendationApiError(response.status);
      const parsed = pathRecommendationSchema.safeParse(await response.json());
      if (!parsed.success) throw new PathRecommendationApiError(502);
      return parsed.data;
    },
    async dismiss(stableId: string): Promise<void> {
      const response = await http.call(
        'POST',
        `/path-recommendation/${encodeURIComponent(stableId)}/dismissal`,
        {
          credentials: 'include',
          headers: { ...headers, 'content-type': 'application/json' },
          body: '{}',
        },
      );
      if (!response.ok) throw new PathRecommendationApiError(response.status);
    },
  };
}
