# EDU-IT

[![CI](https://github.com/kanli-adanlessossi/edu-it/actions/workflows/ci.yml/badge.svg?branch=develop)](https://github.com/kanli-adanlessossi/edu-it/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

## Purpose

EDU-IT is a bilingual (French/English) re-learning platform for IT professionals who are losing hands-on skills as AI agents take over technical work. Learners rebuild skills step by step and advance only by proving mastery through quizzes, labs and projects. The platform runs on Kubernetes (EKS, AKS or on-premise).

**Contents:** [Prerequisites](#prerequisites) · [Quick start](#quick-start) · [Kubernetes](#kubernetes) · [Configuration](#configuration) · [Task runner](#task-runner) · [Repository layout](#repository-layout) · [Documentation](#documentation) · [Troubleshooting](#troubleshooting) · [License](#license) · [Français](#français)

## Prerequisites

| Tool | Version | Needed for |
|------|---------|------------|
| Docker | with Compose and Buildx | everything |
| [uv](https://docs.astral.sh/uv/) | any recent release | the Python workspace (every command here runs through `uv run`) |
| Python | 3.12 or newer | provisioned by `uv` from `pyproject.toml` |
| Node.js and npm | 22 (the version used here) | `apps/web` |
| `kind`, `kubectl`, `helm` | current | only for the Kubernetes tests |
| `make` | optional | the task runner; `tasks.ps1` and `tasks.sh` need nothing extra |

## Quick start

The fastest way to a running project is `start`, which brings up the whole stack on your machine:
PostgreSQL and Redis containers, the database schema, the learning content, the lab images, the
worker, the API (with real Docker lab sandboxes) and the web app.

```sh
./start.sh          # Windows: start.bat
```

The first run installs the dependencies, builds the images and the web app, and takes a few
minutes. When it finishes:

| Service | URL |
|---------|-----|
| Web (`/fr` and `/en`) | http://localhost:3000 |
| API | http://localhost:8000 |
| Worker readiness | http://localhost:8001/ready |

Stop everything with `./stop.sh` (`stop.bat`). It removes only what `start` created.

**Sign in as an admin.** `start` can seed a development admin and approve the synced content. Copy
[`.env.dev-admin.example`](.env.dev-admin.example) to `.env.dev-admin` (git-ignored) and set the
email and password before running `start`; without that file the seeding is skipped. Self-service
sign-up only creates learners: the verification link is written to `.e2e/mail.log`.

**Alternative: Docker Compose.** `deploy/compose` runs web, api, worker, postgres and redis as five
containers. It needs two extra commands (schema and content) and uses the fake lab sandbox. The steps
are in [deploy/compose/README.md](deploy/compose/README.md). Compose and `start` use the same
database ports, so run one at a time.

## Kubernetes

A local [kind](https://kind.sigs.k8s.io/) cluster is used by the Kubernetes and Helm tests. It is
separate from `start`:

```sh
./kind-up.sh        # Windows: kind-up.bat   (builds the lab images if they are missing)
./kind-down.sh      # Windows: kind-down.bat (asks before deleting the cluster)
```

To deploy to a real cluster (EKS, AKS or on-premise), follow the
[deployment guide](docs/DEPLOYMENT.md) and its go-live checklist.

## Configuration

Copy [`.env.example`](.env.example) to `.env` to run the apps outside Docker; it lists every
environment variable with a non-sensitive placeholder, and `.env` is git-ignored. Business
numbers (thresholds, points, intervals, timeouts) are not environment variables: they live in
`packages/domain/edu_it_domain/config.py`.

## Task runner

A single top-level task runner exposes four uniform entry points that CI and the pre-commit hooks
invoke. All three wrappers run the same commands:

| Command   | `make`           | Windows                 | POSIX shell            |
|-----------|------------------|-------------------------|------------------------|
| Lint      | `make lint`      | `./tasks.ps1 lint`      | `./tasks.sh lint`      |
| Format    | `make format`    | `./tasks.ps1 format`    | `./tasks.sh format`    |
| Typecheck | `make typecheck` | `./tasks.ps1 typecheck` | `./tasks.sh typecheck` |
| Test      | `make test`      | `./tasks.ps1 test`      | `./tasks.sh test`      |

Each command runs the TypeScript tooling (`apps/web`) and the Python tooling (`apps/api`,
`apps/worker`, `packages/*`). On a fresh checkout, install the workspace first, as CI does:
`uv sync --frozen --all-packages` and `npm ci --prefix apps/web` (`start` does both). The Docker,
kind and end-to-end suites are described in [docs/TESTING.md](docs/TESTING.md).

## Repository layout

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
    content/        # content pipeline: validate and sync authored content
  content/          # authored learning content (per track and locale)
  lab-images/       # lab sandbox image definitions (Alpine)
  prompts/          # versioned LLM prompts
  evals/            # evaluation suites for generated content
  deploy/
    compose/        # local Docker Compose stack
    images/         # Dockerfiles for web, api, migrations and worker
    kind/           # kind cluster configuration
    helm/           # Helm chart for Kubernetes
  tests/            # cross-package, chart, lab and kind test suites
  tools/            # local stack, kind harness, verification and docs checks
  docs/             # design documents, ADRs, reports
  .kiro/            # specs and steering
```

Dependencies point inward: `api → application → domain ← infrastructure`. The `packages/domain`
layer imports no web or API framework.

## Documentation

- [Deployment guide](docs/DEPLOYMENT.md) and its [design](docs/DEPLOYMENT-DESIGN.md)
- [Architecture](docs/ARCHITECTURE.md) and [data model](docs/DATA-MODEL.md)
- [API conventions](docs/API-CONVENTIONS.md)
- [Hands-on labs](docs/LABS.md) and their [design](docs/LABS-DESIGN.md)
- [AI content design](docs/AI-CONTENT-DESIGN.md)
- [Testing and verification](docs/TESTING.md)
- [Risks](docs/RISKS.md) and [rules review](docs/RULES-REVIEW.md)
- Architecture decision records: [docs/adr](docs/adr); phase reports: [docs/reports](docs/reports)

<details>
<summary>Steering files (the project rules)</summary>

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

</details>

## Troubleshooting

- **`start` stops with "port … is held by container …".** Another container uses 5432 or 6379.
  Stop it with the `docker stop <name>` command the message prints, or stop the Compose stack.
- **Lab image builds fail fetching packages.** The Alpine CDN can be unreachable. Set
  `ALPINE_MIRROR=https://mirror.leaseweb.com/alpine` and run again
  ([lab-images/README.md](lab-images/README.md)).
- **The Compose `worker` is not running.** It exits while the database schema is missing; apply the
  migration from [deploy/compose/README.md](deploy/compose/README.md), then restart it.
- **A service is not ready after `start`.** Read `.e2e/worker.log`, `.e2e/web.log` and
  `.e2e/mail.log` (the API's output), then run `./stop.sh` and start again.

## License

Released under the [MIT License](LICENSE).

## Français

EDU-IT est une plateforme bilingue (français/anglais) de remise à niveau pour les professionnels de l'informatique qui perdent leurs compétences pratiques à mesure que les agents d'IA prennent en charge le travail technique. Les apprenants reconstruisent leurs compétences pas à pas et ne progressent qu'en prouvant leur maîtrise par des quiz, des labs et des projets. La plateforme fonctionne sur Kubernetes (EKS, AKS ou sur site).

Démarrage rapide :

- Prérequis : Docker (avec Compose et Buildx), uv et Node.js 22.
- Tout lancer en local : `./start.sh` (Windows : `start.bat`). Le premier démarrage prend quelques minutes.
- Ouvrir l'interface sur http://localhost:3000 (`/fr` et `/en`) ; l'API répond sur http://localhost:8000.
- Pour un administrateur de développement, copier `.env.dev-admin.example` en `.env.dev-admin` avant de lancer.
- Tout arrêter : `./stop.sh` (Windows : `stop.bat`).
- Lancer les vérifications : `make lint` puis `make test`.

Pour déployer sur Kubernetes, consultez le [guide de déploiement](docs/DEPLOYMENT.md).
