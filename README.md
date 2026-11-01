# EDU-IT

Bilingual (French/English) re-learning platform. This repository is a monorepo.

## Layout

```
edu-it/
  apps/
    web/            # Next.js App Router UI (/fr and /en)
    api/            # FastAPI boundary
    worker/         # task-queue consumer
  packages/
    domain/         # framework-free domain (ports, config, entities)
    application/    # use cases orchestrating domain + ports
    infrastructure/ # concrete port implementations (PG, Redis, SMTP, ...) + fakes
  deploy/
    compose/        # local-development docker-compose only (no K8s this phase)
  docs/             # design documents, ADRs, reports
  .kiro/            # specs and steering
```

Dependencies point inward: `api → application → domain ← infrastructure`. The
`packages/domain` layer imports no web or API framework.

## Task runner

A single top-level task runner exposes four uniform entry points that CI and the
pre-commit hooks invoke. Use whichever wrapper suits your platform — all three
expose the same commands:

| Command     | `make`            | Windows                   | POSIX shell          |
|-------------|-------------------|---------------------------|----------------------|
| Lint        | `make lint`       | `./tasks.ps1 lint`        | `./tasks.sh lint`    |
| Format      | `make format`     | `./tasks.ps1 format`      | `./tasks.sh format`  |
| Typecheck   | `make typecheck`  | `./tasks.ps1 typecheck`   | `./tasks.sh typecheck` |
| Test        | `make test`       | `./tasks.ps1 test`        | `./tasks.sh test`    |

Each command fans out to the TypeScript tooling (`apps/web`) and the Python
tooling (`apps/api`, `apps/worker`, `packages/*`). The concrete tool
invocations behind each entry point are wired by a later task.

## Real stack and verification notes

- Existing databases need one `edu-it-content sync --database-url <url>` after upgrading; it writes the module prerequisite gates and the question-to-concept links.
- Run the Docker-marked tests (`labs-docker`) with the Real_Stack down. The Real_Stack worker reaps `edu-it.managed` containers, so `run_verification.py` reports `labs-docker` as blocked while the worker listens on port 8001.
- Gate procedure: phase 1 (stack down, `ALPINE_MIRROR` set if the Alpine CDN is unreachable) runs `python tools/run_verification.py --suite labs`; phase 2 runs `tools/e2e_stack.py up` and then `--only labs-playwright-real labs-performance`.
- Tests never reset `DATABASE_URL`. The PostgreSQL migration test runs only when `EDU_IT_TEST_DATABASE_URL` names a throwaway database.
