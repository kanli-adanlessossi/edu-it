---
inclusion: fileMatch
fileMatchPattern: "lab-images/**|apps/api/**/{lab,labs,sandbox,terminal}*/**|apps/api/**/*{lab,sandbox,terminal}*|apps/worker/**/{lab,labs,sandbox,terminal,grading,reaper}*/**|apps/worker/**/*{lab,sandbox,terminal,grading,reaper}*|apps/web/**/*{lab,terminal,xterm}*|**/*sandbox*|**/*{SandboxProvider,sandbox_provider}*"
---

# Labs, Sandboxes and Terminal

This steering file is the single source of truth for the EDU-IT labs, sandbox and terminal
rules. It is included for lab image definitions, sandbox code, and lab/terminal code
(conditional fileMatch inclusion). The labs, sandbox, and terminal rules below are transcribed
verbatim from the project rules; all names, numbers, and normative wording are preserved
unchanged. Where a rule is not self-explanatory, a one-line reason is added and marked `Reason:`.

## Mastery Mechanisms (labs)

- Learners rebuild skills step by step and advance only by proving mastery through quizzes,
  hands-on labs (real terminals, graded code) and projects.

## Sandbox Port

- Sandbox: Behind `SandboxProvider`: Docker (dev only), Kubernetes (prod), fake (tests).
- The sandbox sits behind the `SandboxProvider` port: Docker (dev only), Kubernetes (prod),
  fake (tests).
- `SandboxProvider`: implementations are Docker (dev only), Kubernetes (prod), Fake (tests);
  warm-pool extension point.

The `SandboxProvider` interface signatures (illustrative pseudocode; the authoritative versions
live in `docs/LABS-DESIGN.md` (D4)):

```pascal
INTERFACE SandboxProvider              // see docs/LABS-DESIGN.md (D4)
  FUNCTION provision(spec) RETURNS SandboxHandle
  FUNCTION exec(handle, command, as_user, env, cwd) RETURNS ExecResult
  FUNCTION attach_terminal(handle) RETURNS TerminalChannel
  FUNCTION copy_in(handle, source_ro, dest) RETURNS Void    // read-only source for grading
  FUNCTION copy_out(handle, source, dest) RETURNS Void
  FUNCTION destroy(handle) RETURNS Void
  FUNCTION health(handle) RETURNS HealthStatus
  // Implementations: Docker (dev only), Kubernetes (prod), Fake (tests). Warm-pool extension point.
END INTERFACE
```

## Terminal

- `apps/web`: renders the xterm.js terminal.
- `apps/api`: hosts the authenticated terminal WebSocket.
- `apps/worker`: lab provisioning and grading orchestration, reaper for expired sessions.
- The api can attach terminal TTY to the sandbox.

## Runtime and Topology (labs)

- labs namespace — restricted PSA; one pod per session.
- Dedicated lab node pool with taints/tolerations; configurable RuntimeClass (gVisor or Kata;
  plain runc dev-only with a startup warning).
- Default-deny NetworkPolicy in `labs`.
- Graceful shutdown drains terminal WebSockets with a reconnect message.
- The worker provisions, executes and destroys the per-session pool.

## Grading Bounds

- Grading is bounded: results within a 15 s target, executed at most once per 10 s per session.

## Failure Scenarios

### Sandbox provisioning failure

- IF sandbox provisioning fails, the system surfaces a translated error, leaves no partial
  session running, lets the reaper clean orphaned resources, and applies provisioning
  rate-limits and retries bounded by the maximum values stated in `docs/LABS-DESIGN.md`.

### WebSocket drop

- IF a terminal WebSocket connection drops, the system re-verifies session ownership on
  reconnect, presents a reconnect indication, drains active sockets on graceful shutdown, and
  persists no terminal I/O.

### Grading check timeout

- IF a grading check does not return within the 15 s target, grading is bounded (results within
  a 15 s target, executed at most once per 10 s per session), the timeout yields a translated
  "could not complete" feedback instead of raw output, and the run is recorded with a failed
  status.

## Kubernetes Sandbox Provider (ADR 0009)

Rules from `docs/adr/0009-kubernetes-sandbox-provider.md` (status Accepted, finalized in task 5.10
of the `kubernetes-deployment` spec). The pod layout was confirmed by the day-one spike (task 1.2,
risk R1 closed) and is recorded in ADR 0009. The provider shipped in tasks 5.1 to 5.9. The
production `K8sApiPort` client adapter is `edu_it_infrastructure.sandbox.k8s_client` (official
`kubernetes` Python client, pinned exactly; in-cluster ServiceAccount token). The real-API contract
run (task 6.22) and the shared provider suites on kind (task 6.23) are the evidence; see the
evidence table of ADR 0009.

- Exec, copy and terminal use the Kubernetes `pods/exec` subresource through the API server. No
  agent runs in the lab image and no listening port is opened in the lab pod.
  Reason: standard Kubernetes API only, and the RBAC grant is the access control.
- Worker RBAC: one `Role` in `labs`, `create`, `get`, `list`, `watch`, `delete` on `pods` and
  `create`, `get` on `pods/exec`. Api RBAC: one `Role` in `labs`, `get` on `pods` and `create` on
  `pods/exec`, with no pod create, update, patch, delete or deletecollection. No `ClusterRole` or
  `ClusterRoleBinding` for platform ServiceAccounts. The chart renders these roles (tasks 6.10 and
  12.x); the RBAC grants were proven by the spike on kind (check 3).
- Api exec admission policy: RBAC cannot scope `pods/exec` by container, so a
  `ValidatingAdmissionPolicy` plus a binding (`Deny` mode, scoped to the `labs` namespace) on
  `CONNECT` of `pods/exec` and `pods/attach` restricts the Api ServiceAccount to the container
  `learner`. It matches the Api identity through `request.userInfo.username` and requires
  `object.container == 'learner'` (the object is the `PodExecOptions`). The Worker is unaffected.
  `failurePolicy` is `Fail`, so an unavailable policy denies rather than allows. These are
  cluster-scoped admission resources, not a `ClusterRole`; the chart renders them (see
  `deployment.md`).
  Reason: the Api terminal must never reach the `grader` or `owner` containers.
- Pod layout B is chosen and spike-proven (task 1.2) and built by `build_lab_pod`: three
  containers `learner` (UID 10000), `grader` (UID 10001) and `owner` (UID 10002), all non-root, all
  capabilities dropped, `RuntimeDefault` seccomp, and `shareProcessNamespace: false`. `as_user`
  maps `learner` to `learner`, `grader` to `grader` and `root` to `owner`; any other `as_user` is a
  `SandboxError`. The `learner` container mounts `/workspace`, `/home/learner` and `/tmp`; the
  `grader` container mounts `/workspace` read-only and `/grader`; the `owner` container mounts
  `/grader`; the pod `fsGroup` is the grader GID (10001) and the `owner` runs with that GID as
  primary group. Every writable mount is a memory-backed `emptyDir` of exactly the Docker tmpfs
  size, so a write past the size fails at once with `ENOSPC`, as in Docker.
  `/grader` is a `subPath` (`bundle`) of the `v-grader` volume. The kubelet creates the volume
  root as `root:fsGroup` mode 2777, which a non-root identity can neither `chmod` (EPERM, no
  `CAP_CHOWN` or `CAP_FOWNER`) nor close against the grader. An init container running as the owner
  UID creates `bundle` (mode 2750, grader group), so the `owner` owns the mounted `/grader` and
  the housekeeping of `GradeRun` (`chmod 0775`, grader `mkdir run`, `chmod 0755`) works unchanged
  under restricted PSA with all capabilities dropped, and the grader cannot write there during
  Checks. Container resources are listed below (grader and owner sizes). Layout A (shared process namespace) is rejected because it fails the cmdline isolation
  check (`/proc/<pid>/cmdline` of other-UID processes is readable). Layout D is not needed because
  B passed. Layout C is record-only.
  Reason: learner processes cannot see helper processes, and the freeze needs no `CAP_KILL`.
- Reaping init requirement: the PID 1 of every container (`learner`, `grader`, `owner`) must run
  no user code, must survive its children being stopped (freeze), must reap orphans and must never
  fork. The pod builder sets every container command to
  `/bin/bash -c "exec 3<>/dev/ptmx; trap 'exit 0' TERM; while :; do read -t 1 -u 3 _; done"`, so
  the lab image needs `/bin/bash` and `/dev/ptmx`. A `tail` or `sleep` entrypoint does not reap
  (a killed fork bomb stays as zombies that hold the pod PID limit), and a loop that forks `sleep`
  dies with "can't fork" when a fork bomb has used the pod PID limit, which ends the pod.
  Reason: PID 1 is excluded from `kill(-1)`, and the abuse suites on kind found both failure modes.
- The `grader` container, where every Check runs, has `K8S_GRADER_CPU_MILLI` (500 m) and
  `K8S_GRADER_MEMORY_BYTES` (256 MiB); the `owner` keeps `K8S_HELPER_CPU_MILLI` (50 m) and
  `K8S_HELPER_MEMORY_BYTES` (16 MiB). Requests equal limits.
  Reason: at 50 m and 16 MiB an abusive Check livelocks the container cgroup so that not even
  SIGKILL ends it.
- The exec wrapper clears the environment (`env -i`: only `env` plus the marker reach the
  command), runs a command with `timeout_s` under `/usr/bin/timeout -s KILL` inside the container
  (a second exec cannot start while a fork bomb holds the PID limit), raises the command's
  `oom_score_adj` to 1000 so an OOM never picks the init or the `timeout`, and retries an exec
  whose process the runtime could not start (`500`, "failed to start exec") with backoff up to the
  operation timeout.
- The freeze of learner processes runs inside the `learner` container through `exec`. Its
  verification must also run there: the containers have separate process namespaces, so a
  listing as the `grader` sees none of the learner processes and would be vacuously "all stopped".
  `GradeRun` therefore lists processes in the `learner` container when the provider is
  `kubernetes`. The listing names its own PID (`SELF <pid>`), which is excluded as the one running
  process, every other row counts as a learner process (the lab image has no names for the pod
  UIDs), and a listing that does not show its own PID fails closed. The listing runs without
  `timeout_s` (a learner-owned `timeout` wrapper would be a running learner process), and the stop
  may exit 1 (ESRCH: nothing but the init to signal in a fresh pod) because the verified listing is
  the proof. Everything else in `GradeRun` (fail-closed freeze, bundle isolation, argv forms) is
  unchanged.
  Reason: a freeze that is verified in the wrong process namespace verifies nothing.
- Exec timeout: every `exec` carries a random per-command marker variable `EDU_IT_EXEC`. When
  `timeout_s` expires the provider runs a second exec in the same container that sends SIGKILL to
  every process carrying the marker, and reports exit code 137 with `timed_out`. The kill script
  is bash builtins only (no fork), so it still runs when a fork bomb has used the pod PID limit.
  PID 1 never carries the marker, so the pod stays usable. A pod that is gone raises `SandboxGone`.
  Reason: the marker also kills processes that left the process group or were orphaned.
- Copy is a tar stream over `exec` stdin (`copy_in`) or stdout (`copy_out`), with the host source
  only read and symlinks and special files refused. A destination under `/grader` is extracted in
  the `owner` container (owner UID, group read, no group write); every other destination is
  extracted in the container of `as_user` (default `learner`).
- The terminal is `/bin/bash -l` with a TTY in the `learner` container. After the pod is gone,
  `read`, `write` and `resize` raise `SandboxGone`. Closing the channel kills the shell by its
  marker.
- The pod name is deterministic: `lab-<first 20 hex of sha256(session_stable_id)>`. A `409` with
  the same session label adopts the pod; a `409` with a different session label is a
  `ProvisionError`.
  Reason: provisioning is idempotent after a Worker restart.
- The provider waits `min(LAB_PROVISION_TIMEOUT, K8S_READY_CEILING)` for Ready, with
  `K8S_READY_CEILING` equal to 60 s in `domain/config.py`, and deletes the pod and waits until it
  is absent before it raises a provisioning error.
- Destroy deletes the pod and waits until it is absent for at most `LAB_DESTROY_TIMEOUT` (30 s).
  A pod still present at the timeout is labelled `edu-it.io/destroy=pending` for the reaper and
  `destroy` raises a `SandboxError`; destroying an absent pod succeeds.
- The PID limit is the pod annotation `edu-it.io/pid-limit` (the Docker `SANDBOX_PID_LIMIT`
  value), enforced by the `podPidsLimit` kubelet setting of the lab node pool (a limit on the whole
  pod cgroup, which the learner container cannot read) and proven by a fork-bomb case in the
  isolation suite plus a check of the node setting.
- Memory containment on Kubernetes needs the kubelet setting `singleProcessOOMKill: true` on the
  lab node pool: an OOM kills the offending process, as in Docker, and not every process of the
  container (which with `restartPolicy: Never` would end the pod). The kind cluster sets it.
- Network containment on Kubernetes is the default-deny NetworkPolicy of `labs` (Calico enforces it
  on kind), not `network none`: a pod always has an interface. The shared isolation suite asserts
  that no connection leaves the pod on Kubernetes, and `lo` only on Docker.
- Quota rejection is the distinct domain error `ProvisionCapacityError`, a subclass of
  `ProvisionError`. A Pending pod that is Unschedulable at the ready timeout is also a
  `ProvisionCapacityError`. Learner-facing errors carry fixed text and never raw API text.
- Backend selection: `SANDBOX_PROVIDER=kubernetes` builds the provider from the `SANDBOX_*` and
  `K8S_LABS_NAMESPACE` settings (runtime kind and class, node selector, tolerations, node-loss
  toleration seconds, grader GID, priority class). The `K8sApiPort` client comes from
  `edu_it_infrastructure.sandbox.k8s_client` (the official `kubernetes` Python client, loaded
  lazily and pinned exactly in `packages/infrastructure/pyproject.toml`); without it startup is
  refused and the provider never falls back to the fake. It authenticates with the in-cluster
  ServiceAccount token: the Chart sets `automountServiceAccountToken: false` on the Api and Worker
  ServiceAccounts and instead projects a short-lived (`expirationSeconds: 600`), audience-bound
  token at the standard in-cluster path through `_kube-api-access.tpl` (ADR 0010).
- Evidence status: the Sandbox_Port contract suite runs the Kubernetes provider over
  `FakeK8sApi` with zero skips. The real-API contract test (task 6.22) runs the same nine
  scenarios on kind against `FakeK8sApi`, `KubectlK8sApi` and the production `K8sClientApi`, and
  all three agree. The labs isolation, grading isolation, tamper-resistance and check-abuse suites
  run on kind with the Kubernetes provider (task 6.23), with zero skips beyond the Docker run.
