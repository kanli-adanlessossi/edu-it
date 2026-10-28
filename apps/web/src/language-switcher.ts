// Pure, framework-free URL algebra for the language switcher (Req 15.2).
//
// The Web_App serves its pages under a `/fr` route tree and an `/en` route tree
// (Req 15.1). When a User activates the language switcher, the Web_App moves to
// the other-locale view of the *current* page, changing only the leading locale
// segment while preserving the rest of the path and all query parameters
// (Req 15.2).
//
// This module isolates that rewrite as a pure function so it is directly
// testable: the later Property 21 test ("the language switcher preserves path
// and query and round-trips") targets `switchLocalePath` / `otherLocalePath`.
// Keeping it free of next-intl and React means the same algebra backs both the
// server-rendered links and any client navigation without duplication.

import { isLocale, otherLocale, type Locale } from './locale.js';

/** A URL split into the parts the switcher must preserve or rewrite. */
interface SplitPath {
  /** Everything after the locale segment, always starting with `/`. */
  readonly rest: string;
  /** The raw query string including a leading `?`, or `''` when absent. */
  readonly query: string;
  /** The raw fragment including a leading `#`, or `''` when absent. */
  readonly hash: string;
}

/**
 * Split an application path into its leading locale segment (if any), the
 * remaining path, the query string, and the fragment.
 *
 * Only the path+query+hash portion of a URL is accepted (what a Next.js route
 * exposes); absolute URLs with a scheme/host are out of scope for the switcher,
 * which always stays within the app. A path with no recognizable leading locale
 * segment is treated as having no locale, so `rest` is the whole path.
 */
function splitLocalePath(pathWithQuery: string): { locale: Locale | null } & SplitPath {
  // Separate the fragment first, then the query, so neither leaks into `rest`.
  const hashIndex = pathWithQuery.indexOf('#');
  const hash = hashIndex === -1 ? '' : pathWithQuery.slice(hashIndex);
  const beforeHash = hashIndex === -1 ? pathWithQuery : pathWithQuery.slice(0, hashIndex);

  const queryIndex = beforeHash.indexOf('?');
  const query = queryIndex === -1 ? '' : beforeHash.slice(queryIndex);
  const pathname = queryIndex === -1 ? beforeHash : beforeHash.slice(0, queryIndex);

  // Normalize to a leading slash so the first segment is unambiguous.
  const normalized = pathname.startsWith('/') ? pathname : `/${pathname}`;
  const firstSlash = normalized.indexOf('/', 1);
  const firstSegment = firstSlash === -1 ? normalized.slice(1) : normalized.slice(1, firstSlash);

  if (isLocale(firstSegment)) {
    const remainder = firstSlash === -1 ? '' : normalized.slice(firstSlash);
    return { locale: firstSegment, rest: remainder === '' ? '/' : remainder, query, hash };
  }

  return { locale: null, rest: normalized, query, hash };
}

/**
 * Build the path for the given page under an explicit target locale, preserving
 * the page path, query parameters, and fragment (Req 15.2).
 *
 * The result always has the shape `/{locale}{rest}{query}{hash}` where `rest`
 * starts with `/`. If the input already carries a locale segment it is replaced;
 * if it carries none, the target locale segment is prepended.
 */
export function withLocalePath(pathWithQuery: string, target: Locale): string {
  const { rest, query, hash } = splitLocalePath(pathWithQuery);
  // `rest` is `'/'` for the locale root; keep that as just `/{locale}`.
  const tail = rest === '/' ? '' : rest;
  return `/${target}${tail}${query}${hash}`;
}

/**
 * Rewrite the current page path to the *other* locale's view (Req 15.2).
 *
 * The locale segment is read from the path; when the path carries no locale the
 * provided `currentLocale` is used as the origin, so the switch still targets
 * the opposite locale. Path, query, and fragment are preserved unchanged.
 */
export function switchLocalePath(pathWithQuery: string, currentLocale: Locale): string {
  const { locale } = splitLocalePath(pathWithQuery);
  const from = locale ?? currentLocale;
  return withLocalePath(pathWithQuery, otherLocale(from));
}
