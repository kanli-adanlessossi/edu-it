{{/*
Observability helpers (task 12.7; Req 13.1, 13.5, 13.6).
Branch only on Values_File keys (Req 7.4). `observability.scrapeMode` is `serviceMonitor` or
`annotations`; the cross-key `fail` for any other value is added in task 12.11 (Req 13.7), the
schema enum already rejects it.
*/}}

{{/* Metrics port of a component (`api` or `worker`). Usage: include "edu-it.metricsPort" (dict "root" . "component" "api") */}}
{{- define "edu-it.metricsPort" -}}
{{- int (index .root.Values .component).metricsPort -}}
{{- end -}}

{{/*
Prometheus scrape annotations for the pod template of the Api and the Worker. Renders only in
scrape-annotation mode, so ServiceMonitor mode carries no scrape annotations (Req 13.5, 13.6).
Output is a list of `key: value` lines; guard the `annotations:` key with
`include "edu-it.scrapeAnnotations" ... | trim`.
Usage: include "edu-it.scrapeAnnotations" (dict "root" . "component" "api")
*/}}
{{- define "edu-it.scrapeAnnotations" -}}
{{- if eq .root.Values.observability.scrapeMode "annotations" -}}
prometheus.io/scrape: "true"
prometheus.io/port: {{ include "edu-it.metricsPort" . | quote }}
prometheus.io/path: /metrics
{{- end -}}
{{- end -}}

{{/*
OpenTelemetry collector endpoint env var for the Api and the Worker (Req 13.1). Rendered only when
`observability.collectorEndpoint` is not empty; with no endpoint the apps build no exporter.
*/}}
{{- define "edu-it.otelEnv" -}}
{{- with .Values.observability.collectorEndpoint -}}
- name: OTEL_EXPORTER_OTLP_ENDPOINT
  value: {{ . | quote }}
{{- end -}}
{{- end -}}
