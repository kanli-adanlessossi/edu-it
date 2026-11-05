"""Day-one pod layout spike on kind (risk R1, task 1.2, Req 16.12).

Tests layout B first (three containers, distinct non-root UIDs, no shareProcessNamespace, freeze
through ``exec`` in the ``learner`` container). Falls back to layout A (shared process namespace)
only if B fails the isolation checks and A passes all of them, then to layout D (layout C is
recorded only, never run).

Run (needs kind, docker and kubectl on PATH):

    python tests/k8s/spike_pod_layout_kind.py --create-cluster --evidence .e2e/spike-r1.json

Add ``--evaluate-all`` to also record layout A as evidence (selection rule unchanged).

Exit codes: 0 a layout passed, 1 no layout passed, 2 tooling unavailable (nothing was proven).
The script prints real observations only; it never fabricates evidence.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import textwrap
import time
from dataclasses import dataclass, field
from pathlib import Path

CLUSTER = "edu-it-spike"
CONTEXT = f"kind-{CLUSTER}"
NS = "labs-spike"
IMAGE = "busybox:1.36.1"
PID_LIMIT = 128  # SANDBOX_PID_LIMIT
FREEZE_BOUND_S = 1.0  # LAB_GRADING_FREEZE_BOUND
UIDS = {"learner": 10000, "grader": 10001, "owner": 10002}
SECRET = "spike-secret-value"
REPO = Path(__file__).resolve().parents[2]


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class LayoutResult:
    layout: str
    checks: list[Check] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return bool(self.checks) and all(c.ok for c in self.checks)

    def add(self, name: str, ok: bool, detail: str = "") -> None:
        self.checks.append(Check(name, ok, detail))
        print(f"  [{'PASS' if ok else 'FAIL'}] {self.layout}: {name} {detail}".rstrip())


def sh(args: list[str], stdin: str | None = None, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    # kind and kubectl print UTF-8; never rely on the Windows code page.
    return subprocess.run(
        args, input=stdin, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout, check=False,
    )


def kubectl(*args: str, stdin: str | None = None, as_sa: str | None = None, timeout: int = 120):
    cmd = ["kubectl", "--context", CONTEXT]
    if as_sa:
        cmd += ["--as", f"system:serviceaccount:{NS}:{as_sa}"]
    return sh(cmd + list(args), stdin=stdin, timeout=timeout)


def exec_in(pod: str, container: str, script: str, as_sa: str | None = None, timeout: int = 60):
    return kubectl("-n", NS, "exec", pod, "-c", container, "--", "sh", "-c", script, as_sa=as_sa, timeout=timeout)


# --- manifests -----------------------------------------------------------------------------------


def namespace_manifest() -> str:
    return textwrap.dedent(f"""\
        apiVersion: v1
        kind: Namespace
        metadata:
          name: {NS}
          labels:
            pod-security.kubernetes.io/enforce: restricted
            pod-security.kubernetes.io/enforce-version: latest
        ---
        apiVersion: v1
        kind: ServiceAccount
        metadata: {{name: worker, namespace: {NS}}}
        ---
        apiVersion: v1
        kind: ServiceAccount
        metadata: {{name: api, namespace: {NS}}}
        ---
        apiVersion: rbac.authorization.k8s.io/v1
        kind: Role
        metadata: {{name: worker, namespace: {NS}}}
        rules:
          - {{apiGroups: [""], resources: [pods], verbs: [get, list, watch, create, delete]}}
          - {{apiGroups: [""], resources: [pods/exec], verbs: [create, get]}}
        ---
        apiVersion: rbac.authorization.k8s.io/v1
        kind: Role
        metadata: {{name: api, namespace: {NS}}}
        rules:
          - {{apiGroups: [""], resources: [pods], verbs: [get]}}
          - {{apiGroups: [""], resources: [pods/exec], verbs: [create]}}
        ---
        apiVersion: rbac.authorization.k8s.io/v1
        kind: RoleBinding
        metadata: {{name: worker, namespace: {NS}}}
        roleRef: {{apiGroup: rbac.authorization.k8s.io, kind: Role, name: worker}}
        subjects: [{{kind: ServiceAccount, name: worker, namespace: {NS}}}]
        ---
        apiVersion: rbac.authorization.k8s.io/v1
        kind: RoleBinding
        metadata: {{name: api, namespace: {NS}}}
        roleRef: {{apiGroup: rbac.authorization.k8s.io, kind: Role, name: api}}
        subjects: [{{kind: ServiceAccount, name: api, namespace: {NS}}}]
        """)


# RBAC cannot scope pods/exec by container, so the Api restriction is an admission rule on the
# CONNECT of pods/exec (object is the PodExecOptions, whose .container is the target container).
API_EXEC_POLICY = "edu-it-api-exec-learner-only"


def api_exec_policy_manifest() -> str:
    return textwrap.dedent(f"""\
        apiVersion: admissionregistration.k8s.io/v1
        kind: ValidatingAdmissionPolicy
        metadata: {{name: {API_EXEC_POLICY}}}
        spec:
          failurePolicy: Fail
          matchConstraints:
            resourceRules:
              - apiGroups: [""]
                apiVersions: [v1]
                operations: [CONNECT]
                resources: [pods/exec, pods/attach]
          matchConditions:
            - name: api-service-account
              expression: "request.userInfo.username == 'system:serviceaccount:{NS}:api'"
          validations:
            - expression: "has(object.container) && object.container == 'learner'"
              message: "the api ServiceAccount may exec only into the learner container"
        ---
        apiVersion: admissionregistration.k8s.io/v1
        kind: ValidatingAdmissionPolicyBinding
        metadata: {{name: {API_EXEC_POLICY}}}
        spec:
          policyName: {API_EXEC_POLICY}
          validationActions: [Deny]
          matchResources:
            namespaceSelector:
              matchLabels: {{kubernetes.io/metadata.name: {NS}}}
        """)


# Container keepalive. A bare busybox "sleep" as PID 1 exited when reparented children were stopped,
# which restarted the container mid-check. This loop survives that and, being the PID 1 of the
# learner container in layout B, reaps orphaned children (ash waits with waitpid(-1)).
KEEPALIVE = "while :; do sleep 5 & wait $!; done"


def pod_manifest(name: str, share_pid_ns: bool) -> str:
    def container(cname: str, mounts_grader: bool) -> str:
        uid = UIDS[cname]
        mount = "\n            volumeMounts: [{name: grader, mountPath: /grader}]" if mounts_grader else ""
        return textwrap.dedent(f"""\
          - name: {cname}
            image: {IMAGE}
            command: [sh, -c, {json.dumps(KEEPALIVE)}]
            env: [{{name: ROLE_SECRET, value: {SECRET}-{cname}}}]
            securityContext:
              runAsNonRoot: true
              runAsUser: {uid}
              runAsGroup: {uid}
              allowPrivilegeEscalation: false
              capabilities: {{drop: [ALL]}}
              seccompProfile: {{type: RuntimeDefault}}{mount}
        """)

    body = "".join(
        textwrap.indent(container(c, c != "learner"), "    ") for c in ("learner", "grader", "owner")
    )
    return (
        f"apiVersion: v1\nkind: Pod\nmetadata: {{name: {name}, namespace: {NS}}}\n"
        f"spec:\n  shareProcessNamespace: {str(share_pid_ns).lower()}\n"
        "  automountServiceAccountToken: false\n  terminationGracePeriodSeconds: 1\n"
        "  volumes: [{name: grader, emptyDir: {}}]\n  containers:\n" + body
    )


# --- checks --------------------------------------------------------------------------------------

# Freeze command used by GradeRun: SIGSTOP every process the learner identity may signal, from
# inside the learner container, with no capability. kill(-1) never signals the caller or the PID 1 of
# the caller's PID namespace (kernel rule); in layout B that PID 1 is the container keepalive, which
# runs no learner code. The verification loop uses shell builtins only (no fork after the stop) and
# is timed inside the container from /proc/uptime, so kubectl exec overhead is not counted.
FREEZE_ATTEMPTS = 5  # LAB_GRADING_FREEZE_ATTEMPTS
FREEZE_SCRIPT = (
    "own=$(id -u); read t0 _ < /proc/uptime; kill -STOP -1; a=0; ok=0; n=0; run=0; "
    f"while [ $a -lt {FREEZE_ATTEMPTS} ]; do a=$((a+1)); n=0; run=0; "
    "for d in /proc/[0-9]*; do p=${d#/proc/}; [ \"$p\" = \"$$\" ] && continue; [ \"$p\" = 1 ] && continue; "
    "u=; { while read k v x; do if [ \"$k\" = Uid: ]; then u=$v; break; fi; done < $d/status; } 2>/dev/null; "
    "[ \"$u\" = \"$own\" ] || continue; line=; { read line < $d/stat; } 2>/dev/null; [ -z \"$line\" ] && continue; "
    "set -- $line; n=$((n+1)); [ \"$3\" = T ] || run=$((run+1)); done; "
    "if [ $run -eq 0 ] && [ $n -gt 0 ]; then ok=1; break; fi; done; "
    "read t1 _ < /proc/uptime; echo \"attempts=$a ok=$ok stopped=$n running=$run t0=$t0 t1=$t1\""
)
WORKLOAD_START = (
    "printf 'while :; do :; done\\n' > /tmp/workload.sh; : > /tmp/wl.pids; "
    "for i in 1 2 3 4 5 6; do setsid sh /tmp/workload.sh </dev/null >/dev/null 2>&1 & echo $! >> /tmp/wl.pids; done; "
    "setsid sh -c 'sleep 300 & sh /tmp/workload.sh' </dev/null >/dev/null 2>&1 & echo $! >> /tmp/wl.pids; "
    "sleep 1; n=0; for p in $(cat /tmp/wl.pids); do [ -d /proc/$p ] && n=$((n+1)); done; echo started=$n"
)
# Thaw everything, then kill only the workload groups (never the keepalive).
WORKLOAD_STOP = (
    "kill -CONT -1; for p in $(cat /tmp/wl.pids); do kill -KILL -$p 2>/dev/null; done; sleep 1; "
    "n=0; for p in $(cat /tmp/wl.pids); do [ -d /proc/$p ] && n=$((n+1)); done; echo left=$n"
)


def _kv(out: str) -> dict[str, str]:
    return dict(tok.split("=", 1) for tok in out.split() if "=" in tok)


def check_freeze(r: LayoutResult, pod: str) -> None:
    started = exec_in(pod, "learner", WORKLOAD_START).stdout.strip()
    if _kv(started).get("started") != "7":
        r.add("(1) freeze: workload processes started", False, started)
        return
    t_outer = time.monotonic()
    res = exec_in(pod, "learner", FREEZE_SCRIPT)
    outer = time.monotonic() - t_outer
    kv = _kv(res.stdout)
    try:
        inside = float(kv["t1"]) - float(kv["t0"])
        ok = kv["ok"] == "1" and inside <= FREEZE_BOUND_S
    except (KeyError, ValueError):
        inside, ok = float("nan"), False
    stop = exec_in(pod, "learner", WORKLOAD_STOP).stdout.strip()
    r.add(
        "(1) freeze stops every learner process and the stop is verified within the freeze bound",
        ok,
        f"{res.stdout.strip()} in-container={inside:.2f}s bound={FREEZE_BOUND_S}s "
        f"(exec round trip {outer:.2f}s); cleanup {stop}",
    )


def check_isolation(r: LayoutResult, pod: str) -> None:
    ls = exec_in(pod, "learner", "ls -la /grader 2>&1; echo rc=$?")
    stat = exec_in(pod, "learner", "stat /grader 2>&1; echo rc=$?")
    cat = exec_in(pod, "learner", "cat /grader/x 2>&1; echo rc=$?")
    # With a shared process namespace the mount is also reachable through /proc/<pid>/root.
    via_proc = exec_in(
        pod, "learner",
        "n=0; for d in /proc/[0-9]*; do ls $d/root/grader >/dev/null 2>&1 && n=$((n+1)); "
        "stat $d/root/grader >/dev/null 2>&1 && n=$((n+1)); done; echo proc_root_hits=$n",
    )
    denied = all("rc=0" not in o.stdout for o in (ls, stat, cat)) and "proc_root_hits=0" in via_proc.stdout
    r.add(
        "(2a) learner cannot list, read or stat /grader",
        denied,
        f"{ls.stdout.strip()[:60]} | {via_proc.stdout.strip()}",
    )

    # Strict reading of the task: any successful read of environ or cmdline (non-empty) or listing of
    # fd of a process owned by another UID is a leak, whether or not it contains the secret.
    script = (
        "own=$(id -u); seen=0; env=0; cmd=0; fd=0; secret=0; "
        "for d in /proc/[0-9]*; do u=$(awk '/^Uid:/{print $2}' $d/status 2>/dev/null); "
        "[ -z \"$u\" ] && continue; [ \"$u\" = \"$own\" ] && continue; seen=$((seen+1)); "
        "[ -n \"$(cat $d/environ 2>/dev/null | tr '\\0' ' ')\" ] && env=$((env+1)); "
        "[ -n \"$(cat $d/cmdline 2>/dev/null | tr '\\0' ' ')\" ] && cmd=$((cmd+1)); "
        "ls $d/fd >/dev/null 2>&1 && fd=$((fd+1)); "
        f"cat $d/environ $d/cmdline 2>/dev/null | tr '\\0' ' ' | grep -q {SECRET} && secret=$((secret+1)); "
        "done; echo \"other_uid_procs=$seen environ_readable=$env cmdline_readable=$cmd fd_listable=$fd secret_hits=$secret\""
    )
    out = exec_in(pod, "learner", script).stdout.strip()
    kv = _kv(out)
    ok = bool(kv) and all(kv.get(k) == "0" for k in ("environ_readable", "cmdline_readable", "fd_listable", "secret_hits"))
    r.add("(2b) learner cannot read environ, cmdline or fd of grader/owner processes", ok, out)


def check_rbac(r: LayoutResult, pod: str) -> None:
    ok = True
    notes = []
    for c in ("learner", "grader", "owner"):
        res = exec_in(pod, c, "echo ok", as_sa="worker")
        good = res.returncode == 0 and "ok" in res.stdout
        ok &= good
        notes.append(f"worker->{c}={'ok' if good else 'denied'}")
    r.add("(3a) Worker SA execs into each container", ok, " ".join(notes))
    can = kubectl("-n", NS, "auth", "can-i", "--list", as_sa="worker").stdout
    exec_line = [ln for ln in can.splitlines() if ln.startswith("pods/exec")]
    verbs = exec_line[0].split("[")[-1].rstrip("]").split() if exec_line else []
    r.add("(3b) Worker pods/exec verbs are only create and get", sorted(verbs) == ["create", "get"], f"verbs={verbs}")
    api_learner = exec_in(pod, "learner", "echo ok", as_sa="api")
    api_helpers = {c: exec_in(pod, c, "echo ok", as_sa="api") for c in ("grader", "owner")}
    only_learner = api_learner.returncode == 0 and all(h.returncode != 0 for h in api_helpers.values())
    detail = f"api->learner={'ok' if api_learner.returncode == 0 else 'denied'} " + " ".join(
        f"api->{c}={'ok' if h.returncode == 0 else 'denied'}" for c, h in api_helpers.items()
    )
    denial = next((h.stderr.strip() for h in api_helpers.values() if h.returncode != 0), "")
    r.add(
        f"(3c) Api SA execs only into learner (RBAC + ValidatingAdmissionPolicy {API_EXEC_POLICY})",
        only_learner,
        f"{detail} {denial[:160]}".strip(),
    )


def check_timeout_group(r: LayoutResult, pod: str) -> None:
    # Kill the whole group, as the provider does on timeout_s, then wait past one keepalive cycle so
    # orphans can be reaped. Live processes and zombies left in the group are both counted.
    script = (
        "setsid sh -c 'sleep 300 & sleep 300 & (sleep 300 &) ; wait' </dev/null >/dev/null 2>&1 & "
        "sleep 1; g=$(ps -o pid,pgid,args | awk '/sleep 300/ && !/awk/{print $2; exit}'); "
        "before=$(ps -o pgid | awk -v g=$g '$1==g {n++} END{print n+0}'); "
        "kill -KILL -$g; sleep 6; "
        "live=$(ps -o pgid,stat | awk -v g=$g '$1==g && $2 !~ /^Z/ {n++} END{print n+0}'); "
        "zombie=$(ps -o pgid,stat | awk -v g=$g '$1==g && $2 ~ /^Z/ {n++} END{print n+0}'); "
        "echo \"pgid=$g before=$before live=$live zombie=$zombie\""
    )
    out = exec_in(pod, "learner", script).stdout.strip()
    kv = _kv(out)
    ok = kv.get("pgid", "") != "" and int(kv.get("before", "0")) >= 3 and kv.get("live") == "0" and kv.get("zombie") == "0"
    r.add("(4) command killed on timeout_s leaves no process in its group", ok, out)


def check_fork_bomb(r: LayoutResult, pod: str) -> None:
    # Once the PID limit is hit nothing can fork, so the saturated state is read with builtins only
    # (read from the container cgroup) and the bomb's group is killed with the builtin kill.
    bomb = (
        "sleep 6 & s=$!; "
        "setsid sh -c 'i=0; while [ $i -lt 400 ]; do sleep 120 & i=$((i+1)); done; wait' "
        "</dev/null >/dev/null 2>/tmp/bomb.err & b=$!; "
        "wait $s; c=; read c < /sys/fs/cgroup/pids.current; echo \"container_pids_at_saturation=$c\"; "
        "kill -KILL -$b"
    )
    out1 = exec_in(pod, "learner", bomb, timeout=90).stdout.strip()
    out2 = exec_in(
        pod, "learner",
        "sleep 6; e=$(grep -c -i -e \"can't fork\" -e 'resource temporarily unavailable' /tmp/bomb.err); "
        "c=; read c < /sys/fs/cgroup/pids.current; echo \"forkerrors=$e container_pids_after=$c\"",
        timeout=90,
    ).stdout.strip()
    kv = {**_kv(out1), **_kv(out2)}
    try:
        sat = int(kv["container_pids_at_saturation"])
        ferr = int(kv["forkerrors"])
    except (KeyError, ValueError):
        r.add("(5) fork bomb stopped by the PID limit", False, f"unparsable: {out1!r} {out2!r}")
        return
    # 400 children were requested; the pod limit is 128, so fork errors must occur below the limit and
    # the container must recover (the second exec ran and the count dropped).
    ok = ferr > 0 and sat <= PID_LIMIT and "container_pids_after" in kv
    r.add("(5) fork bomb stopped by the PID limit", ok, f"{out1} {out2} pod_limit={PID_LIMIT} requested=400")


def run_layout(layout: str, share_pid_ns: bool) -> LayoutResult:
    r = LayoutResult(layout)
    pod = f"lab-spike-{layout.lower()}"
    print(f"== layout {layout} (shareProcessNamespace={share_pid_ns}) ==")
    kubectl("-n", NS, "delete", "pod", pod, "--ignore-not-found", "--wait=true")
    created = kubectl("apply", "-f", "-", stdin=pod_manifest(pod, share_pid_ns))
    r.add("pod admitted by restricted PSA with all capabilities dropped", created.returncode == 0, created.stderr.strip()[:200])
    if created.returncode != 0:
        return r
    ready = kubectl("-n", NS, "wait", "--for=condition=Ready", f"pod/{pod}", "--timeout=90s")
    r.add("pod Ready (3 containers)", ready.returncode == 0, ready.stderr.strip()[:200])
    if ready.returncode != 0:
        return r
    uids = {c: exec_in(pod, c, "id -u").stdout.strip() for c in UIDS}
    r.add("distinct non-root UIDs", sorted(uids.values()) == sorted(str(u) for u in UIDS.values()), str(uids))
    for check in (check_freeze, check_isolation, check_rbac, check_timeout_group, check_fork_bomb):
        try:
            check(r, pod)
        except subprocess.TimeoutExpired:
            r.add(check.__name__, False, "timed out")
    kubectl("-n", NS, "delete", "pod", pod, "--ignore-not-found", "--wait=false")
    return r


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--create-cluster", action="store_true")
    ap.add_argument("--delete-cluster", action="store_true")
    ap.add_argument("--evidence", type=Path, help="write the observed results as JSON")
    ap.add_argument("--evaluate-all", action="store_true", help="also run layout A as evidence when B passes")
    args = ap.parse_args()

    missing = [t for t in ("kind", "docker", "kubectl") if shutil.which(t) is None]
    if missing:
        print(f"SPIKE NOT RUN: missing tools: {', '.join(missing)}. No evidence was produced.")
        return 2
    if args.create_cluster:
        cfg = REPO / "deploy" / "kind" / "spike-config.yaml"
        p = sh(["kind", "create", "cluster", "--config", str(cfg)], timeout=600)
        if p.returncode != 0 and "already exist" not in p.stderr:
            print(p.stderr)
            return 2
    ver = kubectl("version", "-o", "json")
    server = json.loads(ver.stdout).get("serverVersion", {}) if ver.returncode == 0 and ver.stdout else {}
    applied = kubectl("apply", "-f", "-", stdin=namespace_manifest())
    if applied.returncode != 0:
        print(applied.stderr)
        return 2
    # The ServiceAccount controller creates "default" asynchronously after the namespace exists.
    for _ in range(60):
        if kubectl("-n", NS, "get", "serviceaccount", "default").returncode == 0:
            break
        time.sleep(1)
    vap = kubectl("apply", "-f", "-", stdin=api_exec_policy_manifest())
    print(f"api exec policy: {(vap.stdout or vap.stderr).strip()}")
    time.sleep(3)  # policy and binding are picked up asynchronously by the API server

    results: list[LayoutResult] = []
    chosen = None
    b = run_layout("B", share_pid_ns=False)
    results.append(b)
    if b.passed:
        chosen = "B"
    if not b.passed or args.evaluate_all:
        # --evaluate-all records A as evidence only; A is chosen only when B fails (Req 16.12).
        a = run_layout("A", share_pid_ns=True)
        results.append(a)
        if not b.passed:
            chosen = "A" if a.passed else None
    # Layout D is evaluated by hand only if both fail; layout C is recorded only (never run).
    report = {
        "kubernetes": server.get("gitVersion"),
        "chosen": chosen or "none (evaluate layout D; layout C is recorded only)",
        "layouts": [{"layout": x.layout, "passed": x.passed, "checks": [c.__dict__ for c in x.checks]} for x in results],
    }
    print(json.dumps(report, indent=2))
    if args.evidence:
        args.evidence.parent.mkdir(parents=True, exist_ok=True)
        args.evidence.write_text(json.dumps(report, indent=2), encoding="utf-8")
    if args.delete_cluster:
        sh(["kind", "delete", "cluster", "--name", CLUSTER], timeout=300)
    return 0 if chosen else 1


if __name__ == "__main__":
    sys.exit(main())
