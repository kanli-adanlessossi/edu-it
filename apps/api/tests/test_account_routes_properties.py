"""Property tests for non-disclosure, indistinguishable failures and invalid input.

Covers api-route-wiring task 5.2 (Milestone 1 part): sign-up, resend, verify-email and
sign-in driven through the in-process ASGI client with ``Test_Clock`` and in-memory fakes.
Equal work is asserted by counting hasher and token-factory calls, never by wall-clock time.
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import count
from pathlib import Path
from typing import Any

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
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

# pytest runs with --import-mode=importlib, so sibling test modules are not importable by
# name; load the example-test harness by path to reuse its in-process ASGI client.
_spec = importlib.util.spec_from_file_location(
    "_account_routes_harness", Path(__file__).with_name("test_account_routes.py")
)
assert _spec is not None and _spec.loader is not None
_harness_module = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _harness_module
_spec.loader.exec_module(_harness_module)
_Client: Any = _harness_module._Client

_SETTINGS = settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)

_LOCALS = st.text(alphabet="abcdefghijklmnopqrstuvwxyz", min_size=1, max_size=12)
_PASSWORDS = st.text(alphabet="abcdefghijklmnopqrstuvwxyz ", min_size=12, max_size=40)


def _email(local: str) -> str:
    return f"{local}@example.com"


class _World:
    """One isolated API with counting collaborators and inspectable fakes."""

    def __init__(self) -> None:
        self.clock = Test_Clock(start=datetime(2024, 1, 1, 12, tzinfo=UTC))
        self.mailer = Console_Mailer()
        self.users = In_Memory_UserRepository()
        self.credentials = In_Memory_CredentialStore()
        self.links = In_Memory_VerificationLinkRepository()
        self.reset_links = In_Memory_ResetLinkRepository()
        reset_links = self.reset_links
        self.sessions = In_Memory_SessionRepository()
        self.store = In_Memory_SessionStore(self.clock)
        storage = In_Memory_ObjectStorage()
        self.hash_calls = 0
        self.token_calls = 0
        ids, tokens, refs = count(1), count(1), count(1)

        def hasher(raw: str) -> str:
            self.hash_calls += 1
            return raw

        def token() -> str:
            self.token_calls += 1
            return f"verify-{next(tokens)}"

        def link_builder() -> Web_LinkBuilder:
            return Web_LinkBuilder("https://web.test")

        services = AccountServices(
            resolver=SessionUserResolver(self.store),
            cookies=CookieSettings(
                environment=Environment.DEVELOPMENT, domain=None, max_age=timedelta(days=1)
            ),
            sign_up=SignUpUseCase(
                users=self.users,
                credentials=self.credentials,
                verification_links=self.links,
                link_builder=link_builder(),
                mailer=self.mailer,
                clock=self.clock,
                token_factory=token,
                stable_id_factory=lambda: f"user-{next(ids)}",
                password_hasher=hasher,
            ),
            verify_email=VerifyEmailUseCase(
                users=self.users, verification_links=self.links, clock=self.clock
            ),
            resend=ResendVerificationUseCase(
                users=self.users,
                verification_links=self.links,
                link_builder=link_builder(),
                mailer=self.mailer,
                clock=self.clock,
                token_factory=token,
            ),
            sign_in=AuthenticateUseCase(
                users=self.users,
                credentials=self.credentials,
                sessions=self.sessions,
                session_store=self.store,
                rate_limiter=In_Memory_SignInRateLimiter(self.clock),
                clock=self.clock,
                session_ref_factory=lambda: f"session-ref-{next(refs)}",
            ),
            sign_out=SignOutUseCase(
                sessions=self.sessions, session_store=self.store, clock=self.clock
            ),
            authenticate_session=AuthenticateSessionUseCase(session_store=self.store),
            request_reset=RequestPasswordResetUseCase(
                users=self.users,
                reset_links=reset_links,
                link_builder=Web_LinkBuilder("https://web.test"),
                mailer=self.mailer,
                clock=self.clock,
                token_factory=token,
            ),
            complete_reset=CompletePasswordResetUseCase(
                reset_links=reset_links,
                credentials=self.credentials,
                sessions=self.sessions,
                session_store=self.store,
                clock=self.clock,
                password_hasher=lambda raw: raw,
                session_ref_factory=lambda: f"reset-ref-{next(refs)}",
            ),
            export_data=ExportUserDataUseCase(
                personal_data=In_Memory_PersonalDataGateway(), object_storage=storage
            ),
            object_storage=storage,
            delete_account=DeleteAccountUseCase(
                personal_data=In_Memory_PersonalDataGateway(),
                sessions=self.sessions,
                session_store=self.store,
                clock=self.clock,
            ),
        )
        app = FastAPI()
        install_error_handlers(app)
        app.include_router(build_account_router(services))
        self.client = _Client(app)

    def post(self, path: str, body: dict[str, Any]) -> Any:
        return self.client.post(path, json=body)

    def sign_up(self, email: str, password: str) -> Any:
        return self.post("/auth/sign-up", {"email": email, "password": password})

    def verify(self, email: str) -> None:
        mail = self.mailer.sent_to(email)[-1]
        response = self.post("/auth/verify-email", {"token": mail.headers["X-Verification-Token"]})
        assert response.status_code == 200

    def snapshot(self) -> dict[str, Any]:
        """Everything a rejected request must leave untouched."""
        return {
            "users": dict(self.users._by_stable_id),
            "credentials": dict(self.credentials._by_user),
            "links": dict(self.links._by_token),
            "reset_links": dict(self.reset_links._by_token),
            "sessions": dict(self.sessions._by_ref),
            "mails": len(self.mailer.sent),
        }


@dataclass(frozen=True)
class _Outcome:
    status: int
    body: Any
    set_cookie: bool


def _outcome(response: Any) -> _Outcome:
    return _Outcome(response.status_code, response.json(), "set-cookie" in response.headers)


# Feature: api-route-wiring, Property 3: Accepted responses never disclose account existence
# **Validates: Requirements 3.2, 3.7, 3.11**
@_SETTINGS
@given(
    locals_=st.lists(_LOCALS, min_size=3, max_size=3, unique=True),
    password=_PASSWORDS,
)
def test_property_3_sign_up_and_resend_do_not_disclose_existence(
    locals_: list[str], password: str
) -> None:
    world = _World()
    new, unverified, verified = (_email(name) for name in locals_)
    assert world.sign_up(unverified, password).status_code == 202
    assert world.sign_up(verified, password).status_code == 202
    world.verify(verified)

    # Sign-up: identical response and the same single hash for every class of email.
    sign_up: dict[str, tuple[_Outcome, int]] = {}
    for label, email in (("new", new), ("unverified", unverified), ("verified", verified)):
        before = world.hash_calls
        sign_up[label] = (_outcome(world.sign_up(email, password)), world.hash_calls - before)
    assert all(o == next(iter(sign_up.values()))[0] for o, _ in sign_up.values())
    assert sign_up["new"][0].status == 202
    assert {calls for _, calls in sign_up.values()} == {1}

    # Resend: unknown ("new"), unverified and verified emails mint the same number of tokens.
    resend: dict[str, tuple[_Outcome, int]] = {}
    for label, email in (
        ("unknown", _email("zz" + locals_[0])),
        ("unverified", unverified),
        ("verified", verified),
    ):
        before = world.token_calls
        response = world.post("/auth/verify-email/resend", {"email": email})
        resend[label] = (_outcome(response), world.token_calls - before)
    assert all(o == next(iter(resend.values()))[0] for o, _ in resend.values())
    assert resend["unknown"][0].status == 202
    assert {calls for _, calls in resend.values()} == {1}


# Feature: api-route-wiring, Property 4: Sign-in failures are indistinguishable
# **Validates: Requirements 4.2**
@_SETTINGS
@given(local=_LOCALS, real=_PASSWORDS, attempt=_PASSWORDS)
def test_property_4_sign_in_failures_are_indistinguishable(
    local: str, real: str, attempt: str
) -> None:
    assume(real != attempt)
    email = _email(local)
    known = _World()
    known.sign_up(email, real)
    known.verify(email)
    unknown = _World()

    wrong_password = _outcome(known.post("/auth/sign-in", {"email": email, "password": attempt}))
    no_account = _outcome(unknown.post("/auth/sign-in", {"email": email, "password": attempt}))

    assert wrong_password == no_account
    assert wrong_password.status == 401
    assert not wrong_password.set_cookie
    assert wrong_password.body["type"].endswith("/invalid-credentials")


_BAD_EMAILS = st.one_of(
    st.text(alphabet="abcdefghijklmnopqrstuvwxyz", min_size=1, max_size=20),  # no "@"
    st.integers(min_value=255, max_value=300).map(lambda n: "a" * (n - len("@x.io")) + "@x.io"),
)
_BAD_PASSWORDS = st.text(alphabet="abc", min_size=0, max_size=3)


def _assert_fields_shape(body: Any, expected_locations: set[str]) -> None:
    assert body["type"].endswith("/validation-error")
    fields = body["fields"]
    assert all(set(entry) == {"location", "reason"} for entry in fields)
    assert {entry["location"] for entry in fields} == expected_locations


# Feature: api-route-wiring, Property 12: Invalid input and invalid links leave state unchanged
# **Validates: Requirements 3.3, 3.6, 3.10**
@_SETTINGS
@given(
    local=_LOCALS,
    password=_PASSWORDS,
    bad_email=st.none() | _BAD_EMAILS,
    bad_password=st.none() | _BAD_PASSWORDS,
    extra=st.none() | st.sampled_from(["role", "is_admin", "verified", "locale_x"]),
)
def test_property_12_invalid_sign_up_input_leaves_state_unchanged(
    local: str,
    password: str,
    bad_email: str | None,
    bad_password: str | None,
    extra: str | None,
) -> None:
    assume(bad_email is not None or bad_password is not None or extra is not None)
    world = _World()
    # A pre-existing account proves rejected requests also leave real data untouched.
    world.sign_up(_email("existing" + local), password)
    before = world.snapshot()

    body: dict[str, Any] = {
        "email": _email(local) if bad_email is None else bad_email,
        "password": password if bad_password is None else bad_password,
    }
    if extra is not None:
        body[extra] = "x"
    response = world.sign_up(body["email"], body["password"]) if extra is None else None
    if response is None:
        response = world.post("/auth/sign-up", body)

    assert response.status_code == 422
    if extra is not None:
        # Request validation rejects the unknown member before the use case runs.
        _assert_fields_shape(response.json(), {f"body.{extra}"})
    else:
        expected = set()
        if bad_email is not None:
            expected.add("body.email")
        if bad_password is not None:
            expected.add("body.password")
        _assert_fields_shape(response.json(), expected)
    assert world.snapshot() == before


# Feature: api-route-wiring, Property 12: Invalid input and invalid links leave state unchanged
# **Validates: Requirements 3.3, 3.6, 3.10**
@_SETTINGS
@given(
    local=_LOCALS,
    password=_PASSWORDS,
    kind=st.sampled_from(["unknown", "used", "expired"]),
    junk=st.text(min_size=1, max_size=40),
)
def test_property_12_invalid_verification_links_leave_state_unchanged(
    local: str, password: str, kind: str, junk: str
) -> None:
    world = _World()
    email = _email(local)
    world.sign_up(email, password)
    token = world.mailer.sent_to(email)[-1].headers["X-Verification-Token"]
    if kind == "unknown":
        assume(junk != token)
        token = junk
    elif kind == "used":
        assert world.post("/auth/verify-email", {"token": token}).status_code == 200
    else:
        world.clock.advance(timedelta(days=30))
    before = world.snapshot()

    response = world.post("/auth/verify-email", {"token": token})

    assert response.status_code == 400
    body = response.json()
    assert body["type"].endswith("/verification-link-invalid")
    assert body["offer_resend"] is True
    assert world.snapshot() == before
    if kind == "expired":
        assert next(iter(world.users._by_stable_id.values())).status == "unverified"

    # An unknown extra member on the link route is rejected with the same `fields` shape.
    extra = world.post("/auth/verify-email", {"token": token, "role": "admin"})
    assert extra.status_code == 422
    _assert_fields_shape(extra.json(), {"body.role"})
    assert world.snapshot() == before


# Feature: api-route-wiring, Property 3: Accepted responses never disclose account existence
# (reset request part, counting token factory)
# **Validates: Requirements 5.1, 5.6**
@_SETTINGS
@given(
    locals_=st.lists(_LOCALS, min_size=3, max_size=3, unique=True),
    password=_PASSWORDS,
)
def test_property_3_reset_request_does_not_disclose_existence(
    locals_: list[str], password: str
) -> None:
    world = _World()
    unverified, verified = _email(locals_[0]), _email(locals_[1])
    unknown = _email("zz" + locals_[2])
    assert world.sign_up(unverified, password).status_code == 202
    assert world.sign_up(verified, password).status_code == 202
    world.verify(verified)

    results: list[tuple[_Outcome, int]] = []
    for email in (unknown, unverified, verified):
        before = world.token_calls
        response = world.post("/auth/password-reset", {"email": email})
        results.append((_outcome(response), world.token_calls - before))

    assert all(outcome == results[0][0] for outcome, _ in results)
    assert results[0][0].status == 202
    assert results[0][0].body == {"accepted": True}
    assert not results[0][0].set_cookie
    # Equal work: exactly one token minted for every class of email (Req 5.6).
    assert {calls for _, calls in results} == {1}
    # The only observable difference is the side effect: no reset link for the unknown email.
    assert len(world.reset_links._by_token) == 2


# Feature: api-route-wiring, Property 12: Invalid input and invalid links leave state unchanged
# (reset tokens and weak passwords)
# **Validates: Requirements 5.4, 5.5**
@_SETTINGS
@given(
    local=_LOCALS,
    password=_PASSWORDS,
    new_password=_PASSWORDS,
    kind=st.sampled_from(["unknown", "used", "expired", "weak", "extra"]),
    junk=st.text(min_size=1, max_size=40),
    weak=_BAD_PASSWORDS,
)
def test_property_12_invalid_reset_input_leaves_state_unchanged(
    local: str, password: str, new_password: str, kind: str, junk: str, weak: str
) -> None:
    world = _World()
    email = _email(local)
    world.sign_up(email, password)
    world.verify(email)
    assert world.post("/auth/password-reset", {"email": email}).status_code == 202
    token = world.mailer.sent_to(email)[-1].headers["X-Reset-Token"]
    body: dict[str, Any] = {"token": token, "new_password": new_password}
    if kind == "unknown":
        assume(junk != token)
        body["token"] = junk
    elif kind == "used":
        assert world.post("/auth/password-reset/complete", body).status_code == 200
        world.client.cookies.clear()
    elif kind == "expired":
        world.clock.advance(timedelta(days=30))
    elif kind == "weak":
        body["new_password"] = weak
    else:
        body["role"] = "admin"
    before = world.snapshot()

    response = world.post("/auth/password-reset/complete", body)

    assert "set-cookie" not in response.headers
    assert world.snapshot() == before
    if kind in ("unknown", "used", "expired"):
        assert response.status_code == 400
        assert response.json()["offer_restart"] is True
    elif kind == "weak":
        assert response.status_code == 422
        _assert_fields_shape(response.json(), {"body.new_password"})
    else:
        assert response.status_code == 422
        _assert_fields_shape(response.json(), {"body.role"})
    if kind != "used":
        (user,) = world.users._by_stable_id.values()
        assert world.credentials.verify_password(user.stable_id, password)
