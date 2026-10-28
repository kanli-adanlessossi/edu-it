# apps/worker

Task-queue consumer (per `.kiro/steering/structure.md`). In the foundations
phase the worker is present and healthy (consuming the Redis-backed queue) but
carries no learning-feature job yet. This is a thin boundary over the shared
Python package.

Lint, format, typecheck, and test commands for this app are wired by a later
task and are invoked uniformly through the top-level task runner (`Makefile`)
targets `lint`, `format`, `typecheck`, and `test`.
