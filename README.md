# EDU-IT

## Purpose

EDU-IT is a bilingual (French/English) re-learning platform for IT professionals who are losing hands-on skills as AI agents take over technical work. Learners rebuild skills step by step and advance only by proving mastery through quizzes, labs and projects. The platform runs on Kubernetes (EKS, AKS or on-premise).

## Quick start

Prerequisites: Docker with Compose, Python 3 and Node.js with npm.

```sh
# 1. Start the five-service local stack (web, api, worker, postgres, redis)
docker compose -f deploy/compose/docker-compose.yml up -d

# 2. Wait until web, api and worker all report ready (within 120 s)
python deploy/compose/wait_for_ready.py

# 3. Run the checks
make lint
make test
```

The UI is served at http://localhost:3000 (`/fr` and `/en`) and the API at http://localhost:8000. Copy `.env.example` to `.env` to run the apps outside Compose; see [deploy/compose/README.md](deploy/compose/README.md) for details. Stop the stack with `docker compose -f deploy/compose/docker-compose.yml down`.

## Français

EDU-IT est une plateforme bilingue (français/anglais) de remise à niveau pour les professionnels de l'informatique qui perdent leurs compétences pratiques à mesure que les agents d'IA prennent en charge le travail technique. Les apprenants reconstruisent leurs compétences pas à pas et ne progressent qu'en prouvant leur maîtrise par des quiz, des labs et des projets. La plateforme fonctionne sur Kubernetes (EKS, AKS ou sur site).

Démarrage rapide :

- Lancer la pile locale : `docker compose -f deploy/compose/docker-compose.yml up -d`
- Attendre que web, api et worker soient prêts : `python deploy/compose/wait_for_ready.py`
- Lancer les vérifications : `make lint` puis `make test`
- Ouvrir l'interface sur http://localhost:3000

Pour déployer sur Kubernetes, consultez le [guide de déploiement](docs/DEPLOYMENT.md).

## Documentation

- [Deployment guide](docs/DEPLOYMENT.md)
- [Architecture](docs/ARCHITECTURE.md)

Steering files:

- [ai-content](.kiro/steering/ai-content.md)
- [api-conventions](.kiro/steering/api-conventions.md)
- [auth-privacy](.kiro/steering/auth-privacy.md)
- [content](.kiro/steering/content.md)
- [deployment](.kiro/steering/deployment.md)
- [domain-rules](.kiro/steering/domain-rules.md)
- [labs](.kiro/steering/labs.md)
- [product](.kiro/steering/product.md)
- [quality](.kiro/steering/quality.md)
- [structure](.kiro/steering/structure.md)
- [tech](.kiro/steering/tech.md)
- [working-agreements](.kiro/steering/working-agreements.md)
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
    compose/        # local-development docker-compose stack
    helm/           # Helm chart for Kubernetes (see docs/DEPLOYMENT.md)
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
