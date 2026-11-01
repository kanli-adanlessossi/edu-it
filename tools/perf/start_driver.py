"""One start-to-ready sample against the real stack (Req 31.1).

Mints a fresh learner (untimed; the per-learner provisioning rate limit is 5 per 10 minutes),
then times ``POST /v1/lab-sessions`` -> first read of status ``ready``. Afterwards the session
is ended and the driver waits for the sandbox to be torn down (untimed) so the next sample
starts on a quiet machine. The last stdout line is ``{"seconds": <float>}``.
"""

from __future__ import annotations

import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import stack


def main() -> int:
    learner = stack.mint_learner()
    key = str(uuid.uuid4())

    started = time.perf_counter()
    status, data = stack.api(
        "POST", "/lab-sessions", learner, {"lab_id": stack.LAB_ID}, idempotency_key=key
    )
    if status != 201:
        print(f"start failed: {status} {data}", file=sys.stderr)
        return 1
    session_id = data["stable_id"]
    stack.wait_ready(learner, session_id)
    elapsed = time.perf_counter() - started

    stack.end_session(learner, session_id)
    deadline = time.perf_counter() + 30
    while time.perf_counter() < deadline:
        _, current = stack.api("GET", f"/lab-sessions/{session_id}", learner)
        if current and current["status"] == "ended":
            break
        time.sleep(0.1)
    time.sleep(1.0)  # let the sandbox removal settle before the next sample
    stack.emit_seconds(elapsed, session=session_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
