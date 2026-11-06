// Liveness/readiness/startup probe endpoint for the Web_App container
// (kubernetes-deployment Req 8.1, 8.5, 8.6, 8.12). The Helm chart's web probes
// target GET /api/healthz on the container's HTTP port (deploy/helm/edu-it
// values `web.probes.*.path`); this Route Handler answers them with a plain 200
// so the kubelet can tell the Next.js server is up.
//
// It lives under /api/*, which the next-intl middleware matcher excludes, so it
// is reachable without a locale prefix (unlike the /fr and /en page tree). It is
// deliberately dependency-free: a frontend liveness/readiness signal must not
// flap on the API or any backend being briefly unavailable, so the check only
// reports that this server process is serving requests.

// Never cache or statically prerender: the probe must reach live code on every
// request so an unhealthy process cannot answer from a cached 200.
export const dynamic = 'force-dynamic';

export function GET(): Response {
  return new Response(null, { status: 200 });
}
