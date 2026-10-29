"""Unit tests mapping learning errors to RFC 9457 problems (task 18.4, Req 18.6).

Each domain/application learning error is raised from a route on a real app with
the error handlers installed, and the rendered ``application/problem+json``
document is checked for the mapped problem type, status, translated actionable
``detail``, and the absence of the diagnostic message (which is only logged).
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime

import pytest
from edu_it_api.errors.learning import _handle_learning_error
from edu_it_application.learning.errors import (
    InsufficientReviewPoolError,
    InvalidProjectSubmissionError,
    QuizInCooldownError,
    ReviewNotDueError,
)
from edu_it_domain.learning.scoring import ForeignOptionError
from starlette.requests import Request

_SECRET = "internal-stable-id-xyz"
_WHEN = datetime(2030, 1, 2, 3, 4, 5, tzinfo=UTC)


def _get(exc: Exception, locale: str = "en") -> tuple[int, dict[str, object], dict[str, str]]:
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/v1/x",
        "raw_path": b"/v1/x",
        "query_string": b"",
        "headers": [(b"accept-language", locale.encode("latin-1"))],
    }
    response = asyncio.run(_handle_learning_error(Request(scope), exc))
    assert response.media_type == "application/problem+json"
    return response.status_code, json.loads(bytes(response.body)), dict(response.headers)


def test_foreign_option_maps_to_422_problem() -> None:
    status, body, _ = _get(ForeignOptionError(f"option 9 does not belong to {_SECRET}"))
    assert status == 422
    assert body["status"] == 422
    assert str(body["type"]).endswith("/foreign-option")
    assert _SECRET not in str(body)


def test_cooldown_detail_names_retry_instant_and_sets_retry_after() -> None:
    status, body, headers = _get(QuizInCooldownError(_SECRET, _WHEN))
    assert status == 429
    assert str(body["type"]).endswith("/quiz-in-cooldown")
    assert "2030-01-02 03:04" in str(body["detail"])
    assert headers["retry-after"] == "Wed, 02 Jan 2030 03:04:05 GMT"
    assert _SECRET not in str(body)


def test_cooldown_detail_is_translated() -> None:
    _, body, _ = _get(QuizInCooldownError(_SECRET, _WHEN), locale="fr")
    assert "retenter" in str(body["detail"])
    assert body["title"] == "Quiz en période d'attente"


def test_insufficient_pool_maps_to_409() -> None:
    status, body, _ = _get(InsufficientReviewPoolError(_SECRET, 1, 3))
    assert status == 409
    assert str(body["type"]).endswith("/insufficient-review-pool")
    assert _SECRET not in str(body)


def test_review_not_due_names_due_instant() -> None:
    status, body, _ = _get(ReviewNotDueError(_SECRET, _WHEN))
    assert status == 409
    assert "2030-01-02 03:04" in str(body["detail"])


@pytest.mark.parametrize("locale", ["en", "fr"])
def test_invalid_submission_detail_uses_reason(locale: str) -> None:
    exc = InvalidProjectSubmissionError(
        InvalidProjectSubmissionError.MISSING_EVIDENCE, f"log-only {_SECRET}"
    )
    status, body, _ = _get(exc, locale)
    assert status == 422
    assert ("Evidence" if locale == "en" else "preuve") in str(body["detail"])
    assert _SECRET not in str(body)


def test_diagnostic_message_is_logged_not_returned(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="edu_it_api.errors.learning"):
        _, body, _ = _get(ForeignOptionError(f"option does not belong to {_SECRET}"))
    assert _SECRET in caplog.text
    assert _SECRET not in str(body)
