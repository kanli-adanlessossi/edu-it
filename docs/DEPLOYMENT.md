# EDU-IT Deployment Guide

This guide installs, upgrades, rolls back and sizes EDU-IT on EKS, AKS and on-premise Kubernetes.
Every key below is a real key of the chart in [`deploy/helm/edu-it`](../deploy/helm/edu-it/README.md)
(`values.yaml`, `values-eks.yaml`, `values-aks.yaml`, `values-onprem.yaml`, `values-dev.yaml`).
The design behind it is in [DEPLOYMENT-DESIGN.md](DEPLOYMENT-DESIGN.md) and
[ARCHITECTURE.md](ARCHITECTURE.md).

Conventions: `$REL` is the Helm release name, `$VALUES` is the Values_File for your Target
(`values-eks.yaml`, `values-aks.yaml` or `values-onprem.yaml`). Workload names are derived from the
release name (`$REL-api`, `$REL-worker`, `$REL-web`, `$REL-migration`, `$REL-cni-check`). The
namespaces default to `platform` and `labs` (`namespaces.platform`, `namespaces.labs`).

## Contents

1. [Prerequisites per Target](#prerequisites-per-target)
2. [Install](#install)
3. [Upgrade](#upgrade)
4. [Rollback](#rollback)
5. [Autoscaling](#autoscaling)
6. [Session_Cost and sizing](#session_cost-and-sizing)
7. [Network policy enforcement](#network-policy-enforcement)
8. [Finding networkPolicy.kubernetesApi.cidrs](#finding-networkpolicykubernetesapicidrs)
9. [Failed upgrade: rollback and active-session check](#failed-upgrade-rollback-and-active-session-check)
10. [Diagnosing an ExternalSecret sync failure](#diagnosing-an-externalsecret-sync-failure)
11. [Go_Live_Checklist](#go_live_checklist)
12. [Out of scope](#out-of-scope)

## Prerequisites per Target

Each prerequisite lists a minimum version or a command that confirms it is present. The chart does
not install any of them.

| Prerequisite | EKS | AKS | On-premise | Verification command |
|---|---|---|---|---|
| Kubernetes | 1.28 or later | 1.28 or later | 1.28 or later | `kubectl version` (server version) |
| Helm | 3.12 or later | 3.12 or later | 3.12 or later | `helm version --short` |
| CNI with NetworkPolicy enforcement | Amazon VPC CNI with network policy enabled, or Calico or Cilium | Azure CNI with Azure Network Policy, Calico or Cilium | Calico or Cilium | `kubectl -n kube-system get pods` shows the CNI, then the `cni-check` Job at install (see [Network policy enforcement](#network-policy-enforcement)) |
| Lab_RuntimeClass | gVisor (`runsc`) on the lab nodes | Kata (`kata`) on the lab nodes | gVisor (`runsc`) | `kubectl get runtimeclass` lists `labs.runtimeClass.name`; the Target defaults are `labs.runtimeClass.kind`/`handler` in each Values_File |
| Ingress or Gateway API controller | ingress-nginx (`ingress.mode: ingress-nginx`) or a Gateway API controller (`gateway-api`) | same | same | `kubectl get ingressclass` shows `ingress.className` (default `nginx`); for Gateway API, `kubectl get gatewayclass` |
| External Secrets Operator | v0.9 or later (API `external-secrets.io/v1beta1`) | same | same | `kubectl get crd externalsecrets.external-secrets.io` and `kubectl get clustersecretstore $STORE` is `Ready` (`externalSecrets.storeName`, `externalSecrets.storeKind`) |
| Lab_Node_Pool | Node group labelled `edu-it.io/pool=lab`, tainted `edu-it.io/lab=true:NoSchedule` | same, node pool | same, labelled nodes | `kubectl get nodes -l edu-it.io/pool=lab` returns at least one node; `kubectl describe node <n>` shows the taint |
| Kubelet `podPidsLimit` on the Lab_Node_Pool | Set to 128 to match the Docker PID limit | same | same | `kubectl get --raw /api/v1/nodes/<n>/proxy/configz` shows `"podPidsLimit":128` |
| Image_Policy_Engine (Kyverno), optional | required only when `imagePolicy.enabled: true` | same | same | `kubectl get crd clusterpolicies.kyverno.io`; Kyverno 1.10 or later |
| Prometheus Operator CRDs, optional | needed for `prometheusRules.enabled` and `observability.scrapeMode: serviceMonitor` | same | same | `kubectl get crd prometheusrules.monitoring.coreos.com servicemonitors.monitoring.coreos.com` |

Target-specific values already set in the Values_Files:

| Key | EKS | AKS | On-premise |
|---|---|---|---|
| `workloadIdentity.type` | `irsa` (annotation `eks.amazonaws.com/role-arn`) | `azure` (annotation `azure.workload.identity/client-id`) | `none` |
| `objectStorage.type` | `s3` | `azureblob` | `s3` |
| `labs.runtimeClass.kind` / `handler` | `gvisor` / `runsc` | `kata` / `kata` | `gvisor` / `runsc` |
| `namespaces.manage` | `create` | `create` | `operator-managed` |
| `observability.scrapeMode` | `serviceMonitor` | `serviceMonitor` | `annotations` |

Replace every placeholder before use: the all-zero `images.*.digest` values, `ingress.host`,
`workloadIdentity.annotations`, `objectStorage.bucket` and `networkPolicy.kubernetesApi.cidrs`.
The kubelet `podPidsLimit` cannot be enforced by the chart; it is a node pool setting
(see [ADR 0009](adr/0009-kubernetes-sandbox-provider.md)).

## Install

The steps are the same on all three Targets; the Values_File differs.

1. Pin the image digests. Set `images.web.digest`, `images.api.digest`, `images.worker.digest` and
   `images.migrations.digest` to real `sha256:` digests in `$VALUES`. Rendering fails and names the
   workload when a digest is missing.
   Success check: `helm template $REL deploy/helm/edu-it -f deploy/helm/edu-it/$VALUES >/dev/null`
   exits 0.
2. Set `networkPolicy.kubernetesApi.cidrs` ([how to find it](#finding-networkpolicykubernetesapicidrs)).
3. Create the namespaces (on-premise, `namespaces.manage: operator-managed` only) with the Pod
   Security Admission labels from the [chart README](../deploy/helm/edu-it/README.md).
   Success check: `kubectl get ns platform labs --show-labels` shows `pod-security.kubernetes.io/enforce=restricted`
   on both.
4. Create the remote secrets in the secret store under the keys of `externalSecrets.remoteKeys`
   (`database`, `redis`, `session`, `llm`, `objectStorage`).
   Success check: `kubectl get clustersecretstore $STORE` reports `Ready`.
5. Install:
   `helm install $REL deploy/helm/edu-it -n platform -f deploy/helm/edu-it/$VALUES --wait --timeout 300s`
   Success check: the command exits 0 and `helm status $REL -n platform` shows `STATUS: deployed`.
6. Check the migration and the CNI check:
   `kubectl -n platform get jobs` shows `$REL-migration` and `$REL-cni-check` as `Complete`.
7. Check the workloads: `kubectl -n platform get deploy` shows `READY` equal to the desired count
   for the Api, the Worker and the Web, and `kubectl -n platform get externalsecret` shows
   `READY=True` for every entry.
8. Check the ingress: `curl -fsS https://<ingress.host>/api/healthz` returns HTTP 200.

## Upgrade

1. Record the active Lab_Session count before the upgrade (needed by
   [the failed-upgrade check](#failed-upgrade-rollback-and-active-session-check)):
   `kubectl -n labs get pods --no-headers | wc -l` and note the number.
2. Record the current revision: `helm history $REL -n platform` (note the last `REVISION`).
3. Review the change: `helm diff upgrade` if the plugin is installed, otherwise
   `helm template $REL deploy/helm/edu-it -f deploy/helm/edu-it/$VALUES`.
4. Upgrade:
   `helm upgrade $REL deploy/helm/edu-it -n platform -f deploy/helm/edu-it/$VALUES --wait --timeout 300s`
   The Migration_Job runs first (`migration.backoffLimit` 0 to 6, default 0;
   `migration.activeDeadlineSeconds` 30 to 3600, default 600). If it fails, no new Api or Worker pod
   starts and the command exits non-zero.
5. Success checks: exit status 0; `helm history $REL -n platform` shows a new revision `deployed`;
   `kubectl -n platform rollout status deploy/$REL-api` and `deploy/$REL-worker` succeed; the active
   Lab_Session count equals the count recorded in step 1.

## Rollback

Rollback restores the previously installed release.

1. List revisions: `helm history $REL -n platform`.
2. Roll back: `helm rollback $REL <previous-revision> -n platform --wait --timeout 300s`.
3. Success checks: `helm history $REL -n platform` shows the previous revision's content as the
   newest `deployed` revision; `helm get values $REL -n platform` matches the previous values;
   `kubectl -n platform rollout status deploy/$REL-api` and `deploy/$REL-worker` succeed; the active
   Lab_Session count equals the count recorded before the upgrade.

Migrations stay backward compatible with the previous release for one release, so rolling back one
release needs no schema change.

## Autoscaling

| Workload | Minimum | Maximum | Metric | Target value | Values key |
|---|---|---|---|---|---|
| Api | 2 replicas | 6 replicas | CPU utilization | 70 % | `api.hpa.minReplicas`, `api.hpa.maxReplicas`, `api.hpa.targetCpuUtilizationPercentage` |
| Worker | 2 replicas | 8 replicas | CPU utilization (`worker.hpa.metric: cpu`) | 70 % | `worker.hpa.*` |
| Worker (queue depth variant) | 2 replicas | 8 replicas | queue depth (`worker.hpa.metric` set for queue depth, needs `metricsAdapter.enabled: true`) | 10 jobs per replica (`worker.hpa.queueDepthTarget`) | `worker.hpa.*`, `metricsAdapter.enabled` |
| Web | 2 replicas | 4 replicas | CPU utilization | 70 % | `web.hpa.*` |
| Lab_Node_Pool | set by you: at least 1 node | derived from [the sizing example](#worked-sizing-example) | pending Lab_Pods that no node can fit (cluster autoscaler or Karpenter on the pool labelled `edu-it.io/pool=lab`) | scale out when a Lab_Pod stays Pending | node group or pool settings of the Target, outside the chart |

PodDisruptionBudgets keep `minAvailable: 1` for the Api, Worker and Web (`api.pdb`, `worker.pdb`,
`web.pdb`). The Lab_Node_Pool is not managed by the chart.

## Session_Cost and sizing

Session_Cost is the CPU, memory, ephemeral storage and PID limits one Lab_Session consumes.

**Measured Session_Cost: not measured.** The Load_Test (`tools/perf/lab_load_k8s.py`, task 14.8)
writes per-session CPU, memory and storage to a JSON file (`--out`, default `load-test.json`).
No result file from that Load_Test exists in this repository, so no measured value is stated. Run

```sh
python tools/perf/lab_load_k8s.py --context <kube-context> --runtime-class <name> --out load-test.json
```

on your Target, read `per_session` in the JSON, and replace the sizing inputs below with those
numbers.

**Configured Session_Cost (upper bound from the limits).** Each Lab_Pod has three containers whose
requests equal limits ([ADR 0009](adr/0009-kubernetes-sandbox-provider.md),
`packages/domain/edu_it_domain/config.py`):

| Container | CPU | Memory | Writable storage | PIDs |
|---|---|---|---|---|
| `learner` | 1000 m (`SANDBOX_CPU_LIMIT`) | 512 MiB (`SANDBOX_MEMORY_LIMIT`) | 256 MiB memory-backed volumes (`SANDBOX_WRITABLE_QUOTA`) | 128 (`SANDBOX_PID_LIMIT`, pod level, via `podPidsLimit`) |
| `grader` | 500 m (`K8S_GRADER_CPU_MILLI`) | 256 MiB (`K8S_GRADER_MEMORY_BYTES`) | none | shared |
| `owner` | 50 m (`K8S_HELPER_CPU_MILLI`) | 16 MiB (`K8S_HELPER_MEMORY_BYTES`) | none | shared |
| Total per Lab_Session | 1550 m | 784 MiB | 256 MiB | 128 |

The writable volumes are tmpfs, so their 256 MiB counts inside the container memory limit and is
not extra node memory.

### Worked sizing example

Goal: 100 concurrent Lab_Sessions on nodes with 8 vCPU and 32 GiB, of which 7.5 vCPU and 28 GiB are
allocatable after system reserve (example numbers, replace them with your node type).

1. CPU per node: 7500 m / 1550 m = 4.84, rounded down to 4 sessions.
2. Memory per node: 28 GiB = 28 x 1024 = 28 672 MiB; 28 672 / 784 = 36.57, rounded down to 36 sessions.
3. Sessions per node is the smaller value: min(4, 36) = 4 (CPU is the limit).
4. Nodes for 100 sessions: 100 / 4 = 25, so 25 nodes.
5. Add one spare node for a node loss: 25 + 1 = 26 nodes.
6. Total requests: CPU 100 x 1.55 = 155 vCPU; memory 100 x 784 MiB = 78 400 MiB, about 76.6 GiB.
7. Check the Labs_Namespace ResourceQuota. The defaults (`labs.quota.pods: 50`,
   `requestsCpu: '25'`, `requestsMemory: 50Gi`) admit min(50, 25 000 / 1550 = 16.1, 51 200 / 784 = 65.3)
   = 16 sessions. For 100 sessions raise `labs.quota.pods` to at least 100, `requestsCpu` to at least
   `'155'`, `limitsCpu` to at least `'155'`, `requestsMemory` to at least `77Gi` and `limitsMemory`
   to at least `77Gi`.

## Network policy enforcement

The Target's CNI must enforce NetworkPolicy. A CNI that does not enforce NetworkPolicy leaves all
rendered policies without effect. After install, the `$REL-cni-check` Job reports whether
enforcement is present and completes within 120 seconds.

- `networkPolicy.enforcementRequired: true` (EKS, AKS, on-premise): the install fails with an error
  that says NetworkPolicy enforcement is absent.
- `networkPolicy.enforcementRequired: false` (dev): the install completes and prints a warning.
- A check that does not finish in 120 seconds counts as enforcement absent.

Read the result: `kubectl -n platform logs job/$REL-cni-check`.

## Finding networkPolicy.kubernetesApi.cidrs

The Api and Worker reach the Kubernetes API through an `ipBlock`, because pod selectors cannot match
the API server. The key `networkPolicy.kubernetesApi.cidrs` is required and non-empty; if it is empty
or missing, rendering fails with an error that names the key and produces no manifests.

1. Read the API server addresses: `kubectl get endpoints kubernetes -n default -o jsonpath='{.subsets[*].addresses[*].ip}'`.
2. Write each address as a `/32` entry in `$VALUES`:

```yaml
networkPolicy:
  kubernetesApi:
    cidrs:
    - 172.20.0.1/32
```

3. On EKS use the control plane endpoint addresses (the placeholder in `values-eks.yaml` is not
   valid for your cluster). On AKS and on-premise use the same command. If the API server is behind
   a load balancer, use the load balancer address.
4. Success check: `helm template $REL deploy/helm/edu-it -f deploy/helm/edu-it/$VALUES | grep -A3 ipBlock`
   shows your addresses, and `kubectl -n labs exec` from the Worker still works after install.

## Failed upgrade: rollback and active-session check

If `helm upgrade` exits non-zero (for example the Migration_Job failed or timed out), the previous
Deployments keep running with their pod count unchanged and no new Api or Worker pod starts.

1. Read the migration failure: `kubectl -n platform logs job/$REL-migration`. The log holds the
   failing migration identifier and the database error and is kept for at least 24 hours or until
   the Job is deleted.
2. Roll back if the release is in a failed or partially applied state:
   `helm rollback $REL <previous-revision> -n platform --wait --timeout 300s`.
3. Check the revision returned to the previous one: `helm history $REL -n platform` shows the
   newest `deployed` revision with the previous chart and values, and `helm status $REL -n platform`
   shows `STATUS: deployed`.
4. Check that Lab_Sessions are still active: the count from `kubectl -n labs get pods --no-headers | wc -l`
   must equal the count recorded before the upgrade. A lower count means sessions were lost;
   open an incident and do not retry until the cause is known.
5. Fix the migration, then repeat the [upgrade](#upgrade).

## Diagnosing an ExternalSecret sync failure

When an `ExternalSecret` cannot sync, the Kubernetes Secret is absent and the dependent Api or
Worker pod stays not Ready. `helm install` fails when the Helm timeout (default 300 seconds)
elapses. On `helm upgrade` the previous Api and Worker pods stay Ready and serving. A refresh failure
after a successful start does not restart the pods; they keep the last synced value.

1. List the status: `kubectl -n platform get externalsecret`. Any entry with `READY=False` has a
   sync failure.
2. Read the condition: `kubectl -n platform describe externalsecret <name>` and read `Status.Conditions`
   (type `Ready`, status `False`, with `Reason` and `Message`).
3. Read the pod startup log for the missing key name: `kubectl -n platform logs deploy/$REL-api`
   and `deploy/$REL-worker`. The process exits non-zero within 30 seconds of start and logs one
   entry per missing secret key, naming the key and never the value.
4. Apply the corrective action for the cause:
   - Reason `SecretSyncedError` with a store error: the store or its credentials are wrong. Fix
     `externalSecrets.storeName` and `externalSecrets.storeKind`, and the store authentication,
     then check `kubectl get clustersecretstore $STORE` is `Ready`.
   - Message that a remote key or property is not found: the secret is missing in the store. Create
     it under the key set in `externalSecrets.remoteKeys` with the property named in the log.
   - Message about access denied: grant the workload identity (`workloadIdentity.*`) read access to
     that key.
5. Force a resync: `kubectl -n platform annotate externalsecret <name> force-sync=$(date +%s) --overwrite`,
   then confirm `READY=True` and that the pods become Ready.

With `prometheusRules.enabled: true` the alert `EduItExternalSecretNotReady` fires when the Ready
condition stays `False` for 5 minutes.

## Go_Live_Checklist

Run every check on the real Target. If an item fails its pass condition, go-live is blocked until
that item passes; the corrective section is named next to each item.

- [ ] **NetworkPolicy enforcement.** Pass: `kubectl -n platform logs job/$REL-cni-check` reports enforcement present and `networkPolicy.enforcementRequired` is `true`. Fix: [Network policy enforcement](#network-policy-enforcement).
- [ ] **External Secrets.** Pass: `kubectl -n platform get externalsecret` shows `READY=True` for every entry and `devSecrets.enabled` is `false`. Fix: [Diagnosing an ExternalSecret sync failure](#diagnosing-an-externalsecret-sync-failure).
- [ ] **Image signature verification.** Pass: `imagePolicy.enabled` is `true`, `kubectl get clusterpolicy` lists the policy, and creating a pod from an unsigned image in `platform` is rejected. Fix: [Prerequisites per Target](#prerequisites-per-target) (Image_Policy_Engine) and the `imagePolicy` keys.
- [ ] **TLS.** Pass: `curl -fsSI https://<ingress.host>/` succeeds with a valid certificate from `ingress.tls.secretName` and `kubectl -n platform get secret <ingress.tls.secretName>` exists. Fix: [Install](#install) step 8.
- [ ] **WebSocket timeouts.** Pass: `ingress.websocket.readTimeoutSeconds` and `ingress.websocket.sendTimeoutSeconds` are each at least `labs.sessionTtlSeconds` (default 3600), and a terminal stays open past 60 seconds idle. Fix: set the two keys in `$VALUES`, then follow [Upgrade](#upgrade).
- [ ] **RBAC review.** Pass: `kubectl auth can-i create pods -n labs --as=system:serviceaccount:platform:$REL-worker` is `yes`, and the same check in any other namespace is `no`. Fix: review the chart RBAC (`templates/rbac.yaml`, `templates/rbac-platform.yaml`) and [upgrade](#upgrade).
- [ ] **PSA labels.** Pass: `kubectl get ns platform labs --show-labels` shows enforce, audit and warn set to `restricted` on both. Fix: [Install](#install) step 3.
- [ ] **ResourceQuota values.** Pass: `kubectl -n labs get resourcequota -o yaml` admits the planned concurrent sessions (see [the sizing example](#worked-sizing-example), step 7). Fix: set `labs.quota.*` and [upgrade](#upgrade).
- [ ] **RuntimeClass verified on a real Target.** Pass: on at least one real EKS, AKS or on-premise cluster (not kind), `kubectl get runtimeclass` lists `labs.runtimeClass.name` and a Lab_Pod runs with `kubectl -n labs get pod <p> -o jsonpath='{.spec.runtimeClassName}'` returning that name. Fix: [Prerequisites per Target](#prerequisites-per-target) (Lab_RuntimeClass).
- [ ] **Runc warning removed.** Pass: `kubectl -n platform logs deploy/$REL-worker` has no `Runc_Warning` line and `environment` is `production`; a Worker refuses to start on plain runc in production. Fix: set `labs.runtimeClass.kind` to `gvisor` or `kata`, see [Prerequisites per Target](#prerequisites-per-target) (Lab_RuntimeClass).
- [ ] **Kubelet podPidsLimit.** Pass: the Lab_Node_Pool kubelet config shows `podPidsLimit` of 128 (see the verification command in [Prerequisites per Target](#prerequisites-per-target)). Fix: set it in the node pool kubelet configuration, see [Prerequisites per Target](#prerequisites-per-target).

## Out of scope

Cluster provisioning, warm pool, multi-region and backups are outside this release. The guide
assumes an existing cluster, and you operate the database and object storage backups yourself.
