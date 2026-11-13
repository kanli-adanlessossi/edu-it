---
inclusion: fileMatch
fileMatchPattern: "package.json|pyproject.toml|.pre-commit-config.yaml|.env.example|docker-compose*.y*ml|compose*.y*ml|.github/workflows/**|Makefile"
---
# Developer Workflow and Continuous Integration

This steering file is the single source of truth for EDU-IT developer tooling, local stack and CI
gate rules. The rules are transcribed from the foundations spec; numbers and normative wording are
preserved.

## Monorepo Tooling

- Lint, format, typecheck and test commands exist for the TypeScript code (Web_App) and for the
  Python code (Api, Worker, Shared_Package). Versions are pinned per `docs/adr/0001-versions.md`.
- Each command exits non-zero on a violation, type error or failing test, reporting file path and
  rule or location; it exits zero when clean.
- Shared_Package imports no web or API framework (#[[file:.kiro/steering/structure.md]]).

## Pre-commit and Environment Template

- Pre-commit hooks run lint and format on the staged files and typecheck when a relevant file is
  staged (the web typecheck runs project-wide, not per file). They block the commit when any
  hook fails (leaving changes staged and naming the failed hook), and allow it when all pass.
- `.env.example` at the repository root lists every environment variable read by Web_App, Api and
  Worker, one per line, with non-sensitive placeholders and no real secret, token or production
  value.

## Local Compose Stack

- The Compose_Stack defines exactly five services: Web_App, Api, Worker, PostgreSQL and Redis.
- All five reach the running state; a failing service is reported by name.
- Readiness_Endpoints of Web_App, Api and Worker must all report ready within 120 seconds of the
  start command; any one missing fails the whole readiness check and is reported by name.
- Api and Worker each connect to PostgreSQL and to Redis.

## Continuous Integration

- On a push to any branch, CI runs lint, typecheck, test, dependency-audit and secret-scan; any
  error fails the pipeline and names the failing step.
- The test step runs the end-to-end authentication tests (sign-up, email verification, sign-in,
  sign-out, password reset).
- GitHub-OAuth tests run against a mock OAuth_Provider and call no external provider.
- Deployment-pipeline rules (images, SBOM, release gate) are in
  #[[file:.kiro/steering/deployment.md]].
