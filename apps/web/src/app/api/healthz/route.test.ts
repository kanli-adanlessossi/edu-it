import { describe, expect, it } from 'vitest';

import { dynamic, GET } from './route.js';

// The chart's web probes (liveness, readiness, startup) hit GET /api/healthz and
// require a 200 for the pod to become Ready (kubernetes-deployment Req 8.x).

describe('GET /api/healthz', () => {
  it('answers with a 200 so the kubelet probe passes', () => {
    const response = GET();
    expect(response.status).toBe(200);
  });

  it('is dynamic so the probe always reaches live code', () => {
    expect(dynamic).toBe('force-dynamic');
  });
});
