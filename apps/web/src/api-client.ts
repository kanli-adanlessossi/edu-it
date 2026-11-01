// Adapters binding the generated OpenAPI client to the narrow ports the web
// pages depend on (Req 16.3, 17.2).
//
// The web app calls apps/api EXCLUSIVELY through the generated client (Req 16.3,
// 17.2). Pages, however, depend on small purpose-built ports (`AuthApi`, …) so
// their behavior can be unit- and property-tested without a network. This
// module is the single place those ports are satisfied by the real generated
// client, so there is exactly one request surface and no hand-written fetch
// code anywhere in the pages.

import { createApiClient } from './create-api-client.js';
import type { GeneratedClient } from './generated/api-client.js';
import { routing } from './i18n/routing.js';
import {
  AuthSubmitError,
  type AuthApi,
  type AuthFormFields,
  type AuthPage,
  type AuthSubmitSuccess,
} from './auth-forms.js';
import { isLocale, type Locale } from './locale.js';

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

/** The API path the verification link's token is posted to. */
const VERIFY_EMAIL_PATH = '/auth/verify-email';

/** The API path a reset link's token and the new password are posted to (Milestone 2). */
const RESET_COMPLETE_PATH = '/auth/password-reset/complete';

/** The API paths of the GitHub link-confirmation step (Milestone 2). */
const GITHUB_CONFIRM_PATH = '/auth/github/confirm';
const GITHUB_DECLINE_PATH = '/auth/github/decline';

/** Outcome of verifying an email token: `invalid` covers unknown, used, or expired links. */
export type VerifyEmailOutcome = 'verified' | 'invalid';

/** Outcome of completing a reset: `invalid` is an unknown, used, or expired link (offer restart). */
export type CompleteResetOutcome = AuthSubmitSuccess & { readonly status: 'completed' | 'invalid' };

/** The auth port plus the verification-link, reset-completion, and OAuth operations. */
export interface VerifyingAuthApi extends AuthApi {
  verifyEmail(token: string): Promise<VerifyEmailOutcome>;
  completePasswordReset(token: string, newPassword: string): Promise<CompleteResetOutcome>;
  confirmGitHubLink(): Promise<AuthSubmitSuccess>;
  declineGitHubLink(): Promise<void>;
}

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
export function createAuthApi(locale: string, client?: GeneratedClient): VerifyingAuthApi {
  const resolvedLocale = resolveLocale(locale);
  const generated = client ?? createApiClient();

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
        // The wire member is snake_case `redirect_to`; the web keeps `redirectTo`.
        const body = (await response.json()) as { redirect_to?: unknown };
        if (typeof body.redirect_to === 'string') {
          return { redirectTo: body.redirect_to };
        }
      } catch {
        // No JSON body; a plain success with no redirect target.
      }
      return {};
    },

    async verifyEmail(token: string): Promise<VerifyEmailOutcome> {
      const response = await generated.call('POST', VERIFY_EMAIL_PATH, {
        headers: {
          'content-type': 'application/json',
          'accept-language': resolvedLocale,
        },
        body: JSON.stringify({ token }),
      });
      if (response.ok) return 'verified';
      // 400 is the invalid-or-expired link problem; anything else is a failure.
      if (response.status === 400) return 'invalid';
      const detail = await readProblemDetail(response);
      throw new AuthSubmitError(detail ?? '', resolvedLocale);
    },

    async completePasswordReset(token: string, newPassword: string): Promise<CompleteResetOutcome> {
      const response = await generated.call('POST', RESET_COMPLETE_PATH, {
        headers: {
          'content-type': 'application/json',
          'accept-language': resolvedLocale,
        },
        body: JSON.stringify({ token, new_password: newPassword }),
      });
      if (response.ok) {
        const redirectTo = await readRedirectTo(response);
        return redirectTo === undefined ? { status: 'completed' } : { status: 'completed', redirectTo };
      }
      // 400 is the invalid-or-expired link problem; 422 (weak password) and others carry a detail.
      if (response.status === 400) return { status: 'invalid' };
      const detail = await readProblemDetail(response);
      throw new AuthSubmitError(detail ?? '', resolvedLocale);
    },

    async confirmGitHubLink(): Promise<AuthSubmitSuccess> {
      const response = await generated.call('POST', GITHUB_CONFIRM_PATH, {
        headers: { 'accept-language': resolvedLocale },
      });
      if (!response.ok) {
        const detail = await readProblemDetail(response);
        throw new AuthSubmitError(detail ?? '', resolvedLocale);
      }
      const redirectTo = await readRedirectTo(response);
      return redirectTo === undefined ? {} : { redirectTo };
    },

    async declineGitHubLink(): Promise<void> {
      const response = await generated.call('POST', GITHUB_DECLINE_PATH, {
        headers: { 'accept-language': resolvedLocale },
      });
      if (!response.ok) {
        const detail = await readProblemDetail(response);
        throw new AuthSubmitError(detail ?? '', resolvedLocale);
      }
    },
  };
}

/** Read the wire member `redirect_to` of a success body, when present. */
async function readRedirectTo(response: Response): Promise<string | undefined> {
  try {
    const body = (await response.json()) as { redirect_to?: unknown };
    return typeof body.redirect_to === 'string' ? body.redirect_to : undefined;
  } catch {
    return undefined;
  }
}
