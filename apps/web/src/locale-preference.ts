// Saved locale preference round-trip for a signed-in User (Req 15.3, 15.4).
//
// When a signed-in User selects a locale, the API_Service saves it as the
// User's preference and the Web_App renders the saved locale on the next visit
// (Req 15.3). If saving fails, the previously saved preference is left unchanged
// and an error is returned (Req 15.4).
//
// The actual HTTP call goes through the generated OpenAPI client (Req 16.3), so
// this module depends only on a narrow `LocalePreferenceApi` port that the
// generated client satisfies. Keeping the round-trip and failure semantics in a
// pure, injectable function lets the later Property 22 test ("a saved locale
// preference round-trips") drive it with an in-memory store and injected
// failures — no network, no next-intl, no React.

import { isLocale, type Locale } from './locale.js';

/**
 * The slice of the API the preference round-trip needs. The generated client's
 * locale-save operation is adapted to this port; tests supply an in-memory fake.
 *
 * `saveLocale` resolves when the server has durably persisted the new preference
 * and rejects (throws) when the save fails for any reason (validation, network,
 * or server error). `readLocale` returns the currently persisted preference, or
 * `null` when the User has not yet saved one.
 */
export interface LocalePreferenceApi {
  saveLocale(locale: Locale): Promise<void>;
  readLocale(): Promise<Locale | null>;
}

/** Raised when saving the locale preference fails (Req 15.4). */
export class LocalePreferenceSaveError extends Error {
  /** The locale that was already saved before the failed attempt. */
  readonly previousLocale: Locale | null;
  /** The underlying cause, when one is available. */
  override readonly cause?: unknown;

  constructor(previousLocale: Locale | null, cause?: unknown) {
    super('Saving the locale preference failed; the previous preference is unchanged.');
    this.name = 'LocalePreferenceSaveError';
    this.previousLocale = previousLocale;
    this.cause = cause;
  }
}

/** Raised when the caller supplies a value that is not a supported locale. */
export class UnsupportedLocaleError extends Error {
  constructor(value: string) {
    super(`Unsupported locale "${value}"; expected one of fr, en.`);
    this.name = 'UnsupportedLocaleError';
  }
}

/**
 * Save a signed-in User's selected locale preference through the API (Req 15.3).
 *
 * On success the new preference is persisted and returned, so the next visit
 * renders it. On a save failure the previously saved preference is left exactly
 * as it was and a {@link LocalePreferenceSaveError} is thrown carrying that
 * previous value, so callers can surface an error without corrupting state
 * (Req 15.4).
 *
 * @throws {UnsupportedLocaleError} when `selected` is not `fr` or `en`.
 * @throws {LocalePreferenceSaveError} when the API save fails.
 */
export async function saveLocalePreference(
  api: LocalePreferenceApi,
  selected: Locale,
): Promise<Locale> {
  if (!isLocale(selected)) {
    throw new UnsupportedLocaleError(selected);
  }

  // Capture the previous preference first so a save failure can report it and
  // guarantee it is left unchanged (Req 15.4).
  const previous = await api.readLocale();

  try {
    await api.saveLocale(selected);
  } catch (cause) {
    throw new LocalePreferenceSaveError(previous, cause);
  }

  return selected;
}

/**
 * Read the locale the Web_App should render for a signed-in User on this visit
 * (Req 15.3).
 *
 * Returns the User's saved preference when present and supported; otherwise
 * falls back to `fallback` so an un-set or stale value never leaves the app
 * without a locale to render.
 */
export async function resolveRenderedLocale(
  api: LocalePreferenceApi,
  fallback: Locale,
): Promise<Locale> {
  const saved = await api.readLocale();
  return saved !== null && isLocale(saved) ? saved : fallback;
}
