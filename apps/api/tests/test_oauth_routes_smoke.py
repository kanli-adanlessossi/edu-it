"""Smoke coverage of the GitHub OAuth routes (api-route-wiring Req 6.1-6.7, 6.10)."""

from __future__ import annotations

import sys
from typing import Any
from urllib.parse import parse_qs, urlsplit

from edu_it_api.serve import compose_served_app
from edu_it_domain.ports.github_oauth import GitHubIdentity
from edu_it_infrastructure.composition import CompositionSettings

_SETTINGS = CompositionSettings.from_env(
    {
        "GITHUB_OAUTH_CLIENT_ID": "id",
        "GITHUB_OAUTH_CLIENT_SECRET": "secret",
        "GITHUB_OAUTH_CALLBACK_URL": "http://localhost:8000/v1/auth/github/callback",
    }
)


def _compose(served: object) -> tuple[Any, Any, Any]:
    harness = sys.modules["served_harness"]
    composed = compose_served_app(_SETTINGS, fakes=True, content_root=harness.CONTENT_ROOT)
    client = composed.adapters.oauth_client
    assert client is not None
    return harness, composed, client


def _start(browser: Any) -> str:
    reply = browser.get("/v1/auth/github/authorize")
    assert reply.status == 302
    query = parse_qs(urlsplit(reply.header("location") or "").query)
    assert query["code_challenge_method"] == ["S256"]
    cookie = reply.header("set-cookie") or ""
    assert "edu_it_oauth_state=" in cookie and "HttpOnly" in cookie
    assert "Path=/v1/auth/github" in cookie and "Max-Age=600" in cookie
    return query["state"][0]


def test_new_identity_signs_in_with_new_session(served: object) -> None:
    harness, composed, client = _compose(served)
    client.register_code("c1", GitHubIdentity("sub-1", "new@example.test", True))
    browser = harness.Browser(composed.app)
    state = _start(browser)
    reply = browser.get("/v1/auth/github/callback", query=f"code=c1&state={state}")
    assert reply.status == 302
    assert (reply.header("location") or "").endswith("/en/dashboard")
    assert harness.SESSION_COOKIE in browser.jar and harness.CSRF_COOKIE in browser.jar
    assert "edu_it_oauth_state" not in browser.jar
    assert browser.get("/v1/auth/session").status == 200


def test_state_mismatch_redirects_to_sign_in_and_deletes_cookie(served: object) -> None:
    harness, composed, _client = _compose(served)
    browser = harness.Browser(composed.app)
    _start(browser)
    reply = browser.get("/v1/auth/github/callback", query="code=c1&state=wrong")
    assert reply.status == 302
    assert "/en/sign-in?error=oauth_rejected" in (reply.header("location") or "")
    assert harness.SESSION_COOKIE not in browser.jar
    assert "edu_it_oauth_state" not in browser.jar


def test_existing_user_pending_then_confirm(served: object) -> None:
    harness, composed, client = _compose(served)
    email = "known@example.test"
    client.register_code("c2", GitHubIdentity("sub-2", email, True))
    other = harness.Browser(composed.app)
    other.post("/v1/auth/sign-up", {"email": email, "password": harness.PASSWORD})
    mailer: Any = composed.adapters.mailer
    token = str(mailer.sent_to(email)[-1].headers["X-Verification-Token"])
    other.post("/v1/auth/verify-email", {"token": token})

    browser = harness.Browser(composed.app)
    state = _start(browser)
    reply = browser.get("/v1/auth/github/callback", query=f"code=c2&state={state}")
    assert (reply.header("location") or "").endswith("/en/auth/github/confirm")
    assert harness.SESSION_COOKIE not in browser.jar
    assert "edu_it_oauth_pending" in browser.jar

    confirmed = browser.post("/v1/auth/github/confirm")
    assert confirmed.status == 200 and confirmed.json() == {"redirect_to": "/dashboard"}
    assert harness.SESSION_COOKIE in browser.jar
    assert "edu_it_oauth_pending" not in browser.jar


def test_decline_returns_204_and_confirm_then_fails(served: object) -> None:
    harness, composed, client = _compose(served)
    email = "other@example.test"
    client.register_code("c3", GitHubIdentity("sub-3", email, True))
    other = harness.Browser(composed.app)
    other.post("/v1/auth/sign-up", {"email": email, "password": harness.PASSWORD})
    mailer: Any = composed.adapters.mailer
    other.post(
        "/v1/auth/verify-email",
        {"token": str(mailer.sent_to(email)[-1].headers["X-Verification-Token"])},
    )
    browser = harness.Browser(composed.app)
    state = _start(browser)
    browser.get("/v1/auth/github/callback", query=f"code=c3&state={state}")
    pending = browser.jar["edu_it_oauth_pending"]
    assert browser.post("/v1/auth/github/decline").status == 204
    browser.jar["edu_it_oauth_pending"] = pending
    failed = browser.post("/v1/auth/github/confirm")
    assert failed.status == 400 and failed.is_problem
