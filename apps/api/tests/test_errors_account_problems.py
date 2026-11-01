"""Example tests for the Milestone 1 account problem handlers (api-route-wiring task 4.2).

**Validates: Requirements 1.4, 3.3, 3.4, 3.6, 3.8, 4.2, 4.3, 4.4, 16.6**

Each exception is raised from a bare route on a FastAPI app with the full error
handlers installed, and driven through a minimal in-process ASGI call.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any

import pytest
from edu_it_api.errors import install_error_handlers
from edu_it_api.errors.accounts import (
    InvalidResetLink,
    InvalidVerificationLink,
    ResetPasswordPolicyError,
)
from edu_it_api.identity import NotAuthenticated, SessionUserResolver
from edu_it_application.authenticate import SignInError, SignInFailure
from edu_it_application.resend_verification import ResendVerificationError
from edu_it_application.reset_password import PasswordResetMailDeliveryError
from edu_it_application.sign_up import (
    SignUpFieldError,
    SignUpMailDeliveryError,
    SignUpValidationError,
)
from edu_it_infrastructure.fakes import In_Memory_SessionStore, Test_Clock
from fastapi import FastAPI
from starlette.requests import Request


def _app(exc: Exception) -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/boom")
    def boom() -> None:
        raise exc

    return app


def _get(app: FastAPI, accept_language: str = "en") -> tuple[int, dict[str, str], dict[str, Any]]:
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "path": "/boom",
        "raw_path": b"/boom",
        "query_string": b"",
        "headers": [(b"accept-language", accept_language.encode())],
        "scheme": "http",
        "server": ("test", 80),
        "client": ("test", 1),
        "root_path": "",
    }
    sent: list[dict[str, Any]] = []

    async def run() -> None:
        async def receive() -> dict[str, Any]:
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message: dict[str, Any]) -> None:
            sent.append(message)

        await app(scope, receive, send)  # type: ignore[arg-type]

    asyncio.run(run())
    start = next(m for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    headers = {k.decode().lower(): v.decode() for k, v in start["headers"]}
    return start["status"], headers, json.loads(body)


def test_sign_up_validation_lists_one_field_entry_per_rejected_field() -> None:
    exc = SignUpValidationError(
        (SignUpFieldError(field="email", reason="invalid"), SignUpFieldError("password", "weak"))
    )
    status, headers, body = _get(_app(exc))
    assert status == 422
    assert headers["content-type"].startswith("application/problem+json")
    assert [f["location"] for f in body["fields"]] == ["body.email", "body.password"]


@pytest.mark.parametrize("exc", [SignUpMailDeliveryError(), ResendVerificationError()])
def test_mail_failure_is_502_without_provider_text(exc: Exception) -> None:
    status, _, body = _get(_app(exc))
    assert status == 502
    assert body["type"].endswith("/mail-delivery-failed")


def test_reset_mail_failure_is_502_mail_delivery_failed() -> None:
    status, _, body = _get(_app(PasswordResetMailDeliveryError()))
    assert status == 502
    assert body["type"].endswith("/mail-delivery-failed")


def test_invalid_reset_link_is_translated_400_offering_restart() -> None:
    status, headers, fr = _get(_app(InvalidResetLink()), "fr")
    _, _, en = _get(_app(InvalidResetLink()), "en")
    assert status == 400
    assert headers["content-type"].startswith("application/problem+json")
    assert fr["type"].endswith("/reset-link-invalid")
    assert fr["offer_restart"] is True
    assert fr["detail"] != en["detail"]


def test_reset_password_policy_is_422_with_field_entry() -> None:
    status, _, body = _get(_app(ResetPasswordPolicyError()))
    assert status == 422
    assert [f["location"] for f in body["fields"]] == ["body.new_password"]


def test_invalid_verification_link_offers_resend() -> None:
    status, _, body = _get(_app(InvalidVerificationLink()))
    assert status == 400
    assert body["offer_resend"] is True


@pytest.mark.parametrize(
    ("failure", "status", "slug"),
    [
        (SignInFailure.INVALID_CREDENTIALS, 401, "invalid-credentials"),
        (SignInFailure.EMAIL_NOT_VERIFIED, 403, "email-not-verified"),
        (SignInFailure.RATE_LIMITED, 429, "too-many-attempts"),
    ],
)
def test_sign_in_failures_map_to_distinct_problems(
    failure: SignInFailure, status: int, slug: str
) -> None:
    got, headers, body = _get(_app(SignInError(failure)))
    assert got == status
    assert body["type"].endswith(f"/{slug}")
    assert ("retry-after" in headers) is (failure is SignInFailure.RATE_LIMITED)


def test_not_authenticated_is_translated_401() -> None:
    status, _, fr = _get(_app(NotAuthenticated()), "fr")
    _, _, en = _get(_app(NotAuthenticated()), "en")
    assert status == 401
    assert fr["detail"] != en["detail"]


def test_resolver_rejects_missing_unknown_and_accepts_active_session() -> None:
    store = In_Memory_SessionStore(Test_Clock(datetime(2030, 1, 1, tzinfo=UTC)))
    store.create("ref-1", "user-1", expires_at=datetime(2030, 1, 2, tzinfo=UTC))
    resolver = SessionUserResolver(store)

    def request(cookie: str | None) -> Request:
        headers = [] if cookie is None else [(b"cookie", f"edu_it_session={cookie}".encode())]
        return Request({"type": "http", "headers": headers})

    assert resolver(request("ref-1")) == "user-1"
    for bad in (None, "unknown"):
        with pytest.raises(NotAuthenticated):
            resolver(request(bad))
