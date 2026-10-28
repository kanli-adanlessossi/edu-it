# apps/api

FastAPI boundary for authentication, sessions, and HTTP endpoints, validating
input with Pydantic v2 (per `.kiro/steering/structure.md`). This is a thin
boundary over the shared Python package; dependencies point inward
(`api → application → domain ← infrastructure`).

Lint, format, typecheck, and test commands for this app are wired by a later
task and are invoked uniformly through the top-level task runner (`Makefile`)
targets `lint`, `format`, `typecheck`, and `test`.

## OpenAPI publication (Req 16.1)

The API publishes exactly one OpenAPI document per major version, path-prefixed
`/v{major}` (per `docs/API-CONVENTIONS.md` Section 3). Each supported major
version is a mounted sub-application (see `edu_it_api/versioning.py`) whose single
OpenAPI document is served at `/v{major}/openapi.json` (e.g. `/v1/openapi.json`).

The `apps/web` build obtains the targeted major version's document through the
exporter CLI, which renders that same per-version document without a running
server:

```
python -m edu_it_api.openapi_export --major 1 --out <path>
```

It exits non-zero and reports the reason for an unknown major version, writing
no document.
