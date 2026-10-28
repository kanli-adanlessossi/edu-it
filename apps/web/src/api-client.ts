// Adapters binding the generated OpenAPI client to the narrow ports the web
// pages depend on (Req 16.3, 17.2).
//
// The web app calls apps/api EXCLUSIVELY through the generated client (Req 16.3,
// 17.2). Pages, however, depend on small purpose-built ports (`AuthApi`, …) so
// their behavior can be unit- and property-tested without a network. This
// module is the single place those ports are satisfied by the real generated
// client, so there is exactly one request surface and no hand-written fetch
// code anywhere in the pages.

import { GeneratedClient } from './generated/api-client.js';
import { routing } from './i18n/routing.js';
import {
  AuthSubmitError,
  type AuthApi,
  type AuthFormFields,
  type AuthPage,
  type AuthSubmitSuccess,
} from './auth-forms.js';
import { isLocale, type Locale } from './locale.js';

/** The API base URL the generated client targets, from the environment. */
function apiBaseUrl(): string {
  // Read the public base URL the browser uses to reach the API. The env var is
  // enumerated in `.env.example` (task 3.2); fall back to the local dev default.
  const fromEnv = process.env.NEXT_PUBLIC_API_BASE_URL;
  return fromEnv && fromEnv.length > 0 ? fromEnv : 'http://localhost:8000/v1';
}

/** The locale sent so the API renders learner-facing `detail` in it (Req 19.3). */
function resolveLocale(locale: string): Locale {
  return isLocale(locale) ? locale : routing.defaultLocale;
}

/** The API path each auth page submits to. */
const AUTH_PATHS: { readonly [P in AuthPage]: string } = {
  'sign-up': '/auth/sign-up',
  'sign-in': '/auth/sign-in',
  'verify-email': '/auth/verify-email/resend',
  'reset-password': '/auth/password-reset',
};

/**
 * Extract a learner-facing error message from a Problem_Details response body
 * without leaking internal diagnostics (Req 19.3, 19.6). Only the translated
 * `detail` (or `title`) member is surfaced to the UI.
 */
async function readProblemDetail(response: Response): Promise<string | undefined> {
  try {
    const body = (await response.json()) as { detail?: unknown; title?: unknown };
    if (typeof body.detail === 'string' && body.detail.length > 0) {
      return body.detail;
    }
    if (typeof body.title === 'string' && body.title.length > 0) {
      return body.title;
    }
  } catch {
    // A non-JSON body carries no learner-facing detail to surface.
  }
  return undefined;
}

/**
 * Adapt the generated client to the {@link AuthApi} port so auth pages submit
 * exclusively through the generated client (Req 17.2). A non-2xx response (a
 * Problem_Details document, Req 19.1) becomes an {@link AuthSubmitError}
 * carrying only the translated learner-facing `detail`.
 */
export function createAuthApi(locale: string, client?: GeneratedClient): AuthApi {
  const resolvedLocale = resolveLocale(locale);
  const generated = client ?? new GeneratedClient({ baseUrl: apiBaseUrl() });

  return {
    async submit(page: AuthPage, fields: AuthFormFields): Promise<AuthSubmitSuccess> {
      const response = await generated.call('POST', AUTH_PATHS[page], {
        headers: {
          'content-type': 'application/json',
          'accept-language': resolvedLocale,
        },
        body: JSON.stringify(fields),
      });

      if (!response.ok) {
        const detail = await readProblemDetail(response);
        throw new AuthSubmitError(detail ?? '', resolvedLocale);
      }

      // Successful auth submissions may direct the User onward (e.g. to the
      // dashboard after sign-in). A body-less success leaves `redirectTo` unset.
      try {
        const body = (await response.json()) as { redirectTo?: unknown };
        if (typeof body.redirectTo === 'string') {
          return { redirectTo: body.redirectTo };
        }
      } catch {
        // No JSON body; a plain success with no redirect target.
      }
      return {};
    },
  };
}
