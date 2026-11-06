{{/*
Helpers for the platform NetworkPolicies (networkpolicy-platform.yaml; Req 11.1 to 11.6, 11.13).
Branch only on Values_File keys (Req 7.4).
*/}}

{{/* PostgreSQL and Redis ports. Fixed by the datastores, not by a Target. */}}
{{- define "edu-it.postgresPort" -}}5432{{- end -}}
{{- define "edu-it.redisPort" -}}6379{{- end -}}

{{/*
TCP port of an endpoint URL: the explicit `:port` when present, otherwise 80 for http and 443 for
anything else (an empty endpoint means the provider's default HTTPS endpoint).
Usage: include "edu-it.endpointPort" "https://host:8443/path"
*/}}
{{- define "edu-it.endpointPort" -}}
{{- $url := default "" . | toString -}}
{{- if regexMatch "^[A-Za-z][A-Za-z0-9+.-]*://[^/:]+:[0-9]+" $url -}}
{{- regexReplaceAll "^[A-Za-z][A-Za-z0-9+.-]*://[^/:]+:([0-9]+).*$" $url "${1}" -}}
{{- else if hasPrefix "http://" $url -}}
80
{{- else -}}
443
{{- end -}}
{{- end -}}

{{/*
Egress to the Kubernetes API as `ipBlock` rules (a pod selector cannot match the API server), one
per entry of networkPolicy.kubernetesApi.cidrs. An entry without a prefix length is an endpoint
address and becomes /32 (IPv4) or /128 (IPv6). Ports 443 and 6443 cover managed and self-managed
control planes. Fails, naming the key, when the list is empty (Req 11.13, 11.14).
*/}}
{{- define "edu-it.kubeApiEgressRule" -}}
{{- include "edu-it.validateNetworkPolicy" . -}}
- to:
    {{- range .Values.networkPolicy.kubernetesApi.cidrs }}
    {{- $cidr := toString . }}
    {{- if not (contains "/" $cidr) }}{{ $cidr = printf "%s/%s" $cidr (ternary "128" "32" (contains ":" $cidr)) }}{{ end }}
    - ipBlock:
        cidr: {{ $cidr | quote }}
    {{- end }}
  ports:
    - protocol: TCP
      port: 443
    - protocol: TCP
      port: 6443
{{- end -}}

{{/* Egress to cluster DNS, port 53 over UDP and TCP. */}}
{{- define "edu-it.dnsEgressRule" -}}
- to:
    - namespaceSelector:
        matchLabels:
          kubernetes.io/metadata.name: kube-system
      podSelector:
        matchLabels:
          k8s-app: kube-dns
  ports:
    - protocol: UDP
      port: 53
    - protocol: TCP
      port: 53
{{- end -}}
