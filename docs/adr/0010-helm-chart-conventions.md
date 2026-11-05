# ADR 0010: Helm Chart Conventions

- **ID:** A10
- **Status:** Accepted
- **Date:** 2025-01-01
- **Decision topic:** Helm chart `deploy/helm/edu-it` (values contract, hooks and bootstrap resources, extension kinds, validation rules, secret catalog)
- **Finalized in:** task 12.15 of the `kubernetes-deployment` spec (status was `Draft` since task 1.1)

## Context

The platform must run unchanged on EKS, AKS and on-premise using standard Kubernetes APIs only,
with every provider difference isolated in Helm values (see
#[[file:.kiro/steering/deployment.md]]). One chart at `deploy/helm/edu-it` serves four Targets
through four Values_Files: `values-dev.yaml`, `values-eks.yaml`, `values-aks.yaml` and
`values-onprem.yaml`.

Three questions need a recorded answer:

- How are provider differences expressed so templates never branch on a Target name, a cloud API
  probe or a Kubernetes version (Req 7.2 to 7.4)?
- How do pre-install hooks (the migration Job) get the resources they need, without Helm
  deleting data they own (Req 8.8, 8.9)?
- Which non-core kinds may the chart render, so portability is checkable (Req 7.2)?

Open questions OQ-3, OQ-4 and OQ-8 also land here: drain protection without a cluster-scoped
grant, monitoring access to the Api, and the `web` workload.

## Decision

**Values contract.** Provider-specific settings exist only in values. Templates branch only on
Values_File keys. `values.schema.json` declares every required key with type, enum, numeric range
and the image digest pattern `^sha256:[a-f0-9]{64}$`; a missing or mistyped key fails `helm lint`
and `helm template` with an error naming the key path and renders nothing (Req 7.7, 7.8, 7.12).
Rules that cross keys, or depend on the Target, are `fail` calls. Each message starts with the
offending key and quotes the offending value, and a `fail` anywhere stops the whole render, so a
bad Values_File renders no manifest. They live in these files:

- `templates/_validation.tpl`, entry point `edu-it.validate` (included by `validate.yaml`), one
  helper per rule family: replicas 1 to 100 for `api`, `worker` and `web`; `ingress.mode` is
  `ingress-nginx` or `gateway-api`; `ingress.tls.secretName` not empty; `ingress.websocket`
  read and send timeouts at least `labs.sessionTtlSeconds`; Api `terminationGracePeriodSeconds`
  at least `preStopDelaySeconds` + `drainPeriodSeconds` + 10 (same rule as the domain
  `min_termination_grace`) and Worker at least 10; `migration.backoffLimit` 0 to 6 and
  `migration.activeDeadlineSeconds` 30 to 3600; `externalSecrets.storeName` and one
  `externalSecrets.remoteKeys` entry per catalog secret unless `devSecrets.enabled`; `llm.region`
  an EU region of ADR 0007 unless `environment` is `development`; `observability.scrapeMode` is
  `serviceMonitor` or `annotations`; probe bounds (period 1 to 60, timeout 1 to 30 and at most the
  period, failure threshold 1 to 30) for `api`, `worker` and `web`; every workload image has a
  repository and a sha256 digest (checked in `edu-it.image`).
- `templates/_validation-networkpolicy.tpl`: `networkPolicy.kubernetesApi.cidrs` is non-empty,
  called by every NetworkPolicy template.
- `templates/validate-priorityclass.yaml`: `priorityClass.value` is greater than
  `labs.podPriorityValue`.
- `templates/dev-datastores.yaml`: inline guard that fails when `devDatastores.enabled` is true
  while `environment` is `production`.

`kindTest.sessionTtlSeconds` is not a chart rule: it lives in the overlay `ci/values-kind-test.yaml`,
and `tools/check_values_consistency.py` rejects it in the four Values_Files. Images are referenced as `repository` plus
`digest` and composed as `repository@sha256:<64 hex>` (D8). Secrets are never in values: they are
mounted as optional volume files read by a `SecretSource` (D6).

**Secret catalog.** `edu-it.secretCatalog` in `_helpers.tpl` is the single list the optional secret
volumes and the ExternalSecrets are both generated from, so a secret no workload reads has no
ExternalSecret. Each entry gives the `externalSecrets.remoteKeys` field and the keys read as
files under `/var/run/secrets/edu-it/<name>/`:

| Secret | Read by | Keys | `remoteKeys` field |
|--------|---------|------|--------------------|
| `database` | Api, Worker, Migration_Job | `url` | `database` |
| `redis` | Api, Worker | `url` | `redis` |
| `session` | Api | `key` | `session` |
| `llm` | Worker | `api-key` | `llm` |
| `object-storage` | Api, Worker | `access-key`, `secret-key` | `objectStorage` |

Plain `Secret` objects render only when `devSecrets.enabled` (`secrets-dev.yaml`).

**Hooks and bootstrap resources.** The migration Job is a `pre-install,pre-upgrade` hook with
`backoffLimit` (0 to 6, default 0) and `activeDeadlineSeconds` (30 to 3600, default 600) from
values; a failed hook stops Helm before it touches the Deployments. Resources the hook needs
are rendered as `pre-install,pre-upgrade` hooks with weight `-10` and
`hook-delete-policy: before-hook-creation`; the Job has weight `0`. Shipped as:
`serviceaccounts.yaml` (the `migration` ServiceAccount only), `configmap-migration.yaml`,
`externalsecret-database.yaml` and, when `devSecrets.enabled`, the `database` Secret in
`secrets-dev.yaml`. The database ExternalSecret uses `creationPolicy: Orphan`, so removing the hook
object never removes the synced Secret (risk R7); the other ExternalSecrets
(`externalsecrets.yaml`) are normal release resources with `creationPolicy: Owner`. The Job is kept
after failure so its log stays available. The `cni-check` Job is a `post-install` hook with weight
`0`, `backoffLimit: 0` and `activeDeadlineSeconds: 120`.

**Known gap (first install on kind).** Helm runs pre-install hooks before it creates the
release's normal resources. The namespaces (`namespaces.manage=create`) and the bundled dev
PostgreSQL and Redis (`dev-datastores.yaml`) are normal resources, not hooks, so on a first
`helm install` the migration Job would have neither its namespace nor a database. Task 12.3 made
only the resources listed above hooks. The kind smoke test (`tests/k8s/test_smoke_kind.py`,
task 6.14) therefore works around it without changing the chart: it pre-creates the `platform` and `labs`
namespaces with the restricted Pod Security labels and installs with
`namespaces.manage=operator-managed`, runs `helm install --no-hooks` so the dev PostgreSQL and
Redis exist and become ready, then runs `helm upgrade --wait --timeout 600s`, whose pre-upgrade
migration hook runs against a ready database. Production installs are unaffected: they use
managed datastores and operator-managed namespaces.

**Extension kinds.** Allowed upstream kinds: `Namespace`, `PriorityClass`, `StatefulSet` and
`PersistentVolumeClaim` (dev bundled datastores only; the shipped dev datastores use `emptyDir`),
`ServiceAccount`, `Role`, `RoleBinding`, `Deployment`, `Service`, `HorizontalPodAutoscaler`,
`PodDisruptionBudget`, `Job`, `NetworkPolicy`, `ResourceQuota`, `LimitRange`, `RuntimeClass`,
`Ingress`, `Secret` (dev only) and `ConfigMap`.
Named extension kinds: `ExternalSecret` and `SecretStore` (External Secrets), `Gateway` reference
and `HTTPRoute` (Gateway API), `ServiceMonitor` and `PrometheusRule` (only when enabled), and a
Kyverno `ClusterPolicy` with `verifyImages` (only when `imagePolicy.enabled`, default false;
Kyverno is an operator-installed optional prerequisite). A kind allowlist test fails on any other
kind, and the chart renders zero cloud-provider-specific custom resource kinds.

**OQ-3: drain protection.** Req 4.3 forbids cluster-scoped grants, so the Worker cannot read
nodes. Drain protection renders a PodDisruptionBudget (`maxUnavailable: 0`) on lab pods. The drain
wait limit (`labs.drainProtection.waitLimitSeconds`) is the value the Deployment_Guide uses as
`kubectl drain --timeout`. When the operator force-removes the pod after that limit, the Worker
sees the pod disappear and applies the infrastructure-loss path; the Worker does not time the
drain itself.

**OQ-4: monitoring ingress.** A value-driven ingress rule allows the monitoring namespace
(`networkPolicy.monitoringNamespaceSelector`) to reach only the Api and Worker metrics ports. It is
off by default (`networkPolicy.monitoringIngress.enabled: false`) and the operator turns it on when
the monitoring namespace scrapes the pods. Req 11.4 is amended accordingly.

**Api exec admission policy.** The `ValidatingAdmissionPolicy` that restricts the Api identity to
the `learner` container (ADR 0009) is cluster-scoped and is not rendered by this chart. The spike
(`tests/k8s/spike_pod_layout_kind.py`) defines and proves it, and the Deployment_Guide lists it as an
operator-applied prerequisite per Target.

**OQ-8: web workload.** The chart also renders a `web` Deployment and Service in `platform`, with
the same probes, HPA, priority class and security context as the Api. It needs no Kubernetes API access
and no secrets, so it uses the namespace default ServiceAccount with `automountServiceAccountToken:
false`. The shipped chart has a PDB for the Api and the Worker only. The
`web` egress to the Api is the only extra rule beyond Req 11.

**Other conventions.** The Api, Worker and Migration_Job have one ServiceAccount each with
`automountServiceAccountToken: false`. The Migration_Job, cni-check Job, smoke test and web pod
specs keep it `false`; only the Api and Worker pod specs set it to `true`, because they call the
Kubernetes API. They project their own token (the
`edu-it.kubeApiAccess.*` helpers in `_kube-api-access.tpl`): a `serviceAccountToken` source with
`expirationSeconds: 600` (the smallest value the API server accepts) and the default audience, the
`kube-root-ca.crt` CA bundle and the namespace, mounted read-only at
`/var/run/secrets/kubernetes.io/serviceaccount`, where the Kubernetes client's in-cluster loader
reads them. The `kindTest.sessionTtlSeconds` override is allowed only in the kind test values file. Bundled dev
PostgreSQL and Redis render only when `devDatastores.enabled` is true, and rendering fails if that
is true while `environment` is `production`.

## Consequences

Keeping all differences in values and enforcing them with a schema and `fail` helpers makes
portability checkable by rendering the four Values_Files, and an error always names the key. The
price is a large values surface and a schema that must change together with the templates.

Hook-scoped bootstrap resources let the migration run before the Deployments exist, and
`creationPolicy: Orphan` protects the synced Secret from hook cleanup. The cost is that hook
resources are not tracked as normal release resources, so their drift is not reported by Helm.

The kind allowlist limits blast radius and keeps the chart installable without cloud CRDs, but
External Secrets, Gateway API, Prometheus Operator and Kyverno CRDs become optional prerequisites
that the Deployment_Guide must list per Target. The Image_Policy_Engine kind is cluster-scoped,
which is the one exception to namespaced rendering and is off by default.

Drain protection via a PDB avoids a cluster-scoped grant, but a forced removal after the wait
limit ends the session through the infrastructure-loss path rather than a graceful hand-off.

Because the namespaces and the dev datastores are not hooks, a first install on a Target that
creates its own namespaces needs the two-phase procedure of the kind smoke test, or operator-managed
namespaces and managed datastores (the production path). A later change can turn them into hooks.

This decision flows back into #[[file:.kiro/steering/deployment.md]] per
#[[file:.kiro/steering/working-agreements.md]]. The ADR was finalized (status `Accepted`) in task
12.15 together with that steering update.
