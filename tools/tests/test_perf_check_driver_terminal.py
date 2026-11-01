"""Ordering tests for ``tools/perf/check_driver.py`` (api-route-wiring task 14.4).

On a freshly created session the driver types the reference solution into the real terminal.
Closing that terminal makes the API run the Docker provider's terminal-cleanup ``exec`` in the
sandbox. When that ``exec`` starts within a few milliseconds of the worker's freeze ``exec``
(``kill -s STOP -1``), the freeze call took about 1.05 s on the Real_Stack, past
``LAB_GRADING_FREEZE_BOUND`` (1 s), so the run failed closed with every Check
``could_not_complete``. The driver must therefore not close the terminal between "Check my
work" and the end of the run: the terminal stays open (as in the browser) until the run has
left ``running``. The stack calls are replaced by recorders; no network is used.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "perf"))
import check_driver
import stack

LEARNER = stack.Learner(user_id="u", ref="r", csrf="c")
SID = "s-1"
RUN_ID = "run-1"


class _Terminal:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    def send(self, data: bytes) -> None:
        self._events.append("terminal-send")

    def close(self) -> None:
        self._events.append("terminal-close")

    def __enter__(self) -> _Terminal:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _wire(monkeypatch: pytest.MonkeyPatch, *, created: bool, final: str = "passed") -> list[str]:
    events: list[str] = []
    polls = {"n": 0}

    def ensure_ready_session(name: str) -> tuple[stack.Learner, str, bool]:
        return LEARNER, SID, created

    def ws_connect(learner: stack.Learner, session_id: str) -> _Terminal:
        events.append("terminal-open")
        return _Terminal(events)

    def api(method: str, path: str, learner: stack.Learner, body: Any = None, **kw: Any):
        if method == "POST" and path.endswith("/grading-runs"):
            events.append("grading-post")
            return 202, {"stable_id": RUN_ID, "status": "running", "results": []}
        if path.endswith(f"/grading-runs/{RUN_ID}"):
            polls["n"] += 1
            status = "running" if polls["n"] < 3 else final
            if status != "running":
                events.append("run-ended")
            return 200, {"stable_id": RUN_ID, "status": status, "results": [{}] * 4}
        return 200, {"status": "ready", "grading_available_at": None, "server_now": "x"}

    monkeypatch.setattr(stack, "ensure_ready_session", ensure_ready_session)
    monkeypatch.setattr(stack, "ws_connect", ws_connect)
    monkeypatch.setattr(stack, "api", api)
    monkeypatch.setattr(stack, "drain_until_quiet", lambda ws, **kw: b"")
    monkeypatch.setattr(stack, "read_until", lambda ws, needle, **kw: needle)
    monkeypatch.setattr(stack, "emit_seconds", lambda s, **kw: events.append("emit"))
    monkeypatch.setattr(check_driver.time, "sleep", lambda s: None)
    return events


def test_fresh_session_keeps_the_terminal_open_until_the_run_ends(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events = _wire(monkeypatch, created=True)
    assert check_driver.main() == 0
    assert events.index("terminal-send") < events.index("grading-post")
    # No terminal teardown may run between the request and the end of the run.
    assert events.index("grading-post") < events.index("run-ended") < events.index(
        "terminal-close"
    )
    assert events.count("terminal-close") == 1


def test_terminal_is_closed_even_when_the_run_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    events = _wire(monkeypatch, created=True, final="could-not-complete")
    assert check_driver.main() == 1
    assert events.count("terminal-close") == 1
    assert events.index("run-ended") < events.index("terminal-close")


def test_reused_session_opens_no_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    events = _wire(monkeypatch, created=False)
    assert check_driver.main() == 0
    assert "terminal-open" not in events and "terminal-close" not in events
    assert events[-1] == "emit"
