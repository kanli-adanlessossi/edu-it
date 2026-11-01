"""Example tests for the Milestone 1 account routes (api-route-wiring task 5.1).

**Validates: Requirements 2.1, 2.4, 2.5, 2.7, 3.1, 3.5, 3.7, 3.9, 3.10, 4.1, 4.5, 4.6, 4.8, 4.9**
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from http.cookies import SimpleCookie
from itertools import count
from typing import Any

import pytest
from edu_it_api.accounts import AccountServices, build_account_router
from edu_it_api.errors import install_error_handlers
from edu_it_api.identity import SessionUserResolver
from edu_it_api.session_cookie import CookieSettings
from edu_it_application.authenticate import (
    AuthenticateSessionUseCase,
    AuthenticateUseCase,
    SignOutUseCase,
)
from edu_it_application.gdpr import DeleteAccountUseCase, ExportUserDataUseCase
from edu_it_application.resend_verification import ResendVerificationUseCase
from edu_it_application.reset_password import (
    CompletePasswordResetUseCase,
    RequestPasswordResetUseCase,
)
from edu_it_application.sign_up import SignUpUseCase
from edu_it_application.verify_email import VerifyEmailUseCase
from edu_it_infrastructure.composition import Environment
from edu_it_infrastructure.fakes import (
    Console_Mailer,
    In_Memory_CredentialStore,
    In_Memory_ObjectStorage,
    In_Memory_PersonalDataGateway,
    In_Memory_ResetLinkRepository,
    In_Memory_SessionRepository,
    In_Memory_SessionStore,
    In_Memory_SignInRateLimiter,
    In_Memory_UserRepository,
    In_Memory_VerificationLinkRepository,
    Test_Clock,
)
from edu_it_infrastructure.link_builder import Web_LinkBuilder
from fastapi import FastAPI

_PASSWORD = "correct horse battery staple"
_EMAIL = "learner@example.com"


class _Response:
    def __init__(self, status: int, headers: list[tuple[str, str]], body: bytes) -> None:
        self.status_code = status
        self._headers = headers
        self.text = body.decode()

    def json(self) -> Any:
        return json.loads(self.text)

    @property
    def headers(self) -> _Response:
        return self

    def get_list(self, name: str) -> list[str]:
        return [v for k, v in self._headers if k.lower() == name]

    def __contains__(self, name: object) -> bool:
        return any(k.lower() == str(name).lower() for k, _ in self._headers)

    def __getitem__(self, name: str) -> str:
        return next(v for k, v in self._headers if k.lower() == name.lower())


class _Cookies:
    def __init__(self) -> None:
        self.jar: dict[str, str] = {}

    def set(self, name: str, value: str) -> None:
        self.jar[name] = value

    def clear(self) -> None:
        self.jar.clear()


class _Client:
    """Minimal in-process ASGI client with a cookie jar (httpx is not installed)."""

    def __init__(self, app: FastAPI) -> None:
        self._app = app
        self.cookies = _Cookies()

    def get(self, path: str) -> _Response:
        return self._call("GET", path, None)

    def post(self, path: str, json: dict[str, Any] | None = None) -> _Response:
        return self._call("POST", path, json)

    def _call(self, method: str, path: str, body: dict[str, Any] | None) -> _Response:
        raw = b"" if body is None else json.dumps(body).encode()
        headers = [(b"content-type", b"application/json"), (b"accept-language", b"en")]
        if self.cookies.jar:
            cookie = "; ".join(f"{k}={v}" for k, v in self.cookies.jar.items())
            headers.append((b"cookie", cookie.encode()))
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": method,
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": headers,
            "scheme": "http",
            "server": ("test", 80),
            "client": ("test", 1),
            "root_path": "",
        }
        sent: list[dict[str, Any]] = []

        async def run() -> None:
            async def receive() -> dict[str, Any]:
                return {"type": "http.request", "body": raw, "more_body": False}

            async def send(message: dict[str, Any]) -> None:
                sent.append(message)

            await self._app(scope, receive, send)  # type: ignore[arg-type]

        asyncio.run(run())
        start = next(m for m in sent if m["type"] == "http.response.start")
        data = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
        hdrs = [(k.decode(), v.decode()) for k, v in start["headers"]]
        for k, v in hdrs:
            if k.lower() == "set-cookie":
                parsed = SimpleCookie()
                parsed.load(v)
                for name, morsel in parsed.items():
                    if morsel["max-age"] == "0" or morsel.value == "":
                        self.cookies.jar.pop(name, None)
                    else:
                        self.cookies.jar[name] = morsel.value
        return _Response(start["status"], hdrs, data)


class _Harness:
    def __init__(self, gateway: In_Memory_PersonalDataGateway | None = None) -> None:
        clock = Test_Clock(start=datetime(2024, 1, 1, 12, tzinfo=UTC))
        self.mailer = Console_Mailer()
        users = In_Memory_UserRepository()
        credentials = In_Memory_CredentialStore()
        sessions = In_Memory_SessionRepository()
        store = In_Memory_SessionStore(clock)
        links = In_Memory_VerificationLinkRepository()
        reset_links = In_Memory_ResetLinkRepository()
        storage = In_Memory_ObjectStorage()
        self.storage = storage
        self.gateway = gateway or In_Memory_PersonalDataGateway()
        ids, tokens, refs = count(1), count(1), count(1)
        self.cookies = CookieSettings(
            environment=Environment.DEVELOPMENT, domain=None, max_age=timedelta(days=1)
        )
        services = AccountServices(
            resolver=SessionUserResolver(store),
            cookies=self.cookies,
            sign_up=SignUpUseCase(
                users=users,
                credentials=credentials,
                verification_links=links,
                link_builder=Web_LinkBuilder("https://web.test"),
                mailer=self.mailer,
                clock=clock,
                token_factory=lambda: f"verify-{next(tokens)}",
                stable_id_factory=lambda: f"user-{next(ids)}",
                password_hasher=lambda raw: raw,
            ),
            verify_email=VerifyEmailUseCase(users=users, verification_links=links, clock=clock),
            resend=ResendVerificationUseCase(
                users=users,
                verification_links=links,
                link_builder=Web_LinkBuilder("https://web.test"),
                mailer=self.mailer,
                clock=clock,
                token_factory=lambda: f"verify-{next(tokens)}",
            ),
            sign_in=AuthenticateUseCase(
                users=users,
                credentials=credentials,
                sessions=sessions,
                session_store=store,
                rate_limiter=In_Memory_SignInRateLimiter(clock),
                clock=clock,
                session_ref_factory=lambda: f"session-ref-{next(refs)}",
            ),
            sign_out=SignOutUseCase(sessions=sessions, session_store=store, clock=clock),
            authenticate_session=AuthenticateSessionUseCase(session_store=store),
            request_reset=RequestPasswordResetUseCase(
                users=users,
                reset_links=reset_links,
                link_builder=Web_LinkBuilder("https://web.test"),
                mailer=self.mailer,
                clock=clock,
                token_factory=lambda: f"reset-{next(tokens)}",
            ),
            complete_reset=CompletePasswordResetUseCase(
                reset_links=reset_links,
                credentials=credentials,
                sessions=sessions,
                session_store=store,
                clock=clock,
                password_hasher=lambda raw: raw,
                session_ref_factory=lambda: f"reset-ref-{next(refs)}",
            ),
            export_data=ExportUserDataUseCase(personal_data=self.gateway, object_storage=storage),
            object_storage=storage,
            delete_account=DeleteAccountUseCase(
                personal_data=self.gateway,
                sessions=sessions,
                session_store=store,
                clock=clock,
            ),
        )
        app = FastAPI()
        install_error_handlers(app)
        app.include_router(build_account_router(services))
        self.client = _Client(app)

    def token(self) -> str:
        return self.mailer.sent_to(_EMAIL)[-1].headers["X-Verification-Token"]


@pytest.fixture
def h() -> _Harness:
    return _Harness()


def _signed_in(h: _Harness) -> None:
    assert (
        h.client.post("/auth/sign-up", json={"email": _EMAIL, "password": _PASSWORD}).status_code
        == 202
    )
    assert h.client.post("/auth/verify-email", json={"token": h.token()}).status_code == 200


def test_csrf_issues_cookie_and_echoes_existing(h: _Harness) -> None:
    first = h.client.get("/auth/csrf")
    token = first.json()["token"]
    assert len(token) >= 32
    assert "httponly" not in first.headers["set-cookie"].lower()
    again = h.client.get("/auth/csrf")
    assert again.json()["token"] == token
    assert "set-cookie" not in again.headers


def test_sign_up_is_identical_for_known_email_and_rejects_bad_input(h: _Harness) -> None:
    body = {"email": _EMAIL, "password": _PASSWORD}
    first = h.client.post("/auth/sign-up", json=body)
    second = h.client.post("/auth/sign-up", json=body)
    assert (
        (first.status_code, first.json())
        == (second.status_code, second.json())
        == (202, {"accepted": True})
    )
    long_email = h.client.post(
        "/auth/sign-up", json={"email": "a" * 255 + "@x.io", "password": _PASSWORD}
    )
    extra = h.client.post("/auth/sign-up", json={**body, "role": "admin"})
    assert long_email.status_code == extra.status_code == 422
    assert "fields" in long_email.json()
    assert "fields" in extra.json()


def test_verify_resend_and_invalid_link(h: _Harness) -> None:
    bad = h.client.post("/auth/verify-email", json={"token": "nope"})
    assert bad.status_code == 400
    assert bad.json()["offer_resend"] is True
    known = h.client.post("/auth/verify-email/resend", json={"email": _EMAIL})
    unknown = h.client.post("/auth/verify-email/resend", json={"email": "x@example.com"})
    assert known.status_code == unknown.status_code == 202
    assert known.json() == unknown.json() == {"accepted": True}


def test_sign_in_session_and_sign_out(h: _Harness) -> None:
    _signed_in(h)
    old_csrf = h.client.get("/auth/csrf").json()["token"]
    h.client.cookies.set("edu_it_session", "attacker-chosen")
    response = h.client.post("/auth/sign-in", json={"email": _EMAIL, "password": _PASSWORD})
    assert response.status_code == 200
    assert response.json() == {"redirect_to": "/dashboard"}
    cookies = response.headers.get_list("set-cookie")
    session = next(c for c in cookies if c.startswith("edu_it_session="))
    assert "session-ref-1" in session
    assert "httponly" in session.lower()
    assert "attacker-chosen" not in session
    assert "session-ref-1" not in response.text
    csrf = next(c for c in cookies if c.startswith("csrf_token="))
    assert old_csrf not in csrf

    probe = h.client.get("/auth/session")
    assert probe.status_code == 200
    assert probe.json() == {"user_id": "user-1"}

    out = h.client.post("/auth/sign-out")
    assert out.status_code == 204
    assert any(c.startswith("edu_it_session=") for c in out.headers.get_list("set-cookie"))
    h.client.cookies.clear()
    assert h.client.get("/auth/session").status_code == 401
    assert h.client.post("/auth/sign-out").status_code == 204


def test_failed_sign_in_sets_no_cookie_and_unverified_is_403(h: _Harness) -> None:
    h.client.post("/auth/sign-up", json={"email": _EMAIL, "password": _PASSWORD})
    unverified = h.client.post("/auth/sign-in", json={"email": _EMAIL, "password": _PASSWORD})
    assert unverified.status_code == 403
    wrong = h.client.post("/auth/sign-in", json={"email": _EMAIL, "password": "nope-nope-nope"})
    assert wrong.status_code in (401, 403)
    assert "set-cookie" not in unverified.headers
    assert "set-cookie" not in wrong.headers


def test_password_reset_request_is_identical_and_completion_rotates_cookies(h: _Harness) -> None:
    _signed_in(h)
    old = h.client.post("/auth/sign-in", json={"email": _EMAIL, "password": _PASSWORD})
    assert old.status_code == 200
    old_ref = next(c for c in old.headers.get_list("set-cookie") if c.startswith("edu_it_session="))

    known = h.client.post("/auth/password-reset", json={"email": _EMAIL})
    unknown = h.client.post("/auth/password-reset", json={"email": "nobody@example.com"})
    assert known.status_code == unknown.status_code == 202
    assert known.json() == unknown.json() == {"accepted": True}
    token = h.mailer.sent_to(_EMAIL)[-1].headers["X-Reset-Token"]

    new_password = "another long passphrase"
    done = h.client.post(
        "/auth/password-reset/complete", json={"token": token, "new_password": new_password}
    )
    assert done.status_code == 200
    assert done.json() == {"redirect_to": "/dashboard"}
    cookies = done.headers.get_list("set-cookie")
    session = next(c for c in cookies if c.startswith("edu_it_session="))
    assert session != old_ref
    assert "httponly" in session.lower()
    assert any(c.startswith("csrf_token=") for c in cookies)
    assert h.client.get("/auth/session").status_code == 200

    replay = h.client.post(
        "/auth/password-reset/complete", json={"token": token, "new_password": new_password}
    )
    assert replay.status_code == 400
    assert replay.json()["offer_restart"] is True
    assert "set-cookie" not in replay.headers


def test_password_reset_completion_rejects_weak_password_and_keeps_link(h: _Harness) -> None:
    _signed_in(h)
    h.client.post("/auth/password-reset", json={"email": _EMAIL})
    token = h.mailer.sent_to(_EMAIL)[-1].headers["X-Reset-Token"]
    weak = h.client.post(
        "/auth/password-reset/complete", json={"token": token, "new_password": "x"}
    )
    assert weak.status_code == 422
    assert weak.json()["fields"][0]["location"] == "body.new_password"
    assert "set-cookie" not in weak.headers
    ok = h.client.post(
        "/auth/password-reset/complete",
        json={"token": token, "new_password": "a sufficiently long one"},
    )
    assert ok.status_code == 200


def _sign_in(h: _Harness) -> None:
    _signed_in(h)
    assert (
        h.client.post("/auth/sign-in", json={"email": _EMAIL, "password": _PASSWORD}).status_code
        == 200
    )


def test_export_download_and_deletion_flow(h: _Harness) -> None:
    """Validates: Requirements 13.1, 13.2, 13.3, 13.5."""
    assert h.client.get("/account/export/download").status_code == 401
    assert h.client.post("/account/export").status_code == 401
    _sign_in(h)
    missing = h.client.get("/account/export/download")
    assert missing.status_code == 404
    assert missing.json()["type"].endswith("export-not-found")
    exported = h.client.post("/account/export")
    assert exported.status_code == 200
    body = exported.json()
    assert body["download_path"] == "/v1/account/export/download"
    download = h.client.get("/account/export/download")
    assert download.status_code == 200
    assert download.headers["content-type"].startswith("application/json")
    assert download.headers["content-disposition"].startswith("attachment")
    assert len(download.text.encode()) == body["size_bytes"]

    unconfirmed = h.client.post("/account/deletion", json={"confirm": False})
    assert unconfirmed.status_code == 422
    assert h.client.get("/auth/session").status_code == 200
    deleted = h.client.post("/account/deletion", json={"confirm": True})
    assert deleted.status_code == 204
    assert h.client.get("/auth/session").status_code == 401


def test_download_returns_exactly_the_exported_document(h: _Harness) -> None:
    """Validates: Requirements 13.1, 13.3."""
    _sign_in(h)
    h.gateway.add_row("answer", "user-1", {"id": "a1"})
    body = h.client.post("/account/export").json()
    stored = h.storage.get("exports/user-1.json")
    download = h.client.get("/account/export/download")
    assert download.status_code == 200
    assert download.text.encode() == stored.data
    assert body["size_bytes"] == len(stored.data)
    assert "a1" in download.text


def test_another_users_export_is_unreachable(h: _Harness) -> None:
    """Validates: Requirements 13.3."""
    _sign_in(h)
    h.storage.put("exports/user-2.json", b'{"secret": "other"}', content_type="application/json")
    response = h.client.get("/account/export/download")
    assert response.status_code == 404
    assert "other" not in response.text
    h.client.post("/account/export")
    assert "other" not in h.client.get("/account/export/download").text


def test_unconfirmed_deletion_changes_nothing(h: _Harness) -> None:
    """Validates: Requirements 13.4."""
    _sign_in(h)
    h.gateway.add_row("answer", "user-1", {"id": "a1"})
    response = h.client.post("/account/deletion", json={"confirm": False})
    assert response.status_code == 422
    assert "set-cookie" not in response.headers
    assert h.gateway.row_count("answer", "user-1") == 1
    assert h.client.get("/auth/session").status_code == 200


def test_failed_deletion_keeps_all_data(h: _Harness) -> None:
    """Validates: Requirements 13.5."""
    failing = _Harness(In_Memory_PersonalDataGateway(fail_on_table="answer"))
    _sign_in(failing)
    failing.gateway.add_row("answer", "user-1", {"id": "a1"})
    failing.gateway.add_row("quiz_attempt", "user-1", {"id": "q1"})
    response = failing.client.post("/account/deletion", json={"confirm": True})
    assert response.status_code == 500
    assert response.json()["type"].endswith("deletion-incomplete")
    assert failing.gateway.row_count("answer", "user-1") == 1
    assert failing.gateway.row_count("quiz_attempt", "user-1") == 1


def test_unsupported_object_storage_backend_refuses_startup() -> None:
    """Validates: Requirements 13.6."""
    from edu_it_infrastructure.composition import (
        CompositionSettings,
        ProviderSelectionError,
        build_object_storage,
    )

    settings = CompositionSettings.model_construct(object_storage_backend="s3")
    with pytest.raises(ProviderSelectionError, match="s3"):
        build_object_storage(settings)
