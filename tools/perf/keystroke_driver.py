"""One terminal keystroke round trip against the real stack (Req 31.2).

Uses a long-lived ready session (created untimed on first use). Per sample it opens the
terminal WebSocket, waits for the shell prompt to go quiet, then times
"key frame sent" -> "echoed byte received" through the real Terminal_Gateway and the Docker
sandbox pty. The driver is a WebSocket client, not a browser, so browser event handling and
rendering are outside the measured interval. The last stdout line is ``{"seconds": <float>}``.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import stack  # noqa: E402

# Not a character of the shell prompt (hex host name, lower-case paths), so only the echo matches.
KEY = b"Q"
CLEAR_LINE = b"\x15"  # Ctrl-U: discard the typed character so lines never accumulate


def main() -> int:
    learner, session_id, _created = stack.ensure_ready_session("keystroke")
    with stack.ws_connect(learner, session_id) as ws:
        prompt = stack.drain_until_quiet(ws)
        if KEY in prompt:
            print("prompt contains the probe key; refusing to time a false echo", file=sys.stderr)
            return 1
        started = time.perf_counter()
        ws.send(KEY)
        stack.read_until(ws, KEY, timeout_s=5.0)
        elapsed = time.perf_counter() - started
        ws.send(CLEAR_LINE)
        stack.drain_until_quiet(ws, quiet_s=0.1, max_s=1.0)
    stack.emit_seconds(elapsed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
