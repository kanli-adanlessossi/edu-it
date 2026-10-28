# deploy/compose

Local-development Docker Compose definition only. No Kubernetes artifact is
produced in this phase (Req 20.4). The Compose stack runs **exactly five
services** — `web`, `api`, `worker`, `postgres`, `redis` (Req 3.1).

## Files

- `docker-compose.yml` — the five-service stack. `api` and `worker` each connect
  to both `postgres` and `redis` (Req 3.7), consistent with the connectivity
  intent in `.kiro/steering/deployment.md`.
- `wait_for_ready.py` — the **aggregate readiness wait** (Req 3.4–3.6). It polls
  the Readiness_Endpoints of `web`, `api`, and `worker` and passes **only** when
  all three report ready within 120 seconds; any single timeout fails the whole
  check and the failure report names the service that did not become ready. This
  is a dedicated wait script, not a per-container healthcheck, so the
  all-or-nothing aggregate semantics are explicit and observable. The pure
  decision it relies on is
  `edu_it_application.compose_readiness.decide_compose_readiness`.

## Usage

Start the whole stack with one command (run from this directory):

```sh
docker compose up -d
```

A single start command brings all five services to the running state; if any one
fails to start, Compose reports which service did not start and leaves the others
unchanged (Req 3.2, 3.3).

Then wait for aggregate readiness (run inside the workspace venv so the
`edu_it_application` package is importable):

```sh
python deploy/compose/wait_for_ready.py
```

The script exits `0` only when `web`, `api`, and `worker` all report ready within
the 120-second window, and non-zero (naming the timed-out service) otherwise. It
is the command CI and the phase-1 report invoke to assert the stack came up.

## Service images

The `web`, `api`, and `worker` services build from per-app Dockerfiles
(`apps/web/Dockerfile`, `apps/api/Dockerfile`, `apps/worker/Dockerfile`). Those
Dockerfiles carry each service's runtime entrypoint (the API's uvicorn server,
the worker's queue consumer plus its health surface, and the Next.js build) and
are added alongside those entrypoints. `postgres` and `redis` use the official
pinned images and need no build.
