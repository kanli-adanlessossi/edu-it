---
inclusion: fileMatch
fileMatchPattern: "apps/api/**|apps/worker/**|**/*{logging,logger,metrics,telemetry,tracing,health,readiness}*|deploy/**/*{monitor,metrics}*"
---
# Observability

This steering file is the single source of truth for EDU-IT observability rules (health,
readiness, structured logs, request IDs, traces and metrics). The rules are transcribed from the
foundations and kubernetes-deployment specs; numbers and normative wording are preserved.

## Health and Readiness

- Api and Worker expose a Health_Endpoint (liveness) determined independent of external
  dependencies.
- Api and Worker expose a Readiness_Endpoint that returns ready only when all required
  dependencies are reachable, and not-ready otherwise.

## Structured Logs and Request IDs

- Api and Worker write each log record as a single-line JSON object.
- Every request-scoped record carries the Request_ID; every lab-scoped record carries the
  Lab_Session identifier.
- When a request has no inbound Request_ID, the Api generates one.
- The Api propagates the Request_ID into every Worker job it enqueues and into the trace context,
  so Worker records and spans for the job share the same Request_ID and trace.
- Internal diagnostics stay in logs; no stack traces, SQL or provider errors appear in responses
  (#[[file:.kiro/steering/quality.md]]).
- Terminal I/O, secrets and learner personal data are excluded from log records, trace attributes
  and metric labels. Lab_Metrics never use the learner identifier as a label.

## Traces and Metrics

- Where a collector endpoint is set in the Values_File, Api and Worker export OpenTelemetry traces.
- If the collector is unreachable or rejects an export, spans are dropped after at most 3 export
  attempts, serving continues, and request and job latency changes by no more than 50 ms at the
  95th percentile.
- Api and Worker expose Prometheus metrics (including Lab_Metrics) on a dedicated metrics port,
  separate from the port serving learner traffic.
- A Values_File key selects ServiceMonitor mode or scrape-annotation mode, never both. Any other
  value fails chart rendering with an error naming the key and the two accepted values
  (#[[file:.kiro/steering/deployment.md]]).
