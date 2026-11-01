"""One shell Check run against the real stack (Req 31.3).

Uses a long-lived ready session of the shell seed lab. On first use (untimed) the reference
solution is typed into the real terminal so every Check passes; that terminal stays open
until the run of that sample has ended (see ``apply_reference_solution``). Per sample the
driver waits for the grading rate limit (untimed), then times ``POST .../grading-runs``
("Check my work") until the run leaves ``running``. A run that does not end ``passed`` is
an error, never a sample. The last stdout line is ``{"seconds": <float>}``.
"""

from __future__ import annotations

import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import stack

SOLUTION = (
    b"mkdir -p /workspace/team/docs /workspace/team/scripts && "
    b"chgrp devs /workspace/team && chmod g+s /workspace/team && "
    b"chmod o-w /workspace/team/shared-notes.txt; echo SOLVED_$?\n"
)


def apply_reference_solution(learner: stack.Learner, session_id: str) -> Any:
    """Type the reference solution; return the terminal, still open.

    The caller closes it only after the run has left ``running`` (task 14.4). Closing it
    makes the API run the provider's terminal-cleanup ``exec`` in the sandbox; started
    together with the worker's freeze ``exec`` it made the freeze overrun
    ``LAB_GRADING_FREEZE_BOUND`` and the run end ``could-not-complete``. A browser learner
    also keeps the terminal open while "Check my work" runs.
    """
    ws = stack.ws_connect(learner, session_id)
    try:
        stack.drain_until_quiet(ws)
        ws.send(SOLUTION)
        stack.read_until(ws, b"SOLVED_0", timeout_s=20.0)
    except BaseException:
        ws.close()
        raise
    return ws


def wait_for_grading_slot(learner: stack.Learner, session_id: str) -> None:
    status, data = stack.api("GET", f"/lab-sessions/{session_id}", learner)
    if status != 200:
        raise RuntimeError(f"session read failed: {status} {data}")
    available = data.get("grading_available_at")
    if available:
        wait = (
            datetime.fromisoformat(available) - datetime.fromisoformat(data["server_now"])
        ).total_seconds()
        if wait > 0:
            time.sleep(wait + 0.25)


def main() -> int:
    learner, session_id, created = stack.ensure_ready_session("check")
    terminal = apply_reference_solution(learner, session_id) if created else None
    try:
        return _timed_check(learner, session_id)
    finally:
        if terminal is not None:
            terminal.close()


def _timed_check(learner: stack.Learner, session_id: str) -> int:
    wait_for_grading_slot(learner, session_id)

    started = time.perf_counter()
    status, run = stack.api(
        "POST",
        f"/lab-sessions/{session_id}/grading-runs",
        learner,
        idempotency_key=str(uuid.uuid4()),
    )
    if status != 202:
        print(f"grading request failed: {status} {run}", file=sys.stderr)
        return 1
    run_id = run["stable_id"]
    while True:
        status, run = stack.api("GET", f"/lab-sessions/{session_id}/grading-runs/{run_id}", learner)
        if status != 200:
            print(f"run read failed: {status} {run}", file=sys.stderr)
            return 1
        if run["status"] != "running":
            break
        if time.perf_counter() - started > 60:
            print("run still running after 60 s", file=sys.stderr)
            return 1
        time.sleep(0.025)
    elapsed = time.perf_counter() - started
    if run["status"] != "passed":
        print(f"run ended {run['status']}: {run['results']}", file=sys.stderr)
        return 1
    stack.emit_seconds(elapsed, checks=len(run["results"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
