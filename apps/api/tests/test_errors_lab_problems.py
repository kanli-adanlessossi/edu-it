"""Unit tests mapping lab errors to RFC 9457 problems (task 7.1).

Each lab application error is rendered through the lab error handler; the
``application/problem+json`` body is checked for the mapped type, status,
translated text, ``Retry-After`` where required, and the absence of the
log-only diagnostic.
"""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta

import pytest
from edu_it_api.errors.labs import _handle_lab_error
from edu_it_application.labs.errors import (
    ActiveSessionConflictError,
    GradingRejectedError,
    LabFilesRejectedError,
    LabProvisionRateLimitedError,
    LabSessionNotFoundError,
    LabSessionNotReadyError,
)
from edu_it_domain.labs.files import FileViolation
from edu_it_domain.labs.models import SessionStatus
from edu_it_domain.labs.session_rules import RejectionReason
from edu_it_domain.ports.sandbox import ProviderUnavailable
from starlette.requests import Request

_SECRET = "internal-session-id-xyz"


def _get(exc: Exception, locale: str = "en") -> tuple[int, dict[str, object], dict[str, str]]:
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/v1/lab-sessions",
        "raw_path": b"/v1/lab-sessions",
        "query_string": b"",
        "headers": [(b"accept-language", locale.encode("latin-1"))],
    }
    response = asyncio.run(_handle_lab_error(Request(scope), exc))
    assert response.media_type == "application/problem+json"
    return response.status_code, json.loads(bytes(response.body)), dict(response.headers)


@pytest.mark.parametrize(
    ("exc", "status", "slug"),
    [
        (
            ActiveSessionConflictError(
                active_session_id=_SECRET, active_lab_id="lab-a", requested_lab_id="lab-b"
            ),
            409,
            "lab-session-active",
        ),
        (
            ActiveSessionConflictError(
                active_session_id=_SECRET, active_lab_id="lab-a", requested_lab_id="lab-a"
            ),
            409,
            "lab-session-active",
        ),
        (LabProvisionRateLimitedError(timedelta(seconds=12.2)), 429, "lab-provision-rate-limited"),
        (
            GradingRejectedError(RejectionReason.THROTTLED, timedelta(seconds=4)),
            429,
            "lab-grading-throttled",
        ),
        (GradingRejectedError(RejectionReason.IN_PROGRESS), 409, "lab-grading-in-progress"),
        (GradingRejectedError(RejectionReason.EXPIRED), 409, "lab-session-expired"),
        (GradingRejectedError(RejectionReason.NOT_READY), 409, "lab-session-not-ready"),
        (
            LabSessionNotReadyError(_SECRET, SessionStatus.PROVISIONING),
            409,
            "lab-session-not-ready",
        ),
        (LabSessionNotFoundError(_SECRET), 404, "lab-session-not-found"),
        (LabFilesRejectedError(FileViolation.FILE_TOO_LARGE, _SECRET), 422, "lab-files-invalid"),
        (ProviderUnavailable(f"docker at {_SECRET} down"), 503, "lab-sandbox-unavailable"),
    ],
)
@pytest.mark.parametrize("locale", ["en", "fr"])
def test_lab_errors_map_to_problem_types(
    exc: Exception, status: int, slug: str, locale: str
) -> None:
    got_status, body, _ = _get(exc, locale)
    assert got_status == status
    assert body["status"] == status
    assert str(body["type"]).endswith(f"/{slug}")
    assert body["title"]
    assert body["detail"]
    assert _SECRET not in str(body)


def test_throttles_set_retry_after_in_whole_seconds() -> None:
    _, body, headers = _get(LabProvisionRateLimitedError(timedelta(seconds=12.2)))
    assert headers["retry-after"] == "13"
    assert "13" in str(body["detail"])
    _, _, headers = _get(GradingRejectedError(RejectionReason.THROTTLED, timedelta(seconds=4)))
    assert headers["retry-after"] == "4"


def test_active_session_detail_names_running_lab() -> None:
    _, body, _ = _get(
        ActiveSessionConflictError(
            active_session_id=_SECRET, active_lab_id="lab-a", requested_lab_id="lab-b"
        )
    )
    assert "lab-a" in str(body["detail"])


def test_fr_text_is_translated() -> None:
    _, body, _ = _get(LabSessionNotFoundError(_SECRET), locale="fr")
    assert body["title"] == "Session de lab introuvable"
