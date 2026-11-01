"""Lab performance harness (Req 31).

Measures start-to-ready, terminal keystroke round trip and shell-Check run
time by timing user-supplied commands, applies the protocol in ``protocol.py``
and writes a JSON results file consumed by the phase report.

Each measurement is a command line that performs ONE sample and exits 0 when
it succeeded; wall-clock time of the command is the sample. Example:

    python tools/perf/lab_perf.py \
        --start-cmd "python scripts/start_lab.py" \
        --keystroke-cmd "python scripts/keystroke_rtt.py" \
        --check-cmd "python scripts/run_shell_check.py" \
        --out perf-results.json

Exit code: 0 when the final attempt meets every target, 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shlex
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import protocol


def _run(cmd: str, args: list[str] | None = None) -> str:
    try:
        out = subprocess.run(args or shlex.split(cmd), capture_output=True,
                             text=True, timeout=30)
        return (out.stdout or out.stderr).strip()
    except Exception as exc:
        return f"unavailable ({exc.__class__.__name__})"


def _memory() -> dict:
    try:
        import psutil  # type: ignore

        vm = psutil.virtual_memory()
        return {"total_bytes": vm.total, "available_bytes": vm.available,
                "cpu_percent": psutil.cpu_percent(interval=1.0)}
    except ImportError:
        pass
    if os.name == "nt":
        raw = _run("", ["powershell", "-NoProfile", "-Command",
                        "(Get-CimInstance Win32_OperatingSystem | "
                        "ForEach-Object { \"$($_.TotalVisibleMemorySize) "
                        "$($_.FreePhysicalMemory)\" })"
                        ])
        try:
            total, free = (int(x) * 1024 for x in raw.split())
            return {"total_bytes": total, "available_bytes": free, "cpu_percent": None}
        except ValueError:
            pass
    return {"total_bytes": None, "available_bytes": None, "cpu_percent": None}


def machine_setup() -> dict:
    mem = _memory()
    return {
        "cpu": platform.processor() or platform.machine(),
        "cpu_count": os.cpu_count(),
        "ram_bytes": mem["total_bytes"],
        "os": f"{platform.system()} {platform.release()} {platform.version()}",
        "docker_version": _run("docker version --format {{.Server.Version}}"),
        "python": sys.version.split()[0],
        "pre_run_snapshot": {
            "timestamp": datetime.now(UTC).isoformat(),
            "cpu_percent": mem["cpu_percent"],
            "memory_available_bytes": mem["available_bytes"],
            "memory_total_bytes": mem["total_bytes"],
        },
        "idle_machine_required": True,
    }


def reported_seconds(stdout: str) -> float | None:
    """The driver-reported sample: the last stdout line of the form ``{"seconds": <float>}``.

    A driver times only the interval the requirement defines (for example the start request
    to status ``ready``) and excludes its own process start-up and untimed set-up. When no
    such line is present the wall-clock time of the command is the sample instead.
    """
    for line in reversed(stdout.strip().splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            value = json.loads(line).get("seconds")
        except (ValueError, AttributeError):
            continue
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
            return float(value)
    return None


def time_command(cmd: str) -> float:
    start = time.perf_counter()
    proc = subprocess.run(shlex.split(cmd), capture_output=True, text=True)
    elapsed = time.perf_counter() - start
    if proc.returncode != 0:
        raise RuntimeError(
            f"measurement command failed ({proc.returncode}): {cmd}\n"
            f"{(proc.stdout or '')[-500:]}{(proc.stderr or '')[-1500:]}"
        )
    reported = reported_seconds(proc.stdout or "")
    return reported if reported is not None else elapsed


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--start-cmd", required=True)
    ap.add_argument("--keystroke-cmd", required=True)
    ap.add_argument("--check-cmd", required=True)
    ap.add_argument("--out", default="perf-results.json")
    args = ap.parse_args(argv)

    cmds = {protocol.START_TO_READY: args.start_cmd,
            protocol.KEYSTROKE: args.keystroke_cmd,
            protocol.SHELL_CHECK: args.check_cmd}
    methods = {t: f"wall-clock of command: {c}" for t, c in cmds.items()}

    machine = machine_setup()
    print("Machine setup:", json.dumps(machine, indent=2))

    def collect(target: str, n: int) -> list[float]:
        return [time_command(cmds[target]) for _ in range(n)]

    outcome = protocol.run_protocol(collect, methods)
    data = protocol.outcome_to_dict(outcome, machine)
    Path(args.out).write_text(json.dumps(data, indent=2), encoding="utf-8")

    for a in outcome.attempts:
        for name, r in a.results.items():
            print(f"attempt {a.number} {name}: n={r.counted_count} "
                  f"p95={r.p95} max={r.max_value} limit={r.limit} met={r.met}")
    print("RESULT:", "PASS" if outcome.passed else "FAIL", "->", args.out)
    return 0 if outcome.passed else 1


if __name__ == "__main__":
    sys.exit(main())
