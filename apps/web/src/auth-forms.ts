// Auth-page submission logic for the sign-up, sign-in, email-verification, and
// password-reset pages (Req 17.1, 17.2, 17.3).
//
// Auth pages submit EXCLUSIVELY through the generated OpenAPI client (Req 17.2).
// To keep the generated client the single request surface while still being
// able to unit- and property-test the submission behavior without a network,
// next-intl, or React, this module depends only on a narrow `AuthApi` port that
// the generated client satisfies. A page adapts the generated client's auth
// operation to this port; tests supply an in-memory fake.
//
// The field-preservation behavior on a failed submission (Property 24) is a
// pure function of the submitted field values and the API outcome, so it lives
// here rather than inside a React component: on failure the page is preserved
// with every non-password field equal to what the User entered, every password
// field cleared, and an error indication shown (Req 17.3).

import { type Locale } from './locale.js';

/** The auth pages the application shell provides (Req 17.1). */
export const AUTH_PAGES = ['sign-up', 'sign-in', 'verify-email', 'reset-password'] as const;

export type AuthPage = (typeof AUTH_PAGES)[number];

/** Returns true when `value` names a known auth page. */
export function isAuthPage(value: string): value is AuthPage {
  return (AUTH_PAGES as readonly string[]).includes(value);
}

/**
 * The names of the fields that hold a secret and must never be preserved across
 * a failed submission (Req 17.3). Any field whose name is in this set is treated
 * as a password field and is cleared when the page is re-rendered after a
 * failure, regardless of which auth page it is on.
 */
export const PASSWORD_FIELD_NAMES: ReadonlySet<string> = new Set([
  'password',
  'newPassword',
  'currentPassword',
  'confirmPassword',
  'passwordConfirmation',
]);

/** Returns true when a field name denotes a password (secret) field. */
export function isPasswordField(name: string): boolean {
  return PASSWORD_FIELD_NAMES.has(name);
}

/** A flat map of auth-form field names to the User-entered string values. */
export type AuthFormFields = Readonly<Record<string, string>>;

/**
 * The slice of the API an auth-page submission needs. The generated client's
 * auth operations are adapted to this port; tests supply an in-memory fake.
 *
 * `submit` resolves when the API accepts the submission and rejects (throws)
 * when the submission fails or the API returns an error (validation, invalid
 * credentials, unverified account, network, or server error). The thrown value
 * carries the information needed to render a learner-facing error indication.
 */
export interface AuthApi {
  submit(page: AuthPage, fields: AuthFormFields): Promise<AuthSubmitSuccess>;
}

/** A successful auth submission outcome returned by the API through the client. */
export interface AuthSubmitSuccess {
  /** The page the User should be taken to on success (e.g. the dashboard). */
  readonly redirectTo?: string;
}

/**
 * An error raised when an auth submission fails. It carries a learner-facing,
 * already-localized message describing the failure (Req 17.3); the page renders
 * this message as its error indication. Internal diagnostics are never placed
 * here — the API returns a translated Problem_Details `detail` (Req 19.3) and
 * the client surfaces only that learner-facing text.
 */
export class AuthSubmitError extends Error {
  /** The locale the learner-facing message is rendered in. */
  readonly locale: Locale;
  /** The underlying cause, when one is available (kept out of the UI). */
  override readonly cause?: unknown;

  constructor(message: string, locale: Locale, cause?: unknown) {
    super(message);
    this.name = 'AuthSubmitError';
    this.locale = locale;
    this.cause = cause;
  }
}

/** The result of submitting an auth form. */
export type AuthSubmitResult =
  | {
      readonly status: 'success';
      readonly redirectTo?: string;
    }
  | {
      readonly status: 'error';
      /** The page, with non-password fields preserved and passwords cleared. */
      readonly page: AuthPage;
      /** Field values to re-render: non-password values kept, passwords emptied. */
      readonly fields: AuthFormFields;
      /** The learner-facing error indication to display (Req 17.3). */
      readonly error: string;
    };

/**
 * Compute the field values to re-render after a failed submission: every
 * non-password field keeps the value the User entered, and every password field
 * is cleared (Req 17.3).
 *
 * Returned as a fresh object so the caller cannot accidentally mutate the
 * submitted fields; password fields present in the submission are kept as keys
 * (so the form still renders them) but with an empty value.
 */
export function preserveNonPasswordFields(fields: AuthFormFields): AuthFormFields {
  // Object.fromEntries defines own properties, so a field named "__proto__"
  // is preserved rather than being treated as a prototype assignment.
  return Object.fromEntries(
    Object.entries(fields).map(([name, value]) => [name, isPasswordField(name) ? '' : value]),
  );
}

/**
 * Submit an auth form through the API (Req 17.2) and derive the page's next
 * state.
 *
 * On success the result reports success and any redirect target the API
 * returned. On any failure the page is preserved: the result carries the same
 * page, the submitted non-password field values unchanged, every password field
 * cleared, and a learner-facing error indication (Req 17.3). The submission is
 * never retried and no field value other than passwords is dropped.
 */
export async function submitAuthForm(
  api: AuthApi,
  page: AuthPage,
  fields: AuthFormFields,
  fallbackErrorMessage: string,
): Promise<AuthSubmitResult> {
  try {
    const success = await api.submit(page, fields);
    return { status: 'success', redirectTo: success.redirectTo };
  } catch (cause) {
    const error =
      cause instanceof AuthSubmitError && cause.message.length > 0
        ? cause.message
        : fallbackErrorMessage;
    return {
      status: 'error',
      page,
      fields: preserveNonPasswordFields(fields),
      error,
    };
  }
}
