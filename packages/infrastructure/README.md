# packages/infrastructure

Infrastructure layer: concrete implementations of the domain ports (PostgreSQL
repositories, Redis session and queue adapters, SMTP mailer, object storage) and
the in-memory / console fakes (per `.kiro/steering/structure.md`). Depends
inward on `packages/domain`; it implements the domain ports.

Lint, format, typecheck, and test commands are wired by a later task and are
invoked uniformly through the top-level task runner (`Makefile`).
