"""Kubernetes lab Load_Test (kubernetes-deployment Req 24, task 14.8).

Starts 10 concurrent Lab_Pods on a kind cluster, holds them for 60 s, measures Sandbox_Ready_Time
(at least 20 samples over waves), terminal round trip and grading latency, samples runner CPU and
memory every 5 s or less, reports image pull time separately (risk R4), writes JSON results and
deletes every pod it created, also on failure, abort or timeout.

    python tools/perf/lab_load_k8s.py --context kind-edu-it --out .e2e/load-test.json

Exit code: 0 every target met, 1 a target missed or the run failed, 2 tooling unavailable.

The first part of this module (``nearest_rank_percentile``, ``evaluate_targets`` and the other pure
helpers) is framework-free and has no I/O so that property tests can import it.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

# --------------------------------------------------------------------------- targets (Req 24)

SESSIONS = 10  # 24.2
HOLD_SECONDS = 60.0  # 24.2
MIN_SAMPLES = 20  # 24.1
READY_P95_MAX_S = 30.0  # 24.1
READY_EACH_MAX_S = 120.0  # 24.2
GRADING_MAX_S = 15.0  # 24.4
TERMINAL_P95_MAX_S = 0.15  # 24.3, Phase 3 keystroke target
CLEANUP_MAX_S = 60.0  # 24.7
RESOURCE_SAMPLE_INTERVAL_S = 5.0  # 24.5, at most 5 s
LABEL = "edu-it.io/load-test"


# --------------------------------------------------------------------------- pure functions


def nearest_rank_percentile(values: Iterable[float], percentile: float) -> float:
    """Nearest-rank percentile: the value at rank ``ceil(p/100 * n)`` of the sorted values.

    Raises ``ValueError`` for no values or a percentile outside (0, 100].
    """

    ordered = sorted(values)
    if not ordered:
        raise ValueError("percentile of no values")
    if not 0 < percentile <= 100:
        raise ValueError("percentile must be in (0, 100]")
    rank = max(1, math.ceil(percentile / 100 * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


@dataclass(frozen=True)
class Target:
    """One threshold. ``kind`` is ``"max"`` (measured <= limit) or ``"min"`` (measured >= limit)."""

    name: str
    limit: float
    kind: str = "max"


@dataclass(frozen=True)
class TargetMiss:
    target: str
    measured: float | None
    threshold: float
    kind: str


def evaluate_targets(
    measurements: Mapping[str, float | None], targets: Sequence[Target]
) -> list[TargetMiss]:
    """Return exactly the targets whose measured value misses its threshold (Req 24.6).

    A target with no measurement (absent or ``None``) is missed. The run fails iff the result is
    not empty.
    """

    misses: list[TargetMiss] = []
    for target in targets:
        measured = measurements.get(target.name)
        if measured is None or math.isnan(measured):
            ok = False
        elif target.kind == "min":
            ok = measured >= target.limit
        else:
            ok = measured <= target.limit
        if not ok:
            misses.append(TargetMiss(target.name, measured, target.limit, target.kind))
    return misses


def default_targets(
    *,
    sessions: int = SESSIONS,
    hold_s: float = HOLD_SECONDS,
    min_samples: int = MIN_SAMPLES,
    ready_p95_s: float = READY_P95_MAX_S,
    terminal_p95_s: float = TERMINAL_P95_MAX_S,
) -> list[Target]:
    return [
        Target("sandbox_ready_samples", min_samples, "min"),
        Target("sandbox_ready_p95_seconds", ready_p95_s),
        Target("sandbox_ready_max_seconds", READY_EACH_MAX_S),
        Target("concurrent_sessions_started", sessions, "min"),
        Target("concurrent_sessions_held", sessions, "min"),
        Target("hold_seconds", hold_s, "min"),
        Target("terminal_p95_seconds", terminal_p95_s),
        Target("grading_max_seconds", GRADING_MAX_S),
        Target("cleanup_seconds", CLEANUP_MAX_S),
    ]


def summarize(values: Sequence[float]) -> dict[str, Any]:
    """Sample count, p50, p95 and max (Req 24.5); percentiles are ``None`` for no samples."""

    if not values:
        return {"count": 0, "p50": None, "p95": None, "max": None}
    return {
        "count": len(values),
        "p50": nearest_rank_percentile(values, 50),
        "p95": nearest_rank_percentile(values, 95),
        "max": max(values),
    }


_PULLED = re.compile(r"in (?P<n>[0-9.]+)\s*(?P<unit>ms|s|m)\b")
_PULLED_ALREADY = "already present"


def parse_pull_seconds(message: str) -> float | None:
    """Pull duration from a kubelet ``Pulled`` event message, 0.0 when the image was present."""

    if _PULLED_ALREADY in message:
        return 0.0
    match = _PULLED.search(message)
    if not match:
        return None
    value = float(match["n"])
    return value * {"ms": 0.001, "s": 1.0, "m": 60.0}[match["unit"]]


def parse_cpu_millicores(text: str) -> float | None:
    """``kubectl top`` CPU value (``12m`` or ``1``) in millicores."""

    try:
        return float(text[:-1]) if text.endswith("m") else float(text) * 1000
    except ValueError:
        return None


def parse_mebibytes(text: str) -> float | None:
    """``kubectl top`` memory value (``Ki``, ``Mi``, ``Gi``) in MiB."""

    scale = {"Ki": 1 / 1024, "Mi": 1.0, "Gi": 1024.0}
    for suffix, factor in scale.items():
        if text.endswith(suffix):
            try:
                return float(text[: -len(suffix)]) * factor
            except ValueError:
                return None
    return None


def per_session_cost(observations: Sequence[Mapping[str, float | None]]) -> dict[str, Any]:
    """Peak measured CPU (millicores), memory (MiB) and storage (MiB) per session.

    Each observation is one session at one instant. Missing metrics stay ``None`` so the
    Deployment_Guide can say "not measured".
    """

    out: dict[str, Any] = {}
    for key in ("cpu_millicores", "memory_mib", "storage_mib"):
        values = [o[key] for o in observations if o.get(key) is not None]
        out[key] = max(values) if values else None  # type: ignore[type-var]
    return out


# --------------------------------------------------------------------------- kubectl plumbing


class Kubectl:
    def __init__(self, context: str, namespace: str) -> None:
        self.context = context
        self.namespace = namespace

    def run(
        self, *args: str, stdin: str | None = None, timeout: float = 60
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["kubectl", "--context", self.context, *args],
            input=stdin,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )

    def json(self, *args: str) -> dict[str, Any]:
        done = self.run(*args, "-o", "json")
        if done.returncode != 0:
            raise RuntimeError(done.stderr.strip())
        return json.loads(done.stdout)


def pod_manifest(
    name: str, run_id: str, image: str, namespace: str, runtime_class: str | None
) -> dict[str, Any]:
    spec: dict[str, Any] = {
        "automountServiceAccountToken": False,
        "restartPolicy": "Never",
        "securityContext": {
            "runAsNonRoot": True,
            "runAsUser": 10000,
            "seccompProfile": {"type": "RuntimeDefault"},
        },
        "containers": [
            {
                "name": "learner",
                "image": image,
                "command": ["sleep", "3600"],
                "securityContext": {
                    "allowPrivilegeEscalation": False,
                    "capabilities": {"drop": ["ALL"]},
                },
                "resources": {
                    "requests": {"cpu": "50m", "memory": "64Mi", "ephemeral-storage": "64Mi"},
                    "limits": {"cpu": "500m", "memory": "256Mi", "ephemeral-storage": "256Mi"},
                },
            }
        ],
    }
    if runtime_class:
        spec["runtimeClassName"] = runtime_class
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "labels": {LABEL: run_id, "app.kubernetes.io/component": "lab-load-test"},
        },
        "spec": spec,
    }


# --------------------------------------------------------------------------- runner sampling


def runner_sample() -> dict[str, float | None]:
    """CPU percent and memory MiB of the machine running the test."""

    try:
        import psutil  # type: ignore

        return {
            "cpu_percent": psutil.cpu_percent(interval=None),
            "memory_mib": psutil.virtual_memory().used / 1048576,
        }
    except ImportError:
        pass
    try:  # Linux CI runners
        with open("/proc/meminfo", encoding="utf-8") as fh:
            info = {line.split(":")[0]: int(line.split()[1]) for line in fh if ":" in line}
        used = (info["MemTotal"] - info["MemAvailable"]) / 1024
        load = os.getloadavg()[0] / (os.cpu_count() or 1) * 100
        return {"cpu_percent": min(load, 100.0), "memory_mib": used}
    except (OSError, KeyError, AttributeError):
        return {"cpu_percent": None, "memory_mib": None}


class RunnerSampler:
    """Background sampler of runner CPU and memory every ``interval_s`` (at most 5 s)."""

    def __init__(self, interval_s: float = RESOURCE_SAMPLE_INTERVAL_S) -> None:
        self.interval_s = min(interval_s, RESOURCE_SAMPLE_INTERVAL_S)
        self.samples: list[dict[str, Any]] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def _loop(self) -> None:
        start = time.monotonic()
        while True:
            self.samples.append({"t": round(time.monotonic() - start, 2), **runner_sample()})
            if self._stop.wait(self.interval_s):
                return

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> dict[str, Any]:
        self._stop.set()
        self._thread.join(timeout=10)
        cpu = [s["cpu_percent"] for s in self.samples if s["cpu_percent"] is not None]
        mem = [s["memory_mib"] for s in self.samples if s["memory_mib"] is not None]
        return {
            "interval_seconds": self.interval_s,
            "sample_count": len(self.samples),
            "cpu_percent": {
                "avg": sum(cpu) / len(cpu) if cpu else None,
                "max": max(cpu, default=None),
            },
            "memory_mib": {
                "avg": sum(mem) / len(mem) if mem else None,
                "max": max(mem, default=None),
            },
            "samples": self.samples,
        }


# --------------------------------------------------------------------------- the run


class Aborted(Exception):
    pass


class LoadTest:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.k = Kubectl(args.context, args.namespace)
        self.run_id = uuid.uuid4().hex[:10]
        self.created: set[str] = set()
        self.ready_seconds: list[float] = []
        self.pull_seconds: list[float] = []
        self.terminal_seconds: list[float] = []
        self.grading_seconds: list[float] = []
        self.cost_obs: list[dict[str, float | None]] = []
        self.started_peak = 0
        self.held_min: int | None = None
        self.hold_achieved = 0.0
        self.errors: list[str] = []
        self.deadline = time.monotonic() + args.timeout

    # -- helpers

    def check_deadline(self) -> None:
        if time.monotonic() > self.deadline:
            raise Aborted("load test timed out")

    def my_pods(self) -> list[dict[str, Any]]:
        return self.k.json(
            "get", "pods", "-n", self.args.namespace, "-l", f"{LABEL}={self.run_id}"
        ).get("items", [])

    @staticmethod
    def is_ready(pod: Mapping[str, Any]) -> bool:
        conds = (pod.get("status") or {}).get("conditions") or []
        return any(c.get("type") == "Ready" and c.get("status") == "True" for c in conds)

    # -- waves

    def wave(self, index: int) -> None:
        names = [f"lt-{self.run_id}-{index}-{i}" for i in range(self.args.sessions)]
        requested: dict[str, float] = {}

        def create(name: str) -> None:
            manifest = pod_manifest(
                name, self.run_id, self.args.image, self.args.namespace, self.args.runtime_class
            )
            requested[name] = time.monotonic()
            self.created.add(name)  # register before the call so a half-created pod is cleaned
            done = self.k.run(
                "create", "-n", self.args.namespace, "-f", "-", stdin=json.dumps(manifest)
            )
            if done.returncode != 0:
                self.created.discard(name)
                requested.pop(name, None)
                self.errors.append(f"create {name}: {done.stderr.strip()[:300]}")

        with ThreadPoolExecutor(max_workers=len(names)) as pool:
            list(pool.map(create, names))

        ready_at: dict[str, float] = {}
        limit = time.monotonic() + READY_EACH_MAX_S
        while time.monotonic() < limit and len(ready_at) < len(requested):
            self.check_deadline()
            now = time.monotonic()
            for pod in self.my_pods():
                n = pod["metadata"]["name"]
                if n in requested and n not in ready_at and self.is_ready(pod):
                    ready_at[n] = now
            time.sleep(0.25)
        for n in ready_at:
            self.ready_seconds.append(ready_at[n] - requested[n])
        self.started_peak = max(self.started_peak, len(ready_at))
        self.record_pulls()
        if len(ready_at) < self.args.sessions:
            self.errors.append(
                f"wave {index}: only {len(ready_at)} of {self.args.sessions} sessions became ready"
            )
            return

        self.hold(sorted(ready_at))
        self.measure_latencies(sorted(ready_at))
        self.delete_wave(names)

    def hold(self, names: list[str]) -> None:
        started = time.monotonic()
        last_cost = 0.0
        while time.monotonic() - started < self.args.hold:
            self.check_deadline()
            ready = sum(
                1 for p in self.my_pods() if p["metadata"]["name"] in names and self.is_ready(p)
            )
            self.held_min = ready if self.held_min is None else min(self.held_min, ready)
            if time.monotonic() - last_cost >= 10 or last_cost == 0.0:
                last_cost = time.monotonic()
                self.sample_cost(names)
            time.sleep(1.0)
        self.hold_achieved = max(self.hold_achieved, time.monotonic() - started)

    def sample_cost(self, names: list[str]) -> None:
        """Per-session CPU, memory (metrics-server) and ephemeral storage (kubelet stats)."""

        usage: dict[str, dict[str, float | None]] = {n: {} for n in names}
        done = self.k.run(
            "top", "pods", "-n", self.args.namespace, "-l", f"{LABEL}={self.run_id}", "--no-headers"
        )
        if done.returncode == 0:
            for line in done.stdout.splitlines():
                parts = line.split()
                if len(parts) >= 3 and parts[0] in usage:
                    usage[parts[0]]["cpu_millicores"] = parse_cpu_millicores(parts[1])
                    usage[parts[0]]["memory_mib"] = parse_mebibytes(parts[2])
        else:
            self.errors.append(
                "kubectl top unavailable (metrics-server): CPU and memory per session not measured"
            )
        for pod in self.my_pods():
            name, node = pod["metadata"]["name"], (pod.get("spec") or {}).get("nodeName")
            if name not in usage or not node:
                continue
            stats = self.k.run("get", "--raw", f"/api/v1/nodes/{node}/proxy/stats/summary")
            if stats.returncode != 0:
                continue
            try:
                for p in json.loads(stats.stdout).get("pods", []):
                    if p["podRef"]["name"] == name:
                        used = (p.get("ephemeral-storage") or {}).get("usedBytes")
                        usage[name]["storage_mib"] = used / 1048576 if used is not None else None
            except (ValueError, KeyError):
                continue
        self.cost_obs.extend(usage.values())

    def measure_latencies(self, names: list[str]) -> None:
        """Terminal round trips and grading runs, all sessions at once (24.3, 24.4)."""

        def terminal(name: str) -> list[float]:
            proc = subprocess.Popen(
                [
                    "kubectl",
                    "--context",
                    self.args.context,
                    "exec",
                    "-i",
                    "-n",
                    self.args.namespace,
                    name,
                    "-c",
                    "learner",
                    "--",
                    "sh",
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            out: list[float] = []
            try:
                assert proc.stdin is not None and proc.stdout is not None
                for i in range(self.args.keystrokes + 1):
                    mark = f"m{i}x"
                    t0 = time.monotonic()
                    proc.stdin.write(f"echo {mark}\n".encode())
                    proc.stdin.flush()
                    line = proc.stdout.readline()
                    dt = time.monotonic() - t0
                    if mark.encode() not in line:
                        break
                    if i > 0:  # the first exchange warms the connection
                        out.append(dt)
            except OSError:
                pass
            finally:
                proc.kill()
            return out

        def grade(name: str) -> float | None:
            t0 = time.monotonic()
            done = self.k.run(
                "exec",
                "-n",
                self.args.namespace,
                name,
                "-c",
                "learner",
                "--",
                "sh",
                "-c",
                self.args.grade_cmd,
                timeout=GRADING_MAX_S * 4,
            )
            dt = time.monotonic() - t0
            if done.returncode != 0:
                self.errors.append(f"grading check failed on {name}: {done.stderr.strip()[:200]}")
                return None
            return dt

        with ThreadPoolExecutor(max_workers=len(names)) as pool:
            for samples in pool.map(terminal, names):
                self.terminal_seconds.extend(samples)
            for dt in pool.map(grade, names):
                if dt is not None:
                    self.grading_seconds.append(dt)

    def record_pulls(self) -> None:
        """Image pull time from kubelet events, kept apart from Sandbox_Ready_Time (risk R4)."""

        try:
            events = self.k.json(
                "get",
                "events",
                "-n",
                self.args.namespace,
                "--field-selector",
                "involvedObject.kind=Pod",
            )
        except RuntimeError:
            return
        mine = {p["metadata"]["name"] for p in self.my_pods()}
        for ev in events.get("items", []):
            if ev.get("reason") == "Pulled" and ev.get("involvedObject", {}).get("name") in mine:
                seconds = parse_pull_seconds(ev.get("message", ""))
                if seconds is not None:
                    self.pull_seconds.append(seconds)

    def delete_wave(self, names: list[str]) -> None:
        self.k.run(
            "delete",
            "pod",
            *names,
            "-n",
            self.args.namespace,
            "--grace-period=0",
            "--wait=true",
            "--ignore-not-found",
            timeout=120,
        )
        self.created.difference_update(names)

    # -- cleanup (24.7, 24.8)

    def cleanup(self) -> tuple[float, int]:
        """Delete every pod of this run; return (seconds taken, pods remaining)."""

        t0 = time.monotonic()
        remaining = -1
        while time.monotonic() - t0 < CLEANUP_MAX_S + 5:
            try:
                self.k.run(
                    "delete",
                    "pods",
                    "-n",
                    self.args.namespace,
                    "-l",
                    f"{LABEL}={self.run_id}",
                    "--grace-period=0",
                    "--wait=false",
                    "--ignore-not-found",
                    timeout=60,
                )
                remaining = len(self.my_pods())
            except (RuntimeError, subprocess.TimeoutExpired, json.JSONDecodeError):
                remaining = -1
            if remaining == 0:
                break
            time.sleep(1.0)
        return time.monotonic() - t0, remaining

    # -- driver

    def run(self) -> dict[str, Any]:
        sampler = RunnerSampler(self.args.sample_interval)
        started_at = datetime.now(UTC).isoformat()
        status = "completed"
        sampler.start()
        try:
            wave = 0
            while len(self.ready_seconds) < self.args.min_samples and wave < self.args.max_waves:
                self.wave(wave)
                wave += 1
                if self.errors and len(self.ready_seconds) < self.args.sessions * wave:
                    break  # a wave could not start all sessions; more waves would not help
        except Aborted as exc:
            status = "aborted"
            self.errors.append(str(exc))
        except BaseException as exc:
            status = "failed"
            self.errors.append(f"{type(exc).__name__}: {exc}")
        finally:
            cleanup_s, remaining = self.cleanup()
            runner = sampler.stop()

        measurements: dict[str, float | None] = {
            "sandbox_ready_samples": float(len(self.ready_seconds)),
            "sandbox_ready_p95_seconds": nearest_rank_percentile(self.ready_seconds, 95)
            if self.ready_seconds
            else None,
            "sandbox_ready_max_seconds": max(self.ready_seconds, default=None),
            "concurrent_sessions_started": float(self.started_peak),
            "concurrent_sessions_held": float(self.held_min) if self.held_min is not None else None,
            "hold_seconds": self.hold_achieved if self.held_min is not None else None,
            "terminal_p95_seconds": nearest_rank_percentile(self.terminal_seconds, 95)
            if self.terminal_seconds
            else None,
            "grading_max_seconds": max(self.grading_seconds, default=None),
            "cleanup_seconds": cleanup_s if remaining == 0 else None,
        }
        targets = default_targets(
            sessions=self.args.sessions, hold_s=self.args.hold, min_samples=self.args.min_samples
        )
        misses = evaluate_targets(measurements, targets)
        passed = False if status != "completed" else not misses
        return {
            "schema": "edu-it.lab-load-k8s/1",
            "run_id": self.run_id,
            "started_at": started_at,
            "context": self.args.context,
            "namespace": self.args.namespace,
            "image": self.args.image,
            "status": status,
            "passed": passed,
            "sessions_requested": self.args.sessions,
            # Req 24.9: number of Lab_Sessions that were started when 10 could not be.
            "sessions_started": self.started_peak,
            "sandbox_ready_seconds": {
                **summarize(self.ready_seconds),
                "values": self.ready_seconds,
            },
            "image_pull_seconds": {
                **summarize(self.pull_seconds),
                "values": self.pull_seconds,
                "note": "reported separately from sandbox_ready_seconds (risk R4)",
            },
            "terminal_round_trip_seconds": summarize(self.terminal_seconds),
            "grading_seconds": {**summarize(self.grading_seconds), "values": self.grading_seconds},
            "runner": runner,
            "per_session": per_session_cost(self.cost_obs),
            "cleanup": {"seconds": cleanup_s, "remaining_pods": remaining},
            "measurements": measurements,
            "missed_targets": [asdict(m) for m in misses],
            "errors": self.errors,
        }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--context", default=os.environ.get("EDU_IT_KIND_CONTEXT", "kind-edu-it"))
    p.add_argument("--namespace", default="labs")
    p.add_argument("--image", default="busybox:1.36.1")
    p.add_argument("--runtime-class", default=None)
    p.add_argument("--sessions", type=int, default=SESSIONS)
    p.add_argument("--hold", type=float, default=HOLD_SECONDS, help="hold period in seconds")
    p.add_argument("--min-samples", type=int, default=MIN_SAMPLES)
    p.add_argument("--max-waves", type=int, default=5)
    p.add_argument("--keystrokes", type=int, default=20)
    p.add_argument("--grade-cmd", default="test -d /tmp && echo ok")
    p.add_argument("--sample-interval", type=float, default=RESOURCE_SAMPLE_INTERVAL_S)
    p.add_argument("--timeout", type=float, default=900, help="overall limit in seconds")
    p.add_argument("--out", default="load-test.json")
    return p.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.sessions < 1 or args.min_samples < 1:
        print("sessions and min-samples must be positive", file=sys.stderr)
        return 2
    probe = Kubectl(args.context, args.namespace)
    try:
        ok = (
            probe.run("--request-timeout=5s", "get", "--raw", "/readyz", timeout=30).returncode == 0
        )
    except (OSError, subprocess.TimeoutExpired):
        ok = False
    if not ok:
        print(
            f"no reachable cluster for context {args.context}; nothing was measured",
            file=sys.stderr,
        )
        return 2

    def on_signal(signum: int, _frame: object) -> None:
        raise KeyboardInterrupt(f"signal {signum}")

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, on_signal)

    result = LoadTest(args).run()
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2)
    print(f"load test {result['status']}: passed={result['passed']} results in {args.out}")
    for miss in result["missed_targets"]:
        print(
            f"MISSED {miss['target']}: measured {miss['measured']} "
            f"threshold {miss['threshold']} ({miss['kind']})"
        )
    if result["sessions_started"] < args.sessions:
        print(f"only {result['sessions_started']} of {args.sessions} Lab_Sessions were started")
    if result["cleanup"]["remaining_pods"] != 0:
        print(
            f"WARNING: {result['cleanup']['remaining_pods']} pods remain (run {result['run_id']})"
        )
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
