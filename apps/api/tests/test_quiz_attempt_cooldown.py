"""API tests for cooldown rejection on quiz-attempt submission (task 18.7).

A second submission (new Idempotency-Key) inside the cooldown window is
rejected with a translated problem whose ``detail`` states when the quiz is
attemptable again, and no attempt is recorded.

**Validates: Requirements 24.1, 24.2**
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

from edu_it_api.errors.learning import install_learning_error_handlers
from edu_it_domain.config import COOLDOWN_INTERVAL

# Reuse the learning-route harness (tests are collected without a package).
_NAME = "_learning_routes_harness"
if _NAME not in sys.modules:
    _spec = importlib.util.spec_from_file_location(
        _NAME, Path(__file__).with_name("test_learning_routes.py")
    )
    assert _spec is not None and _spec.loader is not None
    _harness = importlib.util.module_from_spec(_spec)
    sys.modules[_NAME] = _harness
    _spec.loader.exec_module(_harness)
_harness = sys.modules[_NAME]
_QUIZ, _T0, _app, _call = _harness._QUIZ, _harness._T0, _harness._app, _harness._call

_PAYLOAD = {"quiz_id": _QUIZ, "selections": {"qn-1": 1}}


def _attempt(app: Any, key: str, headers: dict[str, str] | None = None) -> Any:
    return _call(
        app,
        "POST",
        "/quiz-attempts",
        body=_PAYLOAD,
        headers={"Idempotency-Key": key, **(headers or {})},
    )


def test_submission_during_cooldown_is_rejected_with_attemptable_instant() -> None:
    app = _app()
    install_learning_error_handlers(app)

    status, _ = _attempt(app, "k-1")
    assert status == 200

    status, body = _attempt(app, "k-2")
    assert status == 429
    assert body["type"].endswith("/quiz-in-cooldown")
    assert body["status"] == 429
    expected = (_T0 + COOLDOWN_INTERVAL).strftime("%Y-%m-%d %H:%M")
    assert expected in body["detail"]


def test_cooldown_detail_is_translated_and_no_attempt_recorded() -> None:
    app = _app()
    install_learning_error_handlers(app)
    _, first = _attempt(app, "k-1")

    status, body = _attempt(app, "k-2", {"Accept-Language": "fr"})
    assert status == 429
    assert "retenter" in body["detail"]

    # The rejected key recorded nothing: replaying the first key still yields
    # the original single attempt, and the cooldown still applies afterwards.
    status, replay = _attempt(app, "k-1")
    assert status == 200
    assert replay["replayed"] is True
    assert replay["attempt"]["stable_id"] == first["attempt"]["stable_id"]
