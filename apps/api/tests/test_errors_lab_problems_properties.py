"""Property test: lab errors are translated problem documents without leaks (task 7.2).

Feature: hands-on-labs, Property 30: Lab errors are translated problem documents without leaks

**Validates: Requirements 14.7, 17.3, 25.2, 27.5, 30.1, 30.3, 30.4, 30.5**
"""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from typing import TYPE_CHECKING

from edu_it_api.errors import labs as lab_errors
from edu_it_api.errors.labs import _handle_lab_error
from edu_it_application.labs.errors import (
    ActiveSessionConflictError,
    GradingRejectedError,
    LabFilesRejectedError,
    LabProvisionRateLimitedError,
    LabSessionNotFoundError,
    LabSessionNotReadyError,
)
from edu_it_domain.config import LAB_GRADING_MIN_INTERVAL, LAB_SESSION_TTL
from edu_it_domain.labs.files import FileViolation
from edu_it_domain.labs.models import SessionStatus
from edu_it_domain.labs.session_rules import RejectionReason
from edu_it_domain.ports.sandbox import ProviderUnavailable
from hypothesis import given, settings
from hypothesis import strategies as st
from starlette.requests import Request

if TYPE_CHECKING:
    from edu_it_api.errors.catalog import ProblemType

_PATH = "/v1/lab-sessions/grade"
_MAX_RETRY_SECONDS = int(LAB_SESSION_TTL.total_seconds())
_MIN_RETRY_MS = 1

_markers = st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789", min_size=8, max_size=20).map(
    lambda s: f"LEAK{s}"
)
_lab_ids = st.text(alphabet="abcdefghij-", min_size=3, max_size=12).map(lambda s: f"lab-{s}")
_locales = st.sampled_from(["en", "fr"])
_delays = st.integers(
    min_value=_MIN_RETRY_MS,
    max_value=_MAX_RETRY_SECONDS * 1000,
).map(lambda ms: timedelta(milliseconds=ms))

# (exception, expected ProblemType, expects Retry-After)
_cases = st.one_of(
    st.tuples(_markers, _lab_ids, _lab_ids).map(
        lambda t: (
            ActiveSessionConflictError(
                active_session_id=t[0], active_lab_id=t[1], requested_lab_id=t[2]
            ),
            lab_errors.LAB_SESSION_ACTIVE
            if t[1] != t[2]
            else lab_errors.LAB_SESSION_ACTIVE_SAME_LAB,
            False,
        )
    ),
    st.tuples(_delays).map(
        lambda t: (
            LabProvisionRateLimitedError(t[0]),
            lab_errors.LAB_PROVISION_RATE_LIMITED,
            True,
        )
    ),
    st.tuples(
        st.sampled_from(list(RejectionReason)),
        st.integers(1, int(LAB_GRADING_MIN_INTERVAL.total_seconds() * 1000)).map(
            lambda ms: timedelta(milliseconds=ms)
        ),
    ).map(
        lambda t: (
            GradingRejectedError(t[0], t[1] if t[0] is RejectionReason.THROTTLED else None),
            {
                RejectionReason.THROTTLED: lab_errors.LAB_GRADING_THROTTLED,
                RejectionReason.IN_PROGRESS: lab_errors.LAB_GRADING_IN_PROGRESS,
                RejectionReason.EXPIRED: lab_errors.LAB_SESSION_EXPIRED,
                RejectionReason.NOT_READY: lab_errors.LAB_SESSION_NOT_READY,
            }[t[0]],
            t[0] is RejectionReason.THROTTLED,
        )
    ),
    st.tuples(_markers, st.sampled_from(list(SessionStatus))).map(
        lambda t: (LabSessionNotReadyError(t[0], t[1]), lab_errors.LAB_SESSION_NOT_READY, False)
    ),
    _markers.map(lambda m: (LabSessionNotFoundError(m), lab_errors.LAB_SESSION_NOT_FOUND, False)),
    st.tuples(st.sampled_from(list(FileViolation)), _markers).map(
        lambda t: (LabFilesRejectedError(t[0], t[1]), lab_errors.LAB_FILES_INVALID, False)
    ),
    _markers.map(
        lambda m: (
            ProviderUnavailable(f"docker daemon at /var/run/{m}.sock refused"),
            lab_errors.LAB_SANDBOX_UNAVAILABLE,
            False,
        )
    ),
)


def _render(exc: Exception, locale: str) -> tuple[int, str, dict[str, object], dict[str, str]]:
    scope = {
        "type": "http",
        "method": "POST",
        "path": _PATH,
        "raw_path": _PATH.encode(),
        "query_string": b"",
        "headers": [(b"accept-language", locale.encode("latin-1"))],
    }
    response = asyncio.run(_handle_lab_error(Request(scope), exc))
    assert response.media_type == "application/problem+json"
    raw = bytes(response.body).decode()
    return response.status_code, raw, json.loads(raw), dict(response.headers)


# Feature: hands-on-labs, Property 30: Lab errors are translated problem documents without leaks
@settings(max_examples=150, deadline=None)
@given(case=_cases, locale=_locales)
def test_lab_errors_are_translated_problem_documents_without_leaks(
    case: tuple[Exception, ProblemType, bool], locale: str
) -> None:
    """**Validates: Requirements 14.7, 17.3, 25.2, 27.5, 30.1, 30.3, 30.4, 30.5**"""
    exc, expected, wants_retry_after = case
    status, raw, body, headers = _render(exc, locale)

    # Valid problem document with the right status and type.
    assert status == expected.status
    assert body["status"] == status
    assert str(body["type"]).endswith(f"/{expected.slug}")
    for field in ("type", "title", "status", "detail"):
        assert body.get(field), field

    # Translated in the requested locale.
    assert body["title"] == expected.title[locale]

    # Retry-After on throttling and rate limiting only, in whole seconds.
    if wants_retry_after:
        assert status == 429
        retry_after = headers["retry-after"]
        assert retry_after.isdigit()
        assert int(retry_after) >= 1
        assert retry_after in str(body["detail"])
    else:
        assert "retry-after" not in headers

    # Active-session conflicts name the running lab.
    if isinstance(exc, ActiveSessionConflictError):
        assert exc.active_lab_id in str(body["detail"])

    # No planted marker, internal id, path or stack trace in body or headers.
    haystack = (raw + json.dumps(headers)).lower()
    for leaked in (
        "leak",
        "traceback",
        "docker",
        "/var/run",
        ".sock",
        "session-id",
        "provisioning",
        "file-too-large",
        "too-many-files",
        "name-not-allowed",
        "unsafe-name",
    ):
        assert leaked not in haystack, leaked
    assert _PATH not in str(body["detail"]) + str(body["title"])
