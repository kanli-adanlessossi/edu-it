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
