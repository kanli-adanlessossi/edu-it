// API base URL selection (kubernetes-deployment E2E): the deployed image builds without
// NEXT_PUBLIC_API_BASE_URL, so the browser uses the same-origin `/v1` that the ingress routes to
// the Api, and the server uses the in-cluster `API_INTERNAL_BASE_URL` set by the Chart.

import { afterEach, describe, expect, it, vi } from 'vitest';

import { apiBaseUrl, publicApiBaseUrl, SAME_ORIGIN_API_BASE } from './create-api-client.js';

const INTERNAL = 'http://edu-it-api.platform.svc.cluster.local:8000/v1';

function unset(name: string): void {
  vi.stubEnv(name, '');
}

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe('publicApiBaseUrl', () => {
  it('is same-origin /v1 when the build sets no public base URL', () => {
    unset('NEXT_PUBLIC_API_BASE_URL');
    expect(publicApiBaseUrl()).toBe(SAME_ORIGIN_API_BASE);
    expect(SAME_ORIGIN_API_BASE).toBe('/v1');
  });

  it('uses NEXT_PUBLIC_API_BASE_URL when the build sets it', () => {
    vi.stubEnv('NEXT_PUBLIC_API_BASE_URL', 'http://localhost:8000/v1');
    expect(publicApiBaseUrl()).toBe('http://localhost:8000/v1');
  });

  it('never exposes the in-cluster URL', () => {
    unset('NEXT_PUBLIC_API_BASE_URL');
    vi.stubEnv('API_INTERNAL_BASE_URL', INTERNAL);
    expect(publicApiBaseUrl()).toBe('/v1');
  });
});

describe('apiBaseUrl on the server', () => {
  it('prefers the runtime API_INTERNAL_BASE_URL', () => {
    vi.stubEnv('API_INTERNAL_BASE_URL', INTERNAL);
    vi.stubEnv('NEXT_PUBLIC_API_BASE_URL', 'http://localhost:8000/v1');
    expect(apiBaseUrl()).toBe(INTERNAL);
  });

  it('falls back to NEXT_PUBLIC_API_BASE_URL, then the local default', () => {
    unset('API_INTERNAL_BASE_URL');
    vi.stubEnv('NEXT_PUBLIC_API_BASE_URL', 'http://127.0.0.1:8100/v1');
    expect(apiBaseUrl()).toBe('http://127.0.0.1:8100/v1');
    unset('NEXT_PUBLIC_API_BASE_URL');
    expect(apiBaseUrl()).toBe('http://localhost:8000/v1');
  });
});

describe('apiBaseUrl in the browser', () => {
  it('is the public base, never the in-cluster URL', () => {
    vi.stubGlobal('document', { cookie: '' });
    vi.stubEnv('API_INTERNAL_BASE_URL', INTERNAL);
    unset('NEXT_PUBLIC_API_BASE_URL');
    expect(apiBaseUrl()).toBe('/v1');
  });
});
