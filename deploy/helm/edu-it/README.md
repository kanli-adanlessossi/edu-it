# EDU-IT Helm chart

Chart for the EDU-IT platform (`web`, `api`, `worker`, migration Job, lab namespace controls).
Values files: `values.yaml` (defaults), `values-dev.yaml`, `values-eks.yaml`, `values-aks.yaml`,
`values-onprem.yaml`. Every key is declared in `values.schema.json`.

## Namespaces

| Key | Default | Purpose |
|-----|---------|---------|
| `namespaces.manage` | `create` | `create` or `operator-managed` |
| `namespaces.platform` | `platform` | Platform_Namespace: web, api, worker, migration and cni-check Jobs |
| `namespaces.labs` | `labs` | Labs_Namespace: learner lab pods |

- `create`: the Chart renders both Namespace objects with the Pod Security Admission labels below.
- `operator-managed`: the Chart renders no Namespace object. The operator must create both
  namespaces before `helm install`, with the labels below. `values-onprem.yaml` uses this mode.

### Required Pod Security Admission labels (both namespaces)

```yaml
pod-security.kubernetes.io/enforce: restricted
pod-security.kubernetes.io/enforce-version: latest
pod-security.kubernetes.io/audit: restricted
pod-security.kubernetes.io/audit-version: latest
pod-security.kubernetes.io/warn: restricted
pod-security.kubernetes.io/warn-version: latest
```

Create them in operator-managed mode (adjust names if you changed the keys above):

```sh
for ns in platform labs; do
  kubectl create namespace "$ns"
  kubectl label namespace "$ns" \
    pod-security.kubernetes.io/enforce=restricted \
    pod-security.kubernetes.io/enforce-version=latest \
    pod-security.kubernetes.io/audit=restricted \
    pod-security.kubernetes.io/audit-version=latest \
    pod-security.kubernetes.io/warn=restricted \
    pod-security.kubernetes.io/warn-version=latest
done
```

Check: `kubectl get ns platform labs --show-labels`.

## NetworkPolicy enforcement

The cluster CNI must enforce NetworkPolicy. Without enforcement every rendered policy has no
effect. A post-install hook Job (`cni-check`, `activeDeadlineSeconds: 120`) creates two probe pods
and checks that an allowed probe is reachable and a denied probe is blocked. A check that does not
finish in 120 seconds counts as enforcement absent.

- `networkPolicy.enforcementRequired: true` (EKS, AKS, on-premise): the install fails with
  "NetworkPolicy enforcement is absent".
- `networkPolicy.enforcementRequired: false` (dev): the install completes and `NOTES.txt` prints a
  warning that NetworkPolicy enforcement is absent. The Job log holds the detail:
  `kubectl -n platform logs job/<release>-cni-check`.

## Image digests

Every workload image needs a `sha256` digest. Rendering fails and names the workload otherwise.
