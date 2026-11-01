// The one factory every web adapter uses to build its API client (api-route-wiring
// Req 14.2, 14.3, 14.4).
//
// The API protects unsafe calls with a double-submit CSRF pair: the readable
// `csrf_token` cookie must equal the `X-CSRF-Token` header (apps/api csrf.py).
//
// - In the browser the client sends `credentials: 'include'`, reads the
//   `csrf_token` cookie, requests `GET /auth/csrf` once when the cookie is
//   absent, and echoes the token in `X-CSRF-Token` on unsafe calls.
// - On the server there is no cookie jar: the client forwards the request cookie
//   and generates a fresh pair (cookie and header carry the same new token).

import { GeneratedClient } from './generated/api-client.js';

/** Name of the readable CSRF cookie and the header that echoes it. */
export const CSRF_COOKIE = 'csrf_token';
export const CSRF_HEADER = 'X-CSRF-Token';

const SAFE_METHODS: ReadonlySet<string> = new Set(['GET', 'HEAD', 'OPTIONS', 'TRACE']);

export interface CreateApiClientOptions {
  /** The incoming request's `Cookie` header, forwarded when running on the server. */
  cookie?: string;
  /** Override the API base URL (defaults to `NEXT_PUBLIC_API_BASE_URL`). */
  baseUrl?: string;
  /** Underlying fetch implementation (defaults to the global fetch). */
  fetch?: typeof fetch;
  /** Force browser (`true`) or server (`false`) behavior; detected from `document` by default. */
  browser?: boolean;
  /** Read the `csrf_token` cookie in the browser (defaults to parsing `document.cookie`). */
  readCsrfCookie?: () => string | undefined;
}

/** The API base URL, from the environment with the local development default. */
export function apiBaseUrl(): string {
  const fromEnv = process.env.NEXT_PUBLIC_API_BASE_URL;
  return fromEnv && fromEnv.length > 0 ? fromEnv : 'http://localhost:8000/v1';
}

function documentCsrfCookie(): string | undefined {
  if (typeof document === 'undefined') return undefined;
  for (const part of document.cookie.split(';')) {
    const [name, ...rest] = part.trim().split('=');
    if (name === CSRF_COOKIE) {
      const value = rest.join('=');
      return value.length > 0 ? decodeURIComponent(value) : undefined;
    }
  }
  return undefined;
}

/** A fresh high-entropy URL-safe token (32 random bytes). */
export function freshCsrfToken(): string {
  const bytes = new Uint8Array(32);
  globalThis.crypto.getRandomValues(bytes);
  let binary = '';
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

function mergeCookies(...parts: (string | null | undefined)[]): string {
  return parts.filter((p): p is string => typeof p === 'string' && p.length > 0).join('; ');
}

/** Build the API client; the only place that constructs a `GeneratedClient`. */
export function createApiClient(options: CreateApiClientOptions = {}): GeneratedClient {
  const baseFetch: typeof fetch = options.fetch ?? ((input, init) => globalThis.fetch(input, init));
  const baseUrl = (options.baseUrl ?? apiBaseUrl()).replace(/\/+$/, '');
  const browser = options.browser ?? typeof document !== 'undefined';

  if (browser) {
    const readCookie = options.readCsrfCookie ?? documentCsrfCookie;
    let issuing: Promise<void> | undefined;

    // Request GET /auth/csrf at most once; the response sets the cookie.
    const ensureToken = async (): Promise<string | undefined> => {
      const existing = readCookie();
      if (existing !== undefined) return existing;
      issuing ??= baseFetch(`${baseUrl}/auth/csrf`, {
        method: 'GET',
        credentials: 'include',
        cache: 'no-store',
        headers: { accept: 'application/json' },
      }).then(
        () => undefined,
        () => undefined,
      );
      await issuing;
      return readCookie();
    };

    const browserFetch: typeof fetch = async (input, init) => {
      const method = (init?.method ?? 'GET').toUpperCase();
      const headers = new Headers(init?.headers);
      if (!SAFE_METHODS.has(method)) {
        const token = await ensureToken();
        if (token !== undefined) headers.set(CSRF_HEADER, token);
      }
      return baseFetch(input, { ...init, headers, credentials: 'include' });
    };
    return new GeneratedClient({ baseUrl, fetch: browserFetch });
  }

  // Server: forward the request cookie and mint a fresh pair per client.
  const token = freshCsrfToken();
  const serverFetch: typeof fetch = (input, init) => {
    const method = (init?.method ?? 'GET').toUpperCase();
    const headers = new Headers(init?.headers);
    const unsafe = !SAFE_METHODS.has(method);
    const cookie = mergeCookies(
      headers.get('cookie') ?? options.cookie,
      unsafe ? `${CSRF_COOKIE}=${token}` : undefined,
    );
    if (cookie.length > 0) headers.set('cookie', cookie);
    if (unsafe) headers.set(CSRF_HEADER, token);
    return baseFetch(input, { ...init, headers });
  };
  return new GeneratedClient({ baseUrl, fetch: serverFetch });
}
