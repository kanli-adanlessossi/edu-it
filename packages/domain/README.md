# packages/domain

Framework-free domain layer: ports, business config, and entities (per
`.kiro/steering/structure.md`). This package imports no web or API framework
(Req 1.8). Business numbers (TTLs, session duration, rate-limit threshold and
window, password policy) live in `config.py`.

Ports defined here: `Clock`, `Mailer`, `ObjectStorage`, `TaskQueue`,
`LLMProvider` (and later extension points). Each has a fake.

Lint, format, typecheck, and test commands are wired by a later task and are
invoked uniformly through the top-level task runner (`Makefile`).
