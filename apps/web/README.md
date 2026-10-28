# apps/web

Next.js App Router UI serving the `/fr` and `/en` route trees (per
`.kiro/steering/structure.md`). This is a thin boundary over the shared Python
package's domain and application layers, calling `apps/api` exclusively through
the generated OpenAPI TypeScript client.

## Tooling

The TypeScript lint, format, typecheck, and test commands are defined as
`package.json` scripts and pinned to the exact versions recorded in
`docs/adr/0001-versions.md`:

| Command        | Script                 | Tool                               |
| -------------- | ---------------------- | ---------------------------------- |
| lint           | `npm run lint`         | ESLint (`eslint .`)                |
| format (write) | `npm run format`       | Prettier (`prettier --write .`)    |
| format (check) | `npm run format:check` | Prettier (`prettier --check .`)    |
| typecheck      | `npm run typecheck`    | TypeScript (`tsc --noEmit`)        |
| test           | `npm run test`         | Vitest (`vitest run`) + fast-check |
| build          | `npm run build`        | client generation + `next build`   |

Each command exits non-zero and reports the offending file path with the
rule/location on a lint violation, type error, or test failure, and exits zero
when clean.

## OpenAPI → TypeScript client generation (Req 16)

`apps/web` calls `apps/api` exclusively through a typed client generated from
the API's OpenAPI document (Req 16.3). The generation runs as the **first** step
of `npm run build`, before Next.js compiles application code (Req 16.2):

```
npm run build  ==  node scripts/build-client.mjs && next build
```

`scripts/build-client.mjs` targets a major version (`API_MAJOR_VERSION`,
default `1`), (re)exports that version's document from `apps/api`
(`python -m edu_it_api.openapi_export`) to `openapi/v{major}.json`, and
generates `src/generated/api-client.ts` from it via
`scripts/generate-client.mjs`. Both `openapi/` and `src/generated/` are build
artifacts (git-ignored) regenerated on every build.

Failure contract:

- **Missing document** — the build exits non-zero and reports that the OpenAPI
  document was not found, producing no client (Req 16.4).
- **Malformed document** — the build exits non-zero, produces no client, and
  reports the malformation (Req 16.5).

Run `npm run generate:client` to (re)generate the client on its own.

They are invoked uniformly through the top-level task runner — `make lint`,
`make format`, `make typecheck`, `make test` (or the `tasks.sh` / `tasks.ps1`
wrappers), which fan out to the `-web` sub-targets that call these scripts.

Run `npm install` in this directory once to install the pinned toolchain.
