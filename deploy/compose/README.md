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

Run these from the repository root, inside the workspace environment (`uv run ...`, so the
`edu_it_*` packages are importable).

1. Build and start the whole stack with one command:

   ```sh
   docker compose -f deploy/compose/docker-compose.yml up -d --build
   ```

   A single start command brings all five services to the running state; if any one
   fails to start, Compose reports which service did not start and leaves the others
   unchanged (Req 3.2, 3.3).

2. Create the schema and load the content. The `api` does not migrate on startup and the stack
   has no sixth service for it (Req 3.1), so run both against the published PostgreSQL port:

   ```sh
   export DATABASE_URL=postgresql://edu_it:change-me@localhost:5432/edu_it
   uv run python -m edu_it_infrastructure.migrate
   uv run python -c "import sys; from edu_it_content.cli import main; sys.exit(main(['sync', '--database-url', '$DATABASE_URL']))"
   ```

   (PowerShell: `$env:DATABASE_URL = '...'`.) Until the schema exists the `worker` exits on
   missing tables; once it is applied, `docker compose ... restart worker` brings it up.

3. Wait for aggregate readiness:

   ```sh
   uv run python deploy/compose/wait_for_ready.py
   ```

   The script exits `0` only when `web`, `api`, and `worker` all report ready within the
   120-second window, and non-zero (naming the timed-out service) otherwise. It is the command
   CI and the phase-1 report invoke to assert the stack came up.

The web app is at http://localhost:3000 and the API at http://localhost:8000. Stop with
`docker compose -f deploy/compose/docker-compose.yml down` (add `-v` to also delete the database
and Redis volumes).

Compose publishes PostgreSQL on 5432 and Redis on 6379, the same ports as `tools/e2e_stack.py`
(`start.sh` / `start.bat`). Run one of the two at a time.

## Service images

The `web`, `api`, and `worker` services build from the same Dockerfiles CI builds for the Helm
chart, under `deploy/images/` (`web/Dockerfile`, `api/Dockerfile`, `worker/Dockerfile`), each with
its final stage as `target` (`final`, `api`, `worker`). `postgres` and `redis` use the official
pinned images and need no build.

The web image inlines `NEXT_PUBLIC_API_BASE_URL` at build time, so Compose passes it as a build
arg (`http://localhost:8000/v1`, the address the browser uses); a runtime variable would have no
effect. The server-side calls use `API_INTERNAL_BASE_URL=http://api:8000/v1` over the Compose
network. The Helm image leaves the build arg empty and uses same-origin `/v1` behind the ingress.

Labs run against the fake sandbox provider here (`SANDBOX_PROVIDER` defaults to `fake`, and no
Docker socket is mounted), so no real lab containers start. Use `start.sh` / `start.bat`, which
select the Docker provider, for real labs.
