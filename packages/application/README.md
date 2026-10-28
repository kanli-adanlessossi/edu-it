# packages/application

Application layer: use cases that orchestrate the domain and its ports (per
`.kiro/steering/structure.md`). Depends inward on `packages/domain` only.

Lint, format, typecheck, and test commands are wired by a later task and are
invoked uniformly through the top-level task runner (`Makefile`).
