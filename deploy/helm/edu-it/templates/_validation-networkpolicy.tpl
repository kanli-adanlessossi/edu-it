{{/*
Fails rendering, naming networkPolicy.kubernetesApi.cidrs, when the list is empty.
NetworkPolicy templates must call this before rendering any object.
Usage: {{ include "edu-it.validateNetworkPolicy" . }}
*/}}
{{- define "edu-it.validateNetworkPolicy" -}}
{{- $np := default dict .Values.networkPolicy -}}
{{- $api := default dict $np.kubernetesApi -}}
{{- if not $api.cidrs -}}
{{- fail "networkPolicy.kubernetesApi.cidrs must be a non-empty list of CIDRs or API server endpoint addresses (find them with: kubectl get endpoints kubernetes -n default)" -}}
{{- end -}}
{{- end -}}
