{{/*
Projected ServiceAccount credentials for the workloads that call the Kubernetes API (Api: terminal
attach through pods/exec; Worker: pod lifecycle and exec). The ServiceAccounts set
automountServiceAccountToken: false (Req 4.6), and so do these pod specs, so no default token volume
exists. This volume replaces it with the same three files the client library's in-cluster loader reads
from /var/run/secrets/kubernetes.io/serviceaccount (token, ca.crt, namespace), with:

- a bound token that expires in 600 s, the smallest value the API server accepts. The kubelet renews
  the file before it expires and the Kubernetes Python client re-reads it, so a copied token is
  useless after at most ten minutes and after the pod is deleted;
- the default audience (the API server), so the token cannot be replayed against another service;
- mode 0444 and a read-only mount: the containers run as a non-root user without a fixed UID.

Usage in a pod spec:
  automountServiceAccountToken: false
  volumes:  {{- include "edu-it.kubeApiAccess.volume" . | nindent 8 }}
  volumeMounts: {{- include "edu-it.kubeApiAccess.volumeMount" . | nindent 12 }}
*/}}

{{- define "edu-it.kubeApiAccess.mountPath" -}}
/var/run/secrets/kubernetes.io/serviceaccount
{{- end -}}

{{- define "edu-it.kubeApiAccess.volume" -}}
- name: kube-api-access
  projected:
    defaultMode: 292
    sources:
      - serviceAccountToken:
          path: token
          expirationSeconds: 600
      - configMap:
          name: kube-root-ca.crt
          items:
            - key: ca.crt
              path: ca.crt
      - downwardAPI:
          items:
            - path: namespace
              fieldRef:
                apiVersion: v1
                fieldPath: metadata.namespace
{{- end -}}

{{- define "edu-it.kubeApiAccess.volumeMount" -}}
- name: kube-api-access
  mountPath: {{ include "edu-it.kubeApiAccess.mountPath" . }}
  readOnly: true
{{- end -}}
