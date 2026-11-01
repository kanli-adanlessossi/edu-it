"""OAuth tests for api-route-wiring task 17.4.

Property 13 (state and PKCE derivation), Property 20 (OAuth sign-in and confirmation rotate the
session and the CSRF token), and example tests over ``Fake_GitHubOAuthClient`` under a
``no_network`` guard.

**Validates: Requirements 1.9, 1.10, 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7, 6.9, 6.10**
"""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import itertools
import re
import socket
import sys
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any, NoReturn
from urllib.parse import parse_qs, quote, urlsplit

import pytest
from edu_it_api.oauth import new_pkce_verifier, pkce_challenge
from edu_it_api.serve import compose_served_app
from edu_it_api.startup import StartupRefused
from edu_it_domain.ports.github_oauth import GitHubIdentity
from edu_it_infrastructure.composition import CompositionSettings
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

_SETTINGS = settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow])
_OAUTH = CompositionSettings.from_env(
    {
        "GITHUB_OAUTH_CLIENT_ID": "id",
        "GITHUB_OAUTH_CLIENT_SECRET": "secret",
        "GITHUB_OAUTH_CALLBACK_URL": "http://localhost:8000/v1/auth/github/callback",
    }
)
_STATE_COOKIE = "edu_it_oauth_state"
_PENDING_COOKIE = "edu_it_oauth_pending"


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail any attempt to resolve a host or open an outbound connection."""

    def _blocked(*_args: object, **_kwargs: object) -> NoReturn:
        raise AssertionError("network access attempted in an OAuth test")

    monkeypatch.setattr(socket, "getaddrinfo", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)
    monkeypatch.setattr(urllib.request, "urlopen", _blocked)


def _harness() -> Any:
    """Load ``served_harness.py`` by path (importlib import mode has no sibling imports)."""
    name = "served_harness"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(f"{name}.py"))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class _World:
    """A Served_App with OAuth on, over the fakes, plus helpers."""

    def __init__(self) -> None:
        self.h = _harness()
        self.composed = compose_served_app(_OAUTH, fakes=True, content_root=self.h.CONTENT_ROOT)
        self.adapters = self.composed.adapters
        self.client: Any = self.adapters.oauth_client
        assert self.client is not None
        self._n = itertools.count(1)

    def browser(self) -> Any:
        return self.h.Browser(self.composed.app)

    def fresh(self, prefix: str) -> str:
        return f"{prefix}{next(self._n)}"

    def counts(self) -> tuple[int, int, int, int]:
        a: Any = self.adapters
        return (
            len(a.users._by_stable_id),
            len(a.oauth_links._by_identity),
            len(a.pending_oauth_links._by_token),
            len(a.sessions._by_ref),
        )

    def start(self, browser: Any) -> str:
        reply = browser.get("/v1/auth/github/authorize")
        assert reply.status == 302
        return parse_qs(urlsplit(reply.header("location") or "").query)["state"][0]

    def callback(self, browser: Any, code: str, state: str) -> Any:
        return browser.get(
            "/v1/auth/github/callback", query=f"code={quote(code)}&state={quote(state)}"
        )

    def existing_user(self, email: str) -> None:
        other = self.browser()
        assert (
            other.post("/v1/auth/sign-up", {"email": email, "password": self.h.PASSWORD}).status
            == 202
        )
        mailer: Any = self.adapters.mailer
        token = str(mailer.sent_to(email)[-1].headers["X-Verification-Token"])
        assert other.post("/v1/auth/verify-email", {"token": token}).status == 200

    def pending_browser(self, email: str) -> Any:
        self.existing_user(email)
        code = self.fresh("pc")
        self.client.register_code(code, GitHubIdentity(self.fresh("sub"), email, True))
        browser = self.browser()
        reply = self.callback(browser, code, self.start(browser))
        assert (reply.header("location") or "").endswith("/en/auth/github/confirm")
        return browser


@pytest.fixture(scope="module")
def world() -> Iterator[_World]:
    yield _World()


# ---- Property 13 -----------------------------------------------------------------------------


# Feature: api-route-wiring, Property 13: OAuth state and PKCE derivation
# **Validates: Requirements 6.1, 6.2**
@_SETTINGS
@given(st.integers(min_value=0, max_value=63))
def test_property_13_verifier_and_challenge_derivation(_seed: int) -> None:
    verifier = new_pkce_verifier()
    assert 43 <= len(verifier) <= 128
    assert re.fullmatch(r"[A-Za-z0-9_-]+", verifier)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    assert pkce_challenge(verifier) == expected
    assert "=" not in expected


# Feature: api-route-wiring, Property 13: OAuth state and PKCE derivation
# **Validates: Requirements 6.1, 6.2**
@_SETTINGS
@given(
    state=st.text(
        alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_", max_size=60
    ),
    mutate=st.booleans(),
)
def test_property_13_state_must_equal_and_verifier_hashes_to_challenge(
    world: _World, state: str, mutate: bool
) -> None:
    browser = world.browser()
    started = browser.get("/v1/auth/github/authorize")
    query = parse_qs(urlsplit(started.header("location") or "").query)
    real, challenge = query["state"][0], query["code_challenge"][0]
    sent = state if mutate and state != real else real
    code = world.fresh("p13")
    world.client.register_code(
        code, GitHubIdentity(world.fresh("sub"), world.fresh("p") + "@e.test", True)
    )
    before = world.counts()
    reply = world.callback(browser, code, sent)
    if sent == real:
        assert (reply.header("location") or "").endswith("/en/dashboard")
        assert world.counts()[0] == before[0] + 1
        # The fake client asserted the exchanged verifier hashes to the issued challenge.
        assert world.client._last_challenge == challenge
        assert world.client.exchanged_codes[-1] == code
    else:
        assert "error=oauth_rejected" in (reply.header("location") or "")
        assert world.counts() == before
        assert code not in world.client.exchanged_codes
    assert _STATE_COOKIE not in browser.jar


# ---- Property 20 -----------------------------------------------------------------------------

_HEX = st.text(alphabet="abcdef0123456789", min_size=8, max_size=40)


# Feature: api-route-wiring, Property 20: Credential-establishing routes rotate the session
# and the CSRF token (OAuth sign-in and confirmation)
# **Validates: Requirements 6.3, 6.6, 6.10**
@_SETTINGS
@given(
    presented_session=st.none() | _HEX,
    presented_csrf=st.none() | _HEX,
    confirm=st.booleans(),
)
def test_property_20_oauth_rotates_session_and_csrf(
    world: _World, presented_session: str | None, presented_csrf: str | None, confirm: bool
) -> None:
    h = world.h
    email = world.fresh("p20-") + "@example.test"
    if confirm:
        browser = world.pending_browser(email)
    else:
        code = world.fresh("p20c")
        world.client.register_code(code, GitHubIdentity(world.fresh("sub"), email, True))
        browser = world.browser()
    browser.jar.pop(h.CSRF_COOKIE, None)
    if presented_session is not None:
        browser.jar[h.SESSION_COOKIE] = presented_session
    if presented_csrf is not None:
        browser.jar[h.CSRF_COOKIE] = presented_csrf

    if confirm:
        # The CSRF gate accepts only server-issued tokens, so the POST presents one of those.
        browser.jar.pop(h.CSRF_COOKIE, None)
        browser.get("/v1/auth/csrf")
        presented_csrf = browser.jar[h.CSRF_COOKIE]
        reply = browser.post("/v1/auth/github/confirm")
        assert reply.status == 200
    else:
        reply = world.callback(browser, code, world.start(browser))
        assert reply.status == 302

    cookies = {
        c.split("=", 1)[0]: c.split(";", 1)[0].split("=", 1)[1]
        for c in reply.header_list("set-cookie")
    }
    assert cookies[h.SESSION_COOKIE] and cookies[h.SESSION_COOKIE] != presented_session
    assert cookies[h.CSRF_COOKIE] and cookies[h.CSRF_COOKIE] != presented_csrf
    assert browser.get("/v1/auth/session").status == 200


# ---- Example tests ---------------------------------------------------------------------------


def test_new_verified_email_signs_in(world: _World) -> None:
    code, email = world.fresh("n"), world.fresh("new") + "@example.test"
    world.client.register_code(code, GitHubIdentity(world.fresh("sub"), email, True))
    browser = world.browser()
    reply = world.callback(browser, code, world.start(browser))
    assert (reply.header("location") or "").endswith("/en/dashboard")
    assert browser.get("/v1/auth/session").status == 200
    assert _STATE_COOKIE not in browser.jar


def test_matching_existing_email_is_pending_without_session(world: _World) -> None:
    before = world.counts()
    browser = world.pending_browser(world.fresh("known") + "@example.test")
    h = world.h
    assert h.SESSION_COOKIE not in browser.jar and _PENDING_COOKIE in browser.jar
    after = world.counts()
    assert after[1] == before[1]  # no link yet
    assert after[2] == before[2] + 1  # one pending link
    assert browser.get("/v1/auth/session").status == 401


def test_unverified_email_links_nothing(world: _World) -> None:
    code = world.fresh("u")
    world.client.register_code(
        code, GitHubIdentity(world.fresh("sub"), world.fresh("unv") + "@example.test", False)
    )
    before = world.counts()
    browser = world.browser()
    reply = world.callback(browser, code, world.start(browser))
    assert "error=oauth_email_unverified" in (reply.header("location") or "")
    assert world.counts() == before
    assert world.h.SESSION_COOKIE not in browser.jar and _STATE_COOKIE not in browser.jar


def test_denied_or_failed_flow_creates_nothing(world: _World) -> None:
    before = world.counts()
    browser = world.browser()
    state = world.start(browser)
    unknown = browser.get(
        "/v1/auth/github/callback", query=f"code=nope&state={quote(state)}"
    )  # exchange fails
    assert "error=oauth_rejected" in (unknown.header("location") or "")
    state = world.start(browser)
    denied = browser.get(
        "/v1/auth/github/callback", query=f"error=access_denied&state={quote(state)}"
    )
    assert "error=oauth_rejected" in (denied.header("location") or "")
    assert world.counts() == before
    assert world.h.SESSION_COOKIE not in browser.jar and _STATE_COOKIE not in browser.jar


def test_confirm_links_and_signs_in(world: _World) -> None:
    browser = world.pending_browser(world.fresh("conf") + "@example.test")
    before_links = world.counts()[1]
    reply = browser.post("/v1/auth/github/confirm")
    assert reply.status == 200 and reply.json() == {"redirect_to": "/dashboard"}
    assert world.counts()[1] == before_links + 1
    assert browser.get("/v1/auth/session").status == 200
    assert _PENDING_COOKIE not in browser.jar


def test_decline_links_nothing(world: _World) -> None:
    browser = world.pending_browser(world.fresh("dec") + "@example.test")
    before_links = world.counts()[1]
    assert browser.post("/v1/auth/github/decline").status == 204
    assert world.counts()[1] == before_links
    assert world.h.SESSION_COOKIE not in browser.jar and _PENDING_COOKIE not in browser.jar


def test_unconfigured_start_omits_routes_with_generic_404(served: object) -> None:
    h = _harness()
    plain = compose_served_app(CompositionSettings(), fakes=True, content_root=h.CONTENT_ROOT)
    assert plain.adapters.oauth_client is None
    browser = h.Browser(plain.app)
    for path in ("/v1/auth/github/authorize", "/v1/auth/github/callback"):
        reply = browser.get(path)
        assert reply.status == 404 and reply.is_problem
    reply = browser.post("/v1/auth/github/confirm")
    assert reply.status == 404 and reply.is_problem


def test_partial_configuration_refuses_to_start() -> None:
    partial = CompositionSettings.from_env({"GITHUB_OAUTH_CLIENT_ID": "id"})
    with pytest.raises(StartupRefused):
        compose_served_app(partial, fakes=True, content_root=_harness().CONTENT_ROOT)
