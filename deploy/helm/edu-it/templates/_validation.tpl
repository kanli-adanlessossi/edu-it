{{/*
Cross-key validation rules (task 12.11; Req 7.7, 7.8, 8.3, 8.8, 9.3, 9.5, 9.9, 10.4, 10.6, 11.14,
13.7, 14.4, 21.4). One helper per rule family. Every `fail` message starts with the offending key and
quotes the offending value. `validate.yaml` includes `edu-it.validate`; a `fail` in any template stops
the whole render, so a bad Values_File renders no manifest. values.schema.json holds the per-key
types, ranges and enums that Helm checks before any template runs; this file holds the rules that
compare two keys or depend on the Target.
The domain rule for termination grace is `min_termination_grace` (drain + preStop + 10).
`kindTest.sessionTtlSeconds` is not checked here: it lives in ci/values-kind-test.yaml, an overlay
the schema accepts, and tools/check_values_consistency.py rejects it in the four Values_Files.
*/}}

{{- define "edu-it.validate" -}}
{{- include "edu-it.validate.replicas" . -}}
{{- include "edu-it.validate.ingress" . -}}
{{- include "edu-it.validate.terminationGrace" . -}}
{{- include "edu-it.validate.migration" . -}}
{{- include "edu-it.validate.externalSecrets" . -}}
{{- include "edu-it.validate.llmRegion" . -}}
{{- include "edu-it.validate.scrapeMode" . -}}
{{- include "edu-it.validate.probes" . -}}
{{- include "edu-it.validate.images" . -}}
{{- end -}}

{{/* Replica counts: 1 to 100 (Req 8.3). */}}
{{- define "edu-it.validate.replicas" -}}
{{- range $component := list "api" "worker" "web" -}}
{{- $replicas := int (index $.Values $component).replicas -}}
{{- if or (lt $replicas 1) (gt $replicas 100) -}}
{{- fail (printf "%s.replicas: %v is outside the allowed range 1 to 100" $component (index $.Values $component).replicas) -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/* Ingress mode, certificate reference and WebSocket timeouts (Req 9.3, 9.5, 9.9). */}}
{{- define "edu-it.validate.ingress" -}}
{{- $mode := .Values.ingress.mode -}}
{{- if not (has $mode (list "ingress-nginx" "gateway-api")) -}}
{{- fail (printf "ingress.mode: invalid value %q, allowed values are \"ingress-nginx\" and \"gateway-api\"" (toString $mode)) -}}
{{- end -}}
{{- if not .Values.ingress.tls.secretName -}}
{{- fail (printf "ingress.tls.secretName: a certificate reference is required for ingress.mode %q" $mode) -}}
{{- end -}}
{{- $ttl := int .Values.labs.sessionTtlSeconds -}}
{{- range $key := list "readTimeoutSeconds" "sendTimeoutSeconds" -}}
{{- $value := index $.Values.ingress.websocket $key -}}
{{- if lt (int $value) $ttl -}}
{{- fail (printf "ingress.websocket.%s: %v must be at least labs.sessionTtlSeconds (%v)" $key $value $ttl) -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/* Termination grace is at least drain + preStop delay + 10 (Req 14.4). The Worker has neither, so its minimum is 10. */}}
{{- define "edu-it.validate.terminationGrace" -}}
{{- $api := .Values.api -}}
{{- if lt (int $api.terminationGracePeriodSeconds) (add (int $api.preStopDelaySeconds) (int $api.drainPeriodSeconds) 10) -}}
{{- fail (printf "api.terminationGracePeriodSeconds: %v must be at least 10 seconds greater than api.preStopDelaySeconds (%v) plus api.drainPeriodSeconds (%v)" $api.terminationGracePeriodSeconds $api.preStopDelaySeconds $api.drainPeriodSeconds) -}}
{{- end -}}
{{- if lt (int .Values.worker.terminationGracePeriodSeconds) 10 -}}
{{- fail (printf "worker.terminationGracePeriodSeconds: %v must be at least 10 (the Worker requeues unfinished jobs 5 s before it elapses)" .Values.worker.terminationGracePeriodSeconds) -}}
{{- end -}}
{{- end -}}

{{/* Migration Job bounds: backoffLimit 0 to 6, activeDeadlineSeconds 30 to 3600 (Req 8.8). */}}
{{- define "edu-it.validate.migration" -}}
{{- $m := .Values.migration -}}
{{- if or (lt (int $m.backoffLimit) 0) (gt (int $m.backoffLimit) 6) -}}
{{- fail (printf "migration.backoffLimit: %v is outside the allowed range 0 to 6" $m.backoffLimit) -}}
{{- end -}}
{{- if or (lt (int $m.activeDeadlineSeconds) 30) (gt (int $m.activeDeadlineSeconds) 3600) -}}
{{- fail (printf "migration.activeDeadlineSeconds: %v is outside the allowed range 30 to 3600" $m.activeDeadlineSeconds) -}}
{{- end -}}
{{- end -}}

{{/* ExternalSecret store name and one remote key per secret in the catalog, unless dev secrets are used (Req 10.4). */}}
{{- define "edu-it.validate.externalSecrets" -}}
{{- if not .Values.devSecrets.enabled -}}
{{- if not .Values.externalSecrets.storeName -}}
{{- fail "externalSecrets.storeName: required when devSecrets.enabled is false" -}}
{{- end -}}
{{- $catalog := include "edu-it.secretCatalog" . | fromJson -}}
{{- range $name := (include "edu-it.secretNames" . | fromJsonArray) -}}
{{- $entry := index $catalog $name -}}
{{- if not (index $.Values.externalSecrets.remoteKeys $entry.remoteKey) -}}
{{- fail (printf "externalSecrets.remoteKeys.%s: required when devSecrets.enabled is false (the %s secret is read by the workloads)" $entry.remoteKey $name) -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
LLM region is an EU region from ADR 0007 unless environment is development (Req 10.6).
Keep this list equal to EU_LLM_REGIONS in tools/check_llm_region.py (a chart test compares them).
*/}}
{{- define "edu-it.validate.llmRegion" -}}
{{- $eu := list "eu-central-1" "eu-north-1" "eu-south-1" "eu-south-2" "eu-west-1" "eu-west-3" -}}
{{- if and (ne (toString .Values.environment) "development") (not (has .Values.llm.region $eu)) -}}
{{- fail (printf "llm.region: %q is not an EU region allowed by docs/adr/0007-llm-provider-and-data-residency.md (allowed: %s)" (toString .Values.llm.region) (join ", " $eu)) -}}
{{- end -}}
{{- end -}}

{{/* Scrape mode (Req 13.7). */}}
{{- define "edu-it.validate.scrapeMode" -}}
{{- $mode := .Values.observability.scrapeMode -}}
{{- if not (has $mode (list "serviceMonitor" "annotations")) -}}
{{- fail (printf "observability.scrapeMode: invalid value %q, allowed values are \"serviceMonitor\" and \"annotations\"" (toString $mode)) -}}
{{- end -}}
{{- end -}}

{{/* Probe bounds: period 1-60, timeout 1-30 and at most the period, failure threshold 1-30 (Req 8.3). */}}
{{- define "edu-it.validate.probes" -}}
{{- range $component := list "api" "worker" "web" -}}
{{- range $kind := list "startup" "liveness" "readiness" -}}
{{- $p := index (index $.Values $component).probes $kind -}}
{{- $where := printf "%s.probes.%s" $component $kind -}}
{{- if or (lt (int $p.periodSeconds) 1) (gt (int $p.periodSeconds) 60) -}}
{{- fail (printf "%s.periodSeconds: %v is outside the allowed range 1 to 60" $where $p.periodSeconds) -}}
{{- end -}}
{{- if or (lt (int $p.timeoutSeconds) 1) (gt (int $p.timeoutSeconds) 30) -}}
{{- fail (printf "%s.timeoutSeconds: %v is outside the allowed range 1 to 30" $where $p.timeoutSeconds) -}}
{{- end -}}
{{- if gt (int $p.timeoutSeconds) (int $p.periodSeconds) -}}
{{- fail (printf "%s.timeoutSeconds: %v must be less than or equal to periodSeconds (%v)" $where $p.timeoutSeconds $p.periodSeconds) -}}
{{- end -}}
{{- if or (lt (int $p.failureThreshold) 1) (gt (int $p.failureThreshold) 30) -}}
{{- fail (printf "%s.failureThreshold: %v is outside the allowed range 1 to 30" $where $p.failureThreshold) -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/* Every workload image has a repository and a sha256 digest (Req 7.11, 7.12, 21.4); the check lives in edu-it.image. */}}
{{- define "edu-it.validate.images" -}}
{{- range $name := list "web" "api" "worker" "migrations" -}}
{{- $_ := include "edu-it.image" (dict "root" $ "name" $name) -}}
{{- end -}}
{{- end -}}
