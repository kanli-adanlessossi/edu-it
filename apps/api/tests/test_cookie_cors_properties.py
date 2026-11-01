"""Property tests for cookie attributes and CORS (api-route-wiring task 4.3).

Feature: api-route-wiring, Property 6: Cookie attributes follow configuration
Feature: api-route-wiring, Property 8: CORS follows the allowlist and the route table

**Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.8, 2.9**

Uses Hypothesis (docs/adr/0001-versions.md). Property 6 drives the cookie helpers
over every environment, optional domain, and lifetime. Property 8 builds a bare
FastAPI app with :func:`install_cors` and sends a preflight for every non-WebSocket
``ROUTE_TABLE`` method from an allowed origin and from a disallowed one.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

from edu_it_api.cors import allowed_methods, install_cors
from edu_it_api.csrf import CSRF_COOKIE_NAME
from edu_it_api.csrf_tokens import new_csrf_token
from edu_it_api.route_table import ROUTE_TABLE
from edu_it_api.session_cookie import (
    CookieSettings,
    expire_session_cookie,
    set_csrf_cookie,
    set_session_cookie,
)
from edu_it_api.terminal import SESSION_COOKIE_NAME
from edu_it_infrastructure.composition import Environment
from fastapi import FastAPI
from hypothesis import given, settings
from hypothesis import strategies as st
from starlette.responses import Response

_LABEL = st.from_regex(r"[a-z][a-z0-9]{0,11}", fullmatch=True)
_DOMAINS = st.none() | st.builds(lambda a, b: f"{a}.{b}", _LABEL, _LABEL)
_COOKIE_SETTINGS = st.builds(
    CookieSettings,
    environment=st.sampled_from(list(Environment)),
    domain=_DOMAINS,
    max_age=st.integers(min_value=60, max_value=60 * 60 * 24 * 30).map(
        lambda s: timedelta(seconds=s)
    ),
)
_REF = st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789-_", min_size=8, max_size=64)


def _attributes(response: Response, name: str) -> tuple[str, dict[str, str]]:
    """Return ``(value, lowercase-attribute map)`` of the ``Set-Cookie`` for ``name``."""
    for raw in response.headers.getlist("set-cookie"):
        parts = [p.strip() for p in raw.split(";")]
        key, _, value = parts[0].partition("=")
        if key == name:
            attrs = {}
            for part in parts[1:]:
                k, _, v = part.partition("=")
                attrs[k.lower()] = v
            return value, attrs
    raise AssertionError(f"no Set-Cookie for {name}")


@settings(max_examples=100, deadline=None)
@given(cookie_settings=_COOKIE_SETTINGS, ref=_REF)
def test_cookie_attributes_follow_configuration(cookie_settings: CookieSettings, ref: str) -> None:
    """Feature: api-route-wiring, Property 6: Cookie attributes follow configuration."""
    response = Response()
    token = new_csrf_token()
    set_session_cookie(response, ref, cookie_settings)
    set_csrf_cookie(response, token, cookie_settings)

    secure = cookie_settings.environment.is_production
    max_age = str(int(cookie_settings.max_age.total_seconds()))

    value, session = _attributes(response, SESSION_COOKIE_NAME)
    assert value == ref
    csrf_value, csrf = _attributes(response, CSRF_COOKIE_NAME)
    assert csrf_value == token

    # Session cookie: HttpOnly. csrf_token cookie: readable by scripts.
    assert "httponly" in session
    assert "httponly" not in csrf

    for attrs in (session, csrf):
        assert attrs["samesite"].lower() == "lax"
        assert attrs["path"] == "/"
        assert attrs["max-age"] == max_age
        assert ("secure" in attrs) is secure
        if cookie_settings.domain is None:
            assert "domain" not in attrs
        else:
            assert attrs["domain"] == cookie_settings.domain

    # Expiry keeps the scoping attributes so the browser matches the cookie to delete.
    expired = Response()
    expire_session_cookie(expired, cookie_settings)
    _, gone = _attributes(expired, SESSION_COOKIE_NAME)
    assert gone["max-age"] == "0"
    assert gone["path"] == "/"
    assert gone.get("domain") == cookie_settings.domain


def _options(app: FastAPI, path: str, headers: dict[str, str]) -> tuple[int, dict[str, str]]:
    """Send one in-process ``OPTIONS`` request (the project pins no HTTP client)."""
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "OPTIONS",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
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
    return start["status"], {k.decode().lower(): v.decode() for k, v in start["headers"]}


_ORIGINS = st.lists(
    st.builds(lambda label: f"https://{label}.example.com", _LABEL),
    min_size=1,
    max_size=4,
    unique=True,
)
_PREFLIGHT_ENTRIES = [e for e in ROUTE_TABLE if e.method != "WS"]


@settings(max_examples=100, deadline=None)
@given(origins=_ORIGINS, data=st.data())
def test_cors_follows_allowlist_and_route_table(origins: list[str], data: st.DataObject) -> None:
    """Feature: api-route-wiring, Property 8: CORS follows the allowlist and the route table."""
    app = FastAPI()
    install_cors(app, origins, ROUTE_TABLE)

    entry = data.draw(st.sampled_from(_PREFLIGHT_ENTRIES))
    origin = data.draw(st.sampled_from(origins))

    status, headers = _options(
        app,
        entry.path,
        {
            "Origin": origin,
            "Access-Control-Request-Method": entry.method,
            "Access-Control-Request-Headers": "content-type, x-csrf-token",
        },
    )
    assert status == 200
    assert headers["access-control-allow-origin"] == origin
    assert headers["access-control-allow-credentials"] == "true"
    assert entry.method in headers["access-control-allow-methods"].split(", ")

    # An origin outside the allowlist gets no allow headers.
    _, denied = _options(
        app,
        entry.path,
        {
            "Origin": "https://evil.example.org",
            "Access-Control-Request-Method": entry.method,
        },
    )
    assert "access-control-allow-origin" not in denied


def test_allowed_methods_derive_from_route_table_and_include_put() -> None:
    methods = allowed_methods(ROUTE_TABLE)
    assert methods == ["DELETE", "GET", "OPTIONS", "POST", "PUT"]
    assert "WS" not in methods
