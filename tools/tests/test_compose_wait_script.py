"""Unit tests for the aggregate readiness wait script (task 10, Req 3.4-3.6).

The pure aggregate *decision* is covered by the Property 35 property test; these
example-based tests cover the wait script's polling loop and exit behavior
(``deploy/compose/wait_for_ready.py``): that it polls each service's
Readiness_Endpoint, passes only when all three become ready within the window,
and fails naming the timed-out service when one never does — all with injected
clock/sleep/probe seams so no real time elapses and no live stack is needed.

The script lives under ``deploy/compose/`` (invoked as ``python
deploy/compose/wait_for_ready.py`` inside the workspace venv), which is not on
the pytest import path, so it is loaded here by file path via importlib.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "deploy" / "compose" / "wait_for_ready.py"


def _load_script() -> ModuleType:
    """Load the wait script module from its file path.

    The module is registered in ``sys.modules`` before execution so that its
    ``@dataclass(slots=True)`` definitions can resolve their own module during
    class creation (dataclasses look the owning module up by name).
    """
    module_name = "edu_it_compose_wait_for_ready"
    spec = importlib.util.spec_from_file_location(module_name, SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


_mod = _load_script()


class _FakeClock:
    """A monotonic clock whose ``sleep`` advances the recorded time, no real wait."""

    def __init__(self) -> None:
        self.t = 0.0

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds


def _config(timeout: float = 120.0, poll: float = 2.0) -> object:
    return _mod._Config(
        base_urls={"web": "http://web", "api": "http://api", "worker": "http://worker"},
        timeout_seconds=timeout,
        poll_interval_seconds=poll,
    )


def test_passes_when_all_three_ready_immediately() -> None:
    """All three ready on the first poll → pass, nothing timed out."""
    clock = _FakeClock()
    passed, timed_out = _mod.wait_for_compose_ready(
        _config(),
        probe=lambda _url: True,
        now=clock.now,
        sleep=clock.sleep,
    )
    assert passed is True
    assert timed_out == ()


def test_fails_and_names_the_single_timed_out_service() -> None:
    """One service never becomes ready → fail, naming exactly that service (Req 3.5)."""
    clock = _FakeClock()

    def probe(url: str) -> bool:
        # web and api are ready; worker never is.
        return url != "http://worker"

    passed, timed_out = _mod.wait_for_compose_ready(
        _config(),
        probe=probe,
        now=clock.now,
        sleep=clock.sleep,
    )
    assert passed is False
    assert timed_out == ("worker",)


def test_fails_naming_all_services_that_time_out() -> None:
    """When several services never become ready, all are named (Req 3.5, 3.6)."""
    clock = _FakeClock()

    def probe(url: str) -> bool:
        return url == "http://api"  # only api becomes ready

    passed, timed_out = _mod.wait_for_compose_ready(
        _config(),
        probe=probe,
        now=clock.now,
        sleep=clock.sleep,
    )
    assert passed is False
    # Reported in the canonical web, api, worker order.
    assert timed_out == ("web", "worker")


def test_passes_when_a_service_becomes_ready_before_the_deadline() -> None:
    """A service that turns ready after a few polls still passes within the window."""
    clock = _FakeClock()
    # worker reports not-ready for its first two polls, then ready.
    worker_calls = {"n": 0}

    def probe(url: str) -> bool:
        if url != "http://worker":
            return True
        worker_calls["n"] += 1
        return worker_calls["n"] > 2

    passed, timed_out = _mod.wait_for_compose_ready(
        _config(timeout=120.0, poll=2.0),
        probe=probe,
        now=clock.now,
        sleep=clock.sleep,
    )
    assert passed is True
    assert timed_out == ()


def test_main_returns_nonzero_on_timeout() -> None:
    """main() exits non-zero when the aggregate check fails (Req 3.5)."""
    # Patch the module's default probe to a never-ready one and run a short window
    # so the loop terminates quickly under the real monotonic clock.
    original: Callable[[str], bool] = _mod._http_ready
    try:
        _mod._http_ready = lambda _url: False  # type: ignore[attr-defined]
        code = _mod.main(["--timeout", "0", "--poll-interval", "0"])
    finally:
        _mod._http_ready = original  # type: ignore[attr-defined]
    assert code == 1


def test_main_returns_zero_when_all_ready() -> None:
    """main() exits zero when every service reports ready (Req 3.6)."""
    original: Callable[[str], bool] = _mod._http_ready
    try:
        _mod._http_ready = lambda _url: True  # type: ignore[attr-defined]
        code = _mod.main(["--timeout", "0", "--poll-interval", "0"])
    finally:
        _mod._http_ready = original  # type: ignore[attr-defined]
    assert code == 0
