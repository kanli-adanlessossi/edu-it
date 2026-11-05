{{/*
Shared helpers for all EDU-IT templates (names, labels, images, namespaces, security contexts).
Templates branch only on Values_File keys, never on a Target name (Req 7.4).
Cross-key `fail` rules live in _validation.tpl (task 12.11).
*/}}

{{- define "edu-it.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "edu-it.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "edu-it.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/* Name of a component resource. Usage: include "edu-it.componentName" (dict "root" . "component" "api") */}}
{{- define "edu-it.componentName" -}}
{{- printf "%s-%s" (include "edu-it.fullname" .root) .component | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "edu-it.selectorLabels" -}}
app.kubernetes.io/name: {{ include "edu-it.name" .root }}
app.kubernetes.io/instance: {{ .root.Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{/* Common labels. Usage: include "edu-it.labels" (dict "root" . "component" "api") */}}
{{- define "edu-it.labels" -}}
helm.sh/chart: {{ include "edu-it.chart" .root }}
{{ include "edu-it.selectorLabels" . }}
app.kubernetes.io/version: {{ .root.Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .root.Release.Service }}
app.kubernetes.io/part-of: edu-it
{{- end -}}

{{- define "edu-it.platformNamespace" -}}
{{- .Values.namespaces.platform -}}
{{- end -}}

{{- define "edu-it.labsNamespace" -}}
{{- .Values.namespaces.labs -}}
{{- end -}}

{{/*
Image reference `repository@sha256:<64 hex>` (design D8). A missing or malformed digest fails
rendering with an error that names the workload (Req 7.11, 7.12).
Usage: include "edu-it.image" (dict "root" . "name" "api")
*/}}
{{- define "edu-it.image" -}}
{{- $img := default dict (index .root.Values.images .name) -}}
{{- $digest := default "" $img.digest | toString -}}
{{- if not (regexMatch "^sha256:[a-f0-9]{64}$" $digest) -}}
{{- fail (printf "images.%s.digest: workload %q needs a sha256 digest matching ^sha256:[a-f0-9]{64}$, got %q" .name .name $digest) -}}
{{- end -}}
{{- if not $img.repository -}}
{{- fail (printf "images.%s.repository: workload %q needs a repository" .name .name) -}}
{{- end -}}
{{- printf "%s@%s" $img.repository $digest -}}
{{- end -}}

{{/* Pod-level security context for platform workloads. */}}
{{- define "edu-it.podSecurityContext" -}}
runAsNonRoot: true
seccompProfile:
  type: RuntimeDefault
{{- end -}}

{{/* Container-level security context for platform workloads. */}}
{{- define "edu-it.containerSecurityContext" -}}
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
runAsNonRoot: true
capabilities:
  drop:
    - ALL
seccompProfile:
  type: RuntimeDefault
{{- end -}}

{{/* ServiceAccount name per component. Usage: include "edu-it.serviceAccountName" (dict "root" . "component" "api") */}}
{{- define "edu-it.serviceAccountName" -}}
{{- include "edu-it.componentName" . -}}
{{- end -}}

{{/*
Startup, liveness and readiness probes of a workload (Req 8.1). Every number and path comes from
`<component>.probes`; the schema bounds the period (1-60 s), timeout (1-30 s) and failure
threshold (1-30), and a timeout above its period fails rendering here, naming the workload.
Usage: include "edu-it.probes" (dict "root" . "component" "api" "port" "http")
*/}}
{{- define "edu-it.probes" -}}
{{- $probes := (index .root.Values .component).probes -}}
{{- $port := .port -}}
{{- $component := .component -}}
{{- range $kind := list "startupProbe" "livenessProbe" "readinessProbe" }}
{{- $probe := ternary $probes.startup (ternary $probes.liveness $probes.readiness (eq $kind "livenessProbe")) (eq $kind "startupProbe") }}
{{ $kind }}:
  httpGet:
    path: {{ $probe.path }}
    port: {{ $port }}
  initialDelaySeconds: {{ int $probe.initialDelaySeconds }}
  periodSeconds: {{ int $probe.periodSeconds }}
  timeoutSeconds: {{ int $probe.timeoutSeconds }}
  failureThreshold: {{ int $probe.failureThreshold }}
{{- end }}
{{- end -}}

{{/*
Optional secret volumes (Req 10, 23.1): each Secret is mounted at /var/run/secrets/edu-it/<name>/
with `optional: true`, so a Secret that does not exist yet leaves the pod not Ready instead of
starting it with empty credentials (the Api and the Worker check the required keys at start).
*/}}
{{- define "edu-it.secretVolumeMounts" -}}
{{- range $secret := (include "edu-it.secretNames" . | fromJsonArray) }}
- name: secret-{{ $secret }}
  mountPath: /var/run/secrets/edu-it/{{ $secret }}
  readOnly: true
{{- end }}
{{- end -}}

{{- define "edu-it.secretVolumes" -}}
{{- range $secret := (include "edu-it.secretNames" .root | fromJsonArray) }}
- name: secret-{{ $secret }}
  secret:
    secretName: {{ include "edu-it.componentName" (dict "root" $.root "component" $secret) }}
    optional: true
{{- end }}
{{- end -}}

{{/*
Secrets read by the Api, the Worker or the Migration_Job (Req 10.1, 10.2, 23.1): the single list
that the optional secret volumes and the ExternalSecrets are both generated from, so an unread
secret has no ExternalSecret. Each entry gives the `externalSecrets.remoteKeys` field that names
the remote secret and the keys the workloads read as files (the remote property carries the same name).
  database       Api, Worker, Migration_Job   url
  redis          Api, Worker                  url
  session        Api                          key
  llm            Worker                       api-key
  object-storage Api, Worker                  access-key, secret-key
*/}}
{{- define "edu-it.secretCatalog" -}}
{{- dict
  "database" (dict "remoteKey" "database" "keys" (list "url"))
  "redis" (dict "remoteKey" "redis" "keys" (list "url"))
  "session" (dict "remoteKey" "session" "keys" (list "key"))
  "llm" (dict "remoteKey" "llm" "keys" (list "api-key"))
  "object-storage" (dict "remoteKey" "objectStorage" "keys" (list "access-key" "secret-key"))
  | toJson -}}
{{- end -}}

{{- define "edu-it.secretNames" -}}
{{- keys (include "edu-it.secretCatalog" . | fromJson) | sortAlpha | toJson -}}
{{- end -}}
