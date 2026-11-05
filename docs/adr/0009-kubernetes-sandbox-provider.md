# ADR 0009: Kubernetes Sandbox Provider

- **ID:** A9
- **Status:** Accepted
- **Date:** 2025-01-01
- **Decision topic:** Kubernetes implementation of `SandboxProvider` (exec through the API server, three-container identity model, deterministic pod name)
- **Finalized in:** task 5.10 of the `kubernetes-deployment` spec (status `Accepted`; provider code from tasks 5.1 to 5.9)

## Context

The sandbox sits behind the `SandboxProvider` port: Docker (dev only), Kubernetes (prod), fake
(tests). Production labs run on Kubernetes (EKS, AKS or on-premise) using standard Kubernetes
APIs only, with one pod per session in the `labs` namespace under the restricted Pod Security
Admission profile. The Kubernetes provider must keep the grading isolation and session limits of
the Docker provider without changing the port, `GradeRun` or the domain.

Four tensions drive the decision:

- Terminals, exec and copy need a channel into a learner-controlled pod, and the pod must not
  expose an extra listening port or run an agent.
- Restricted PSA forbids root (Req 2.1, 2.2), while grading needs three identities: the learner,
  the grader and the owner of the grading bundle (Req 5.1).
- Several requirements conflict or leave a gap, tracked as open questions OQ-1, OQ-2, OQ-5 and
  OQ-6 in the `kubernetes-deployment` design.
- Provisioning must be idempotent when a Worker restarts mid-job (Req 20.5).

The governing rules live in #[[file:.kiro/steering/labs.md]] and
#[[file:.kiro/steering/deployment.md]]; the port signatures live in `docs/LABS-DESIGN.md` (D4).

## Decision

**Exec through the API server (D2, OQ-1).** All pod interaction (exec, copy, terminal) uses the
Kubernetes `pods/exec` subresource through the API server. No agent runs inside the lab image and
no port is opened in the lab pod. The RBAC grant, plus one admission policy for the Api container
scope, is the access control:

- Worker: one `Role` in `labs` with `create`, `get`, `list`, `watch`, `delete` on `pods` and
  `create`, `get` on `pods/exec`.
- Api: one `Role` in `labs` with `get` on `pods` and `create` on `pods/exec`; no pod create,
  update, patch, delete or deletecollection. RBAC cannot scope `pods/exec` by container, so a
  `ValidatingAdmissionPolicy` on `CONNECT pods/exec` restricts the Api identity to the `learner`
  container (proven by the spike, see "Spike result").
- No `ClusterRole` or `ClusterRoleBinding` for any platform ServiceAccount.

The "terminal port" in Req 11.5 is read as the Kubernetes API server endpoint, so Api egress goes
to the API server through an `ipBlock` rule. The Labs_Allowlist ingress rules of Req 3.2 are still
rendered as defense in depth only.

**Three-container identity model (D3, OQ-2).** The baseline is layout B: the Lab_Pod has three
containers with distinct non-root UIDs and no shared process namespace.

| Container | UID | Role |
|-----------|-----|------|
| `learner` | 10000 | Learner terminal, workspace, freeze target |
| `grader` | 10001 | Runs Checks; reads `/workspace` read-only, owns `/grader` group access |
| `owner` | 10002 | Owns the grading bundle (the "root" identity of `GradeRun`, mapped to a non-root UID) |

`as_user` maps to a container: `learner` to `learner`, `grader` to `grader`, `root` to `owner`.
`shareProcessNamespace` is `false`, so learner processes cannot see `/proc` entries of helper
processes. `GradeRun` is reused unchanged; the provider only supplies `exec`. The freeze of learner
processes runs inside the `learner` container through `exec` as the learner UID (SIGSTOP, no
capability needed). Req 5.1 and 5.4 are read as "owned by an identity the learner cannot write as"
and "freeze through the existing grading use case". Every container drops all capabilities, sets
`runAsNonRoot`, `allowPrivilegeEscalation: false` and the `RuntimeDefault` seccomp profile.

Layout A (three UIDs plus a shared process namespace) is the fallback, used only if the day-one
spike (risk R1, task 1.2) shows B fails the isolation checks and A passes all of them. Then layout
D is considered; layout C is recorded only. In every case the grading semantics stay unchanged
(Req 16.10). The spike result (chosen layout, evidence, rejected layouts) is recorded in the
"Spike result" section below before any provider code is built (Req 16.12).

**Deterministic pod name (D1).** The pod is named `lab-<first 20 hex of sha256(session_stable_id)>`.
A re-run after a Worker restart gets `409 AlreadyExists` with the same session label, adopts the
pod and never creates a second one. A `409` with a different session label is a `ProvisionError`.

**Ready wait (OQ-5).** The provider waits `min(LAB_PROVISION_TIMEOUT, K8S_READY_CEILING)`, where
`K8S_READY_CEILING` is 60 s in `domain/config.py`. Req 6.2 (30 s default) and Req 1.5 (60 s hard
ceiling) both hold. On failure the provider deletes the pod and waits until it is absent before it
raises.

**PID limit (OQ-6).** The Kubernetes pod API has no per-pod PID field. The pod carries the
annotation `edu-it.io/pid-limit` with the Docker value (`SANDBOX_PID_LIMIT`), and the limit is
enforced by the `podPidsLimit` kubelet setting of the lab node pool. That setting is a
Go_Live_Checklist item and is proven by a fork-bomb case in the isolation suite on kind.

**Exec, copy and terminal (shipped, `K8sExecAdapter`).** Every call goes through the `pods/exec`
subresource behind the `K8sApiPort` adapter interface.

- `exec` wraps the command so it starts in `cwd` with `env` and carries a random per-command marker
  variable `EDU_IT_EXEC`. When `timeout_s` expires, a second exec in the same container sends
  SIGKILL to every process that carries the marker (the command, its process group and any process
  that left the group or was orphaned), and the result is exit code 137 with `timed_out`. This is
  the strategy of the Docker provider, needs no capability and never touches PID 1, so the pod
  stays usable (Req 5.6).
- `copy_in` and `copy_out` stream a tar archive over exec stdin or stdout. The host source is only
  read, and symlinks and special files are refused. A destination under `/grader` is extracted in
  the `owner` container (owner UID, group read, no group write; the `learner` container does not
  mount `/grader`); any other destination is extracted in the container of `as_user` (default
  `learner`).
- `attach_terminal` runs `/bin/bash -l` with a TTY in the `learner` container. A pod that is gone
  raises `SandboxGone` from `attach_terminal`, `read`, `write` and `resize`, and closing the
  channel kills the shell by its marker.
- An unknown `as_user` raises `SandboxError`. Raw API text is logged and never put in an exception
  message.

**Pod layout as built (`build_lab_pod`).** `learner` mounts `/workspace`, `/home/learner` and
`/tmp`; `grader` mounts `/workspace` read-only and `/grader`; `owner` mounts `/grader`; the pod
`fsGroup` is the grader GID (10001) and the `owner` runs with that GID as its primary group. Every
writable mount is a memory-backed `emptyDir` of exactly the Docker tmpfs size (`ENOSPC` at the size,
as in Docker). The `owner` container uses `K8S_HELPER_CPU_MILLI` (50 m) and
`K8S_HELPER_MEMORY_BYTES` (16 MiB), requests equal to limits. The builder sets the command of
every container to a bash init that never forks and blocks in the `read -t 1` builtin
on a pty master (see the amendment below), so the lab image needs `/bin/bash` and `/dev/ptmx`. The
`grader` container, where Checks run, uses `K8S_GRADER_CPU_MILLI` (500 m) and
`K8S_GRADER_MEMORY_BYTES` (256 MiB) instead of the helper size.

**Destroy and cleanup.** `destroy` deletes the pod and waits until it is absent for at most
`LAB_DESTROY_TIMEOUT` (30 s). A pod still present at the timeout is labelled
`edu-it.io/destroy=pending` for the Reaper and `destroy` raises a `SandboxError`. Destroying an
absent pod succeeds.

**Supporting decisions.** The pod builder, the rejection classifier and all decision rules are pure
functions (D4). Quota rejection is the distinct domain error `ProvisionCapacityError`, a subclass of
`ProvisionError` (D5); a Pending pod that is Unschedulable at the ready timeout is also a
`ProvisionCapacityError`. The runtime kind comes from the Chart value `labs.runtimeClass.kind`, and
plain runc is refused in production.

**Backend selection.** `SANDBOX_PROVIDER=kubernetes` builds the provider from the `SANDBOX_*` and
`K8S_LABS_NAMESPACE` settings (runtime kind and class, node selector, tolerations, node-loss
toleration seconds, grader GID, priority class). The production `K8sApiPort` comes from the
adapter module `edu_it_infrastructure.sandbox.k8s_client`, which is loaded lazily so that no
Kubernetes client is imported unless the backend is selected. Without it the start is refused and
the provider never falls back to the fake.

**Amendment: `/grader` ownership and freeze verification (checkpoint 7 findings).** The first
real-cluster run of `GradeRun` against layout B found two defects. The grading semantics
(fail-closed freeze, bundle isolation, argv forms) are unchanged; the pod layout details change.

1. *`/grader` was root-owned.* The kubelet creates an `emptyDir` root as `root:fsGroup`, mode 2777.
   `chmod 0775 /grader` as `root` maps to the `owner` container (UID 10002), which is neither the
   owner of that directory nor holds `CAP_FOWNER` (all capabilities are dropped), so the housekeeping
   failed with `EPERM`. The directory also stayed writable by the grader group at Check time, so it
   could not be closed. *Fix:* an init container (same image, owner UID, grader GID) creates the
   directory `bundle` (mode 2750, group inherited from the setgid volume root) in the `v-grader`
   volume, and the `grader` and `owner` containers mount it as `/grader` with `subPath: bundle`.
   The `owner` now owns the mounted `/grader`, so `GradeRun` opens it to the group, lets the grader
   create `run`, and closes it before any Check, unchanged. Rejected: a nested read-only mount
   (runc cannot create the `run` mountpoint inside a read-only parent: `read-only file system`), a
   root init container (forbidden by restricted PSA), `CAP_CHOWN`/`CAP_FOWNER` (violates "all
   capabilities dropped"). Verified on kind: owner `chmod 0775` succeeds, grader `mkdir -m 0700
   /grader/run` succeeds.
2. *The freeze was verified in the wrong process namespace.* Under layout B `ps` as the `grader`
   sees only the grader container, so `learner_all_stopped` was vacuously true and the freeze was
   unverified. *Fix:* for the `kubernetes` provider `GradeRun` runs the process listing in the
   `learner` container (`exec` as `learner`, `/bin/sh -c 'echo "SELF $$"; exec /bin/ps ...'`). The
   listing names its own PID, which is excluded as the single running process; every other row
   (PID 1 excluded) counts as a learner process, because the lab image has no names for the pod
   UIDs; a listing that does not show its own PID fails closed. Tests: a unit test with per-container
   process namespaces fails if the verification cannot see the learner processes, and a kind test
   shows that a real running learner process is seen in the learner container and is invisible to
   the grader container.

Further pod/node settings found by the shared suites on kind, and the equivalent-behaviour notes:

- *PID limit.* `podPidsLimit` limits the pod cgroup, so the learner container's own `pids.max` and
  `pids.events` are not the pod's. The Kubernetes branch of the fork-bomb case checks that the
  process count stops at the limit (less the pod's other processes); the node setting itself is
  checked on the labs-pool nodes through the kubelet `configz`. The Docker branch is unchanged.
- *OOM.* With the default kubelet behaviour a memory OOM kills every process of the container
  (cgroup `memory.oom.group`), which ends the pod under `restartPolicy: Never`. The lab node pool
  sets `singleProcessOOMKill: true` (the kind cluster does), so only the offender dies, as in Docker.
  The suite assertion is unchanged.
- *Init of every container.* The abuse cases (fork bomb, disk fill, memory loop, in a learner
  shell and inside a Check) exposed that the spike's inits do not survive a pod at its PID limit.
  The learner loop `sh -c "while :; do sleep 5 & wait $!; done"` forks, so once a bomb used the
  pod PID limit it died with "can't fork" (exit 2) and ended the container and, with
  `restartPolicy: Never`, the pod. The helpers' `tail -f /dev/null` does not reap, so a killed
  Check bomb stayed as zombies of PID 1 and held the pod PID limit for ever. Every container now
  runs the same init, which never forks: `/bin/bash -c "exec 3<>/dev/ptmx; trap 'exit 0' TERM;
  while :; do read -t 1 -u 3 _; done"` (the pty master needs no writable mount and nothing writes
  to it; bash reaps orphans between reads; SIGTERM exits at once). This supersedes the spike's
  inits; the lab image needs `/bin/bash` and `/dev/ptmx`.
- *Exec at the PID limit.* Three changes in `K8sExecAdapter`, each found on kind: (a) a command
  with `timeout_s` runs under `/usr/bin/timeout -s KILL` inside the container, as in the Docker
  provider, because a second exec (the marker kill) cannot even start while a bomb holds the pod
  PID limit; the client-side limit is a backstop; (b) the marker kill script is bash builtins only
  (no `tr | grep` pipeline), so it runs at the limit; (c) an exec whose process the runtime could
  not start (`500`, "failed to start exec") is retried with backoff up to the operation timeout;
  it never ran, so a retry cannot run a command twice. The command's environment is now exactly
  `env` plus the marker (`env -i`), so no image or `KUBERNETES_*` variable reaches a Check
  (Req 10.4); the terminal gets an explicit `PATH`.
- *OOM victim.* The last wrapper step raises the command's `oom_score_adj` to 1000 (a non-root
  process may raise its own score), so a memory OOM in a container kills the command's processes,
  never the container init (an OOM-killed init ends the pod) nor the `timeout` that must end the
  command.
- *Grader resources.* Checks run in the `grader` container. At the helper size (50 m, 16 MiB) a
  Check fork bomb livelocked the container cgroup in memory reclaim under CPU throttling: neither
  `timeout` nor SIGKILL nor `cgroup.kill` ended it, and the processes outlived the pod. In Docker a
  Check gets the whole Sandbox (1 CPU, 512 MiB, the learner frozen). The `grader` container now
  has `K8S_GRADER_CPU_MILLI` (500 m) and `K8S_GRADER_MEMORY_BYTES` (256 MiB), requests equal to
  limits; the `owner` keeps `K8S_HELPER_*` (50 m, 16 MiB) because it only runs `tar` and `chmod`
  on the fixed bundle. Consequence: Session_Cost rises by 450 m CPU and 240 MiB per Lab_Pod.
- *Freeze in a fresh pod.* In the learner's own container `kill -s STOP -1` exits 1 (ESRCH) when
  only the init runs (Docker always has its learner-owned keep-alive to signal). For the
  `kubernetes` provider `GradeRun` therefore requires that the stop exec ran and takes the
  verified listing as the proof; the listing runs without `timeout_s`, because a learner-owned
  `timeout` wrapper would itself be a running learner process. The Docker path is unchanged.
- *Fixture identities.* The grading-isolation fixture Checks (`c_identity`, `c_bundle`,
  `c_frozen`) derive the identity table from the UID they run as (Docker 1001, Kubernetes 10001),
  and on Kubernetes `c_frozen` requires that the grader sees no learner process at all; the
  learner counter sampled around each Check still proves the freeze. `c_env` allows the
  provider-injected `EDU_IT_EXEC` marker as it allows Docker's `HOSTNAME`. The abuse and
  grading-isolation tests pass the provider name to `GradeRun` (as the Worker composition does)
  and use one session id per provisioned Sandbox (the pod name derives from it).
- *Disk.* Memory-backed `emptyDir` with `sizeLimit` gives `ENOSPC` at the size; a disk-backed
  `emptyDir` is only evicted later by the kubelet. The suite assertion is unchanged.
- *Network.* A pod always has an interface, so "only `lo`" has no Kubernetes equivalent. The
  Kubernetes branch asserts that no connection leaves the pod (internet and cluster IP) under the
  default-deny NetworkPolicy of `labs`, which the kind fixture applies (Calico enforces it); the
  interface list may hold only `lo`, `eth0` and Calico's `tunl0`. This is the one assertion adapted
  for genuinely different Kubernetes behaviour; no test is skipped or excluded.

### Evidence status

| Claim | Evidence today | Pending |
|-------|----------------|---------|
| Pod layout B isolation, freeze, RBAC verbs, Api exec scoping, group kill, PID limit | Day-one spike on kind (task 1.2), see "Spike result" | Chart-rendered RBAC and admission policy tests (chart tasks) |
| Provider lifecycle, exec, copy and terminal logic, error mapping | Unit and property tests over `FakeK8sApi`, and the Sandbox_Port contract suite with zero skips (call logic, not security evidence) | none for the fake |
| The fake and the production client behave like a real API server (409 adopt, quota 403, PSA 403, missing RuntimeClass, Pending Unschedulable, delete delay, exec stream close, 404) | Task 6.22's real-API contract test, run on kind against `FakeK8sApi`, `KubectlK8sApi` and `K8sClientApi` (the production client); all three give the same classification, exception type and final pod set for the nine scenarios (checkpoint 7) | none |
| Labs isolation, grading isolation, tamper-resistance and check-abuse suites pass on `KubernetesSandboxProvider` | The suites can be pointed at the Kubernetes provider through a backend switch (`tests/labs/test_sandbox_backend.py`); task 6.23 runs them on kind | none |
| Production `K8sApiPort` client adapter (`edu_it_infrastructure.sandbox.k8s_client.K8sClientApi`) | Unit tests of the pure translation (`pod_view_from_v1pod`, `translate_exception`, `exit_code_of`) over a stubbed `CoreV1Api`, plus the real-API contract test on kind (above) | Authentication by in-cluster ServiceAccount token is exercised only against a kubeconfig context on kind (`build_k8s_api_from_kubeconfig`); `build_k8s_api`'s in-cluster code path has no in-cluster test (it needs the client to run inside a pod) |

Checkpoint 7 closed the real-cluster gap for the provider and the production client: the contract
test and the shared provider suites both ran on kind.

### Spike result

**Status: RUN, layout B chosen (task 1.2, risk R1 closed).** Environment: kind v0.33.0 (built from
the `sigs.k8s.io/kind@v0.33.0` module, checksum-verified by the Go module proxy, because GitHub
release assets were unreachable from the authoring machine), Kubernetes v1.37.0 single node,
Docker Engine 29.9.2, image `busybox:1.36.1`.

- Cluster: `deploy/kind/spike-config.yaml` (one node, `podPidsLimit: 128`; restricted PSA enforced
  by labels on the `labs-spike` namespace).
- Command: `python tests/k8s/spike_pod_layout_kind.py --create-cluster --evaluate-all --evidence .e2e/spike-r1.json`
  (`--evaluate-all` also runs layout A as evidence; A is still chosen only if B fails).
- Raw evidence: `.e2e/spike-r1.json` (structured) and `.e2e/spike-r1.out` (console log).

| Check | Layout B | Layout A |
|-------|----------|----------|
| Pod admitted by restricted PSA, all capabilities dropped, Ready, UIDs 10000/10001/10002 | pass | pass |
| (1) `kill -STOP -1` in `learner` stops every learner process, verified within 1 s | pass: 9 of 9 stopped on attempt 1, 0.00 s in-container (10 ms clock resolution), 0.17 s exec round trip | pass: 10 of 10, 0.01 s |
| (2a) learner cannot list, read or stat `/grader` (directly or through `/proc/<pid>/root`) | pass: not mounted, 0 `/proc` hits | pass |
| (2b) learner cannot read `environ`, `cmdline` or `fd` of grader/owner processes | pass: 0 other-UID processes visible | **fail**: `cmdline` of 5 other-UID processes readable (environ and fd denied, secret not leaked) |
| (3a) Worker SA execs into `learner`, `grader`, `owner` | pass | pass |
| (3b) Worker `pods/exec` verbs are exactly `create`, `get` | pass | pass |
| (3c) Api SA execs only into `learner` | pass with the admission policy below (RBAC alone: fail, Api could exec into `grader` and `owner` in the first run) | pass with the policy |
| (4) command killed on `timeout_s` (group SIGKILL) leaves no process in its group | pass: 4 before, 0 live, 0 zombies after 6 s | pass |
| (5) fork bomb (400 requested) stopped by the PID limit | pass: fork failure at 121 container PIDs (pod limit 128), container recovered to 3 | pass |

| Field | Value |
|-------|-------|
| Chosen layout | **B**: three containers (`learner` 10000, `grader` 10001, `owner` 10002), `shareProcessNamespace: false`, freeze through `exec` in `learner`, all helper identities kept with all capabilities dropped |
| Evidence | Table above; `.e2e/spike-r1.json`, `.e2e/spike-r1.out` |
| Rejected layouts | **A** (shared process namespace): runs only if B fails, and B passed; A also fails check (2b) because `/proc/<pid>/cmdline` is world-readable across UIDs in a shared PID namespace. **D**: not evaluated, because B passed. **C**: recorded only, never run |

Findings that bind the provider code (tasks 4.3, 4.4, 5.2 to 5.10):

- **Api exec scoping needs an admission policy.** RBAC cannot restrict `pods/exec` by container.
  A `ValidatingAdmissionPolicy` (with a binding in `Deny` mode, scoped to the `labs` namespace) on
  `CONNECT` of `pods/exec` and `pods/attach` matches the Api ServiceAccount through
  `request.userInfo.username` and requires `object.container == 'learner'` (the object is the
  `PodExecOptions`). It was verified to deny Api exec into `grader` and `owner` and to leave the
  Worker unaffected. These are cluster-scoped objects (not a `ClusterRole`), so the chart renders
  them as cluster-scoped admission resources (ADR 0010). With `failurePolicy: Fail` an
  unavailable policy denies rather than allows.
- **The learner container needs a robust, reaping PID 1.** In layout B the learner container's
  PID 1 is excluded from `kill(-1)` (kernel rule), so it must run no learner code, must survive its
  children being stopped and must reap orphans. A bare busybox `sleep` entrypoint exited when the
  reparented workload was stopped and restarted the container mid-grading. The spike used
  `while :; do sleep 5 & wait $!; done`, which survives and reaps (0 zombies after a group kill).
  The lab image entrypoint must meet the same contract (a reaping init).
- **The freeze is verified with shell builtins only, timed inside the container.** No fork after
  `SIGSTOP`, so the check cannot be skewed by kubectl exec overhead or blocked by the PID limit.

## Consequences

Using `pods/exec` keeps the lab image free of agents and listening ports and makes RBAC, plus the
Api exec admission policy, the access control, at the cost that the Api and the Worker need egress to the Kubernetes API server
(an `ipBlock` value per Target) and that exec traffic is bounded by API server capacity. Pod-network
policies in `labs` become defense in depth, not the primary control.

Layout B (now built, see "Pod layout as built") removes the need for cross-UID signalling and `CAP_KILL`, and hides helper processes from
the learner by construction. The cost is two extra containers per pod (the `grader` with
500 m CPU and 256 MiB, because Checks run there, and the `owner` with 50 m and 16 MiB, all counted
in Session_Cost), node-pool kubelet settings (`podPidsLimit`, `singleProcessOOMKill`) that the
chart cannot enforce, and a dependency on the spike: the pod builder, the `as_user`
table and Property 2 change together if the recorded layout is not B.

The deterministic name makes provisioning idempotent and lets the Reaper match pods to sessions
without extra state. The PID limit depends on node pool configuration that the chart cannot
enforce, so it is a go-live item rather than a rendered control. Quota rejections can be requeued
without a retry penalty because they have their own error type.

The provider talks to the cluster only through `K8sApiPort`, so the fake and the real client are
interchangeable, at the cost that the fake can drift from a real API server; task 6.22 runs the same
scenarios on both to detect drift. The exec wrapper depends on `/bin/sh`, `/usr/bin/env`,
`/usr/bin/timeout` and a writable `/proc/self/oom_score_adj`, the marker kill on `/bin/bash` and
`/proc/<pid>/environ`, and the init on `/bin/bash` and `/dev/ptmx`; the lab image tasks must keep
them.

This decision flows back into #[[file:.kiro/steering/labs.md]] and
#[[file:.kiro/steering/deployment.md]] per #[[file:.kiro/steering/working-agreements.md]]. The ADR
was finalized (status `Accepted`) in task 5.10 together with the labs steering, when the provider
code of tasks 5.1 to 5.9 landed.
