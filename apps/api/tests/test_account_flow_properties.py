"""Property and example tests for the sign-up flow, secret hygiene and session rotation.

Covers api-route-wiring task 5.3: Property 2 (sign-up, verification and sign-in compose),
Property 9 (failures and logs never leak secrets or set sessions), Property 20 (sign-in part:
the session and the CSRF token rotate), plus example tests for mail failure (502, user stays
unverified), unverified sign-in (403) and the rate-limit boundary driven by ``Test_Clock``.

**Validates: Requirements 2.10, 3.1, 3.4, 3.5, 3.8, 3.9, 4.1, 4.3, 4.4, 4.8, 4.9, 7.3, 16.6**
"""

from __future__ import annotations

import importlib.util
import io
import logging
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any

from edu_it_domain.config import SIGN_IN_RATE_LIMIT_THRESHOLD, SIGN_IN_RATE_LIMIT_WINDOW
from edu_it_domain.ports.repository import USER_STATUS_UNVERIFIED, USER_STATUS_VERIFIED
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

# pytest runs with --import-mode=importlib: load the sibling harness by path.
_spec = importlib.util.spec_from_file_location(
    "_account_properties_harness", Path(__file__).with_name("test_account_routes_properties.py")
)
assert _spec is not None and _spec.loader is not None
_harness = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _harness
_spec.loader.exec_module(_harness)
_World: Any = _harness._World
_LOCALS = _harness._LOCALS
_PASSWORDS = _harness._PASSWORDS
_email = _harness._email

_SETTINGS = settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)

# Distinctive prefix so a generated password cannot collide with ordinary log words.
_SECRET_PASSWORDS = _PASSWORDS.map(lambda s: "Zq9#" + s)

_SESSION_COOKIE = "edu_it_session"
_CSRF_COOKIE = "csrf_token"


def _cookie_value(response: Any, name: str) -> str:
    header = next(c for c in response.headers.get_list("set-cookie") if c.startswith(f"{name}="))
    return header.split(";", 1)[0].split("=", 1)[1]


def _verified(world: Any, email: str, password: str) -> None:
    assert world.sign_up(email, password).status_code == 202
    world.verify(email)


# Feature: api-route-wiring, Property 2: Sign-up, verification, and sign-in compose
# **Validates: Requirements 3.1, 3.5, 3.9, 4.1, 7.3**
@_SETTINGS
@given(local=_LOCALS, password=_PASSWORDS)
def test_property_2_sign_up_verify_sign_in_compose(local: str, password: str) -> None:
    world = _World()
    email = _email(local)

    assert world.sign_up(email, password).status_code == 202
    (user,) = world.users._by_stable_id.values()
    assert user.status == USER_STATUS_UNVERIFIED
    assert world.credentials.verify_password(user.stable_id, password)  # stored at sign-up (7.3)

    world.verify(email)
    assert next(iter(world.users._by_stable_id.values())).status == USER_STATUS_VERIFIED

    response = world.post("/auth/sign-in", {"email": email, "password": password})
    assert response.status_code == 200
    assert response.json() == {"redirect_to": "/dashboard"}
    assert _cookie_value(response, _SESSION_COOKIE)

    probe = world.client.get("/auth/session")
    assert probe.status_code == 200
    assert probe.json() == {"user_id": user.stable_id}
    # Exactly one account exists: sign-up created a single learner record, nothing else.
    assert len(world.users._by_stable_id) == 1


class _Boom(io.StringIO):
    """A mail stream that fails with an injected marker, simulating a delivery failure."""

    def __init__(self, marker: str) -> None:
        super().__init__()
        self._marker = marker

    def write(self, _text: str) -> int:
        raise RuntimeError(self._marker)


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(
            record.getMessage() + " " + " ".join(f"{k}={v}" for k, v in record.__dict__.items())
        )
        if record.exc_info and record.exc_info[1] is not None:
            self.lines.append(repr(record.exc_info[1]))


# Feature: api-route-wiring, Property 9: Failures and logs never leak secrets or set sessions
# **Validates: Requirements 2.10, 4.8, 16.6**
@_SETTINGS
@given(
    local=_LOCALS,
    password=_SECRET_PASSWORDS,
    attempt=_SECRET_PASSWORDS,
    marker=st.text(alphabet="XYZQ0123456789", min_size=10, max_size=16).map(lambda s: "BOOM-" + s),
    kind=st.sampled_from(["wrong_password", "unverified", "mail_failure", "bad_link"]),
)
def test_property_9_failures_and_logs_never_leak(
    local: str, password: str, attempt: str, marker: str, kind: str
) -> None:
    assume(password != attempt)
    world = _World()
    email = _email(local)
    capture = _Capture()
    root = logging.getLogger()
    previous_level = root.level
    root.addHandler(capture)
    root.setLevel(logging.DEBUG)
    try:
        if kind == "wrong_password":
            _verified(world, email, password)
            response = world.post("/auth/sign-in", {"email": email, "password": attempt})
        elif kind == "unverified":
            world.sign_up(email, password)
            response = world.post("/auth/sign-in", {"email": email, "password": password})
        elif kind == "mail_failure":
            world.mailer._stream = _Boom(marker)
            response = world.sign_up(email, password)
        else:
            world.sign_up(email, password)
            response = world.post("/auth/verify-email", {"token": attempt})
    finally:
        root.removeHandler(capture)
        root.setLevel(previous_level)

    assert response.status_code in (400, 401, 403, 502)
    assert "set-cookie" not in response.headers  # no Session_Cookie on any failure

    secrets = [password, attempt, "session-ref"]
    secrets += list(world.credentials._by_user.values())  # the stored hash
    secrets += [str(ref) for ref in world.sessions._by_ref]
    for secret in secrets:
        for haystack in (response.text, *capture.lines):
            assert secret not in haystack
    # Requirement 16.6 keeps provider diagnostics (the injected marker) in the logs by design,
    # so the marker is asserted absent from the response only.
    assert marker not in response.text


# Feature: api-route-wiring, Property 20: Credential-establishing routes rotate the session
# and the CSRF token (sign-in part)
# **Validates: Requirements 4.9**
@_SETTINGS
@given(
    local=_LOCALS,
    password=_PASSWORDS,
    presented=st.sampled_from(["absent", "valid", "revoked", "random"]),
    junk=st.text(alphabet="abcdef0123456789", min_size=8, max_size=40),
    presented_csrf=st.none() | st.text(alphabet="abcdef0123456789", min_size=8, max_size=40),
)
def test_property_20_sign_in_rotates_session_and_csrf(
    local: str, password: str, presented: str, junk: str, presented_csrf: str | None
) -> None:
    world = _World()
    email = _email(local)
    _verified(world, email, password)

    old_ref: str | None = None
    if presented in ("valid", "revoked"):
        first = world.post("/auth/sign-in", {"email": email, "password": password})
        old_ref = _cookie_value(first, _SESSION_COOKIE)
        if presented == "revoked":
            assert world.client.post("/auth/sign-out").status_code == 204
            world.client.cookies.set(_SESSION_COOKIE, old_ref)
    elif presented == "random":
        old_ref = junk
        world.client.cookies.set(_SESSION_COOKIE, junk)
    world.client.cookies.jar.pop(_CSRF_COOKIE, None)
    if presented_csrf is not None:
        world.client.cookies.set(_CSRF_COOKIE, presented_csrf)

    response = world.post("/auth/sign-in", {"email": email, "password": password})

    assert response.status_code == 200
    new_ref = _cookie_value(response, _SESSION_COOKIE)
    assert new_ref != old_ref
    new_csrf = _cookie_value(response, _CSRF_COOKIE)
    assert new_csrf and new_csrf != presented_csrf
    (user,) = world.users._by_stable_id.values()
    assert world.client.get("/auth/session").json() == {"user_id": user.stable_id}


# ---- Example tests ---------------------------------------------------------------------------


def test_mail_failure_is_502_and_keeps_user_unverified() -> None:
    world = _World()
    world.mailer._stream = _Boom("provider-secret-detail")
    response = world.sign_up("learner@example.com", "correct horse battery staple")

    assert response.status_code == 502
    assert response.json()["type"].endswith("/mail-delivery-failed")
    assert "provider-secret-detail" not in response.text
    (user,) = world.users._by_stable_id.values()
    assert user.status == USER_STATUS_UNVERIFIED


def test_unverified_sign_in_is_403_without_session() -> None:
    world = _World()
    world.sign_up("learner@example.com", "correct horse battery staple")
    response = world.post(
        "/auth/sign-in",
        {"email": "learner@example.com", "password": "correct horse battery staple"},
    )
    assert response.status_code == 403
    assert "set-cookie" not in response.headers


def test_rate_limit_boundary_returns_429_with_retry_after_and_reopens() -> None:
    world = _World()
    email, password = "learner@example.com", "correct horse battery staple"
    _verified(world, email, password)
    wrong = {"email": email, "password": "wrong wrong wrong wrong"}

    # Up to and including the threshold the attempts are still evaluated (401).
    for _ in range(SIGN_IN_RATE_LIMIT_THRESHOLD + 1):
        assert world.post("/auth/sign-in", wrong).status_code == 401

    # The next attempt is refused before credentials are evaluated, even with the right ones.
    blocked = world.post("/auth/sign-in", {"email": email, "password": password})
    assert blocked.status_code == 429
    assert blocked.headers["retry-after"] == str(int(SIGN_IN_RATE_LIMIT_WINDOW.total_seconds()))
    assert "set-cookie" not in blocked.headers

    # Just before the window elapses it is still closed; once it elapses it reopens.
    world.clock.advance(SIGN_IN_RATE_LIMIT_WINDOW - timedelta(seconds=1))
    assert world.post("/auth/sign-in", {"email": email, "password": password}).status_code == 429
    world.clock.advance(timedelta(seconds=1))
    assert world.post("/auth/sign-in", {"email": email, "password": password}).status_code == 200


# Feature: api-route-wiring, Property 20: Credential-establishing routes rotate the session
# and the CSRF token (reset completion part)
# **Validates: Requirements 5.3**
@_SETTINGS
@given(
    local=_LOCALS,
    password=_PASSWORDS,
    new_password=_PASSWORDS,
    other_sessions=st.integers(min_value=0, max_value=2),
    presented_csrf=st.none() | st.text(alphabet="abcdef0123456789", min_size=8, max_size=40),
)
def test_property_20_reset_completion_rotates_session_and_csrf(
    local: str, password: str, new_password: str, other_sessions: int, presented_csrf: str | None
) -> None:
    world = _World()
    email = _email(local)
    _verified(world, email, password)
    (user,) = world.users._by_stable_id.values()

    old_refs: list[str] = []
    for _ in range(other_sessions + 1):
        first = world.post("/auth/sign-in", {"email": email, "password": password})
        old_refs.append(_cookie_value(first, _SESSION_COOKIE))
    assert world.post("/auth/password-reset", {"email": email}).status_code == 202
    token = world.mailer.sent_to(email)[-1].headers["X-Reset-Token"]
    world.client.cookies.jar.pop(_CSRF_COOKIE, None)
    if presented_csrf is not None:
        world.client.cookies.set(_CSRF_COOKIE, presented_csrf)

    response = world.post(
        "/auth/password-reset/complete", {"token": token, "new_password": new_password}
    )

    assert response.status_code == 200
    new_ref = _cookie_value(response, _SESSION_COOKIE)
    assert new_ref not in old_refs
    new_csrf = _cookie_value(response, _CSRF_COOKIE)
    assert new_csrf and new_csrf != presented_csrf
    assert world.client.get("/auth/session").json() == {"user_id": user.stable_id}
    # Every other Session no longer authenticates; only the new one does.
    for old in old_refs:
        world.client.cookies.set(_SESSION_COOKIE, old)
        assert world.client.get("/auth/session").status_code == 401
    world.client.cookies.set(_SESSION_COOKIE, new_ref)
    assert world.client.get("/auth/session").status_code == 200
    assert world.credentials.verify_password(user.stable_id, new_password)


def test_reset_mail_failure_is_502_and_changes_no_password() -> None:
    world = _World()
    email, password = "learner@example.com", "correct horse battery staple"
    _verified(world, email, password)
    (user,) = world.users._by_stable_id.values()
    world.mailer._stream = _Boom("provider-secret-detail")

    response = world.post("/auth/password-reset", {"email": email})

    assert response.status_code == 502
    assert response.json()["type"].endswith("/mail-delivery-failed")
    assert "provider-secret-detail" not in response.text
    assert "set-cookie" not in response.headers
    assert world.credentials.verify_password(user.stable_id, password)
