# Testing and verification

How to run the EDU-IT checks, and the rules that keep the heavier suites from interfering with each
other. For the everyday lint, format, typecheck and test commands, see the task runner table in the
[README](../README.md#task-runner).

## Suites and markers

`uv run pytest` runs the default suite. Two markers select the suites that need external systems;
without them these tests are skipped with the reason, so the default run is unaffected:

| Marker | Needs | Run with |
|--------|-------|----------|
| `docker` | a reachable Docker daemon and the locally built lab images (`edu-it/lab-*:dev`) | `uv run pytest -m docker` |
| `kind` | a reachable kind cluster (context `kind-edu-it`, or `EDU_IT_KIND_CONTEXT`) and `kubectl` | `uv run pytest -m kind --no-cov` |

- Build the lab images with `cd lab-images && docker buildx bake --load` (see
  [lab-images/README.md](../lab-images/README.md)). Set `ALPINE_MIRROR` if the Alpine CDN is
  unreachable.
- Create the kind cluster with `./kind-up.sh` (`kind-up.bat` on Windows) and remove it with
  `./kind-down.sh`. See the [Deployment guide](DEPLOYMENT.md) for the chart under test.

## The Real_Stack

The Real_Stack is the local stack that `tools/e2e_stack.py up` starts and that `start.sh` /
`start.bat` wrap: PostgreSQL and Redis containers, then the worker, API and web as local processes,
with the API on the Docker sandbox provider. `tools/e2e_stack.py down` (`stop.sh` / `stop.bat`)
removes only what `up` started.

## Verification notes

- Existing databases need one `edu-it-content sync --database-url <url>` after upgrading; it writes
  the module prerequisite gates and the question-to-concept links.
- Run the Docker-marked tests (`labs-docker`) with the Real_Stack down. The Real_Stack worker reaps
  `edu-it.managed` containers, so `run_verification.py` reports `labs-docker` as blocked while the
  worker listens on port 8001.
- Gate procedure: phase 1 (stack down, `ALPINE_MIRROR` set if the Alpine CDN is unreachable) runs
  `python tools/run_verification.py --suite labs`; phase 2 runs `tools/e2e_stack.py up` and then
  `--only labs-playwright-real labs-performance`.
- Tests never reset `DATABASE_URL`. The PostgreSQL migration test runs only when
  `EDU_IT_TEST_DATABASE_URL` names a throwaway database.
- Compose (`deploy/compose`) and the Real_Stack publish the same PostgreSQL and Redis ports (5432
  and 6379). Run one of the two at a time.
