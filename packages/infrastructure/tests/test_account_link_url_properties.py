"""Property test for the verification link URL (api-route-wiring, task 3.3).

Property 10: Account link URLs round trip. For any token and locale, the
verification URL parses to the configured web base host, the path
``/{locale}/verify-email``, and a ``token`` query value equal to the original
token, contains no whitespace, and appears on a single line of a mail whose
language matches the locale. The reset URL (``/{locale}/reset-password``) and the
reset mail are covered the same way.

Validates: Requirements 8.1, 8.2, 8.4, 8.5

Uses Hypothesis, the pinned Python property-based testing library
(docs/adr/0001-versions.md).
"""

from __future__ import annotations

from datetime import UTC, datetime
from itertools import count
from urllib.parse import parse_qs, urlsplit

from edu_it_application.resend_verification import (
    ResendVerificationRequest,
    ResendVerificationUseCase,
)
from edu_it_application.reset_password import (
    RequestPasswordResetRequest,
    RequestPasswordResetUseCase,
)
from edu_it_application.sign_up import SignUpRequest, SignUpUseCase
from edu_it_domain.ports.repository import USER_STATUS_UNVERIFIED, UserRecord
from edu_it_infrastructure.fakes import (
    Console_Mailer,
    In_Memory_CredentialStore,
    In_Memory_ResetLinkRepository,
    In_Memory_UserRepository,
    In_Memory_VerificationLinkRepository,
    Test_Clock,
)
from edu_it_infrastructure.link_builder import Web_LinkBuilder
from hypothesis import given, settings
from hypothesis import strategies as st

_T0 = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
_BASE = "https://web.test"
_EMAIL = "learner@example.com"
_PASSWORD = "a-valid-password-123"

_locales = st.sampled_from(["fr", "en"])
# Arbitrary tokens, including whitespace, reserved URL characters, and non-ASCII
# text; surrogates are excluded because they cannot be encoded as UTF-8.
_tokens = st.text(alphabet=st.characters(blacklist_categories=["Cs"]), min_size=1, max_size=64)
# Optional trailing slash on the configured base must not change the URL.
_bases = st.sampled_from([_BASE, _BASE + "/"])


def _assert_url_round_trips(url: str, token: str, locale: str, page: str = "verify-email") -> None:
    assert not any(character.isspace() for character in url)
    parts = urlsplit(url)
    assert f"{parts.scheme}://{parts.netloc}" == _BASE
    assert parts.path == f"/{locale}/{page}"
    assert parse_qs(parts.query, keep_blank_values=True) == {"token": [token]}


def _assert_mail_carries_url(body: str, url: str, locale: str) -> None:
    # The URL sits on a single line, and the body language matches the locale.
    assert "\n" not in body
    assert "\r" not in body
    assert url in body.split(" ")
    assert body.startswith("Bonjour" if locale == "fr" else "Hello")


@given(token=_tokens, locale=_locales, base=_bases)
@settings(max_examples=200)
def test_property_10_verification_url_round_trips(token: str, locale: str, base: str) -> None:
    """Feature: api-route-wiring, Property 10: Account link URLs round trip

    The verification URL parses back to the web base host, the locale path, and
    the original token, with no whitespace.
    """
    url = Web_LinkBuilder(base).verification_url(token, locale)

    _assert_url_round_trips(url, token, locale)


@given(token=_tokens, locale=_locales)
@settings(max_examples=100)
def test_property_10_sign_up_mail_carries_url_on_one_line(token: str, locale: str) -> None:
    """Feature: api-route-wiring, Property 10: Account link URLs round trip

    The sign-up mail body carries the verification URL on a single line, in the
    language matching the locale.
    """
    mailer = Console_Mailer()
    ids = (f"user-{n}" for n in count())
    use_case = SignUpUseCase(
        users=In_Memory_UserRepository(),
        credentials=In_Memory_CredentialStore(),
        verification_links=In_Memory_VerificationLinkRepository(),
        link_builder=Web_LinkBuilder(_BASE),
        mailer=mailer,
        clock=Test_Clock(start=_T0),
        token_factory=lambda: token,
        stable_id_factory=lambda: next(ids),
        password_hasher=lambda raw: f"hash:{raw}",
    )

    use_case.execute(SignUpRequest(email=_EMAIL, password=_PASSWORD, locale=locale))

    assert len(mailer.sent) == 1
    url = Web_LinkBuilder(_BASE).verification_url(token, locale)
    _assert_url_round_trips(url, token, locale)
    _assert_mail_carries_url(mailer.sent[0].body, url, locale)


@given(token=_tokens, locale=_locales)
@settings(max_examples=100)
def test_property_10_resend_mail_carries_url_on_one_line(token: str, locale: str) -> None:
    """Feature: api-route-wiring, Property 10: Account link URLs round trip

    The resend mail body carries the verification URL on a single line, in the
    language matching the locale.
    """
    users = In_Memory_UserRepository()
    users.add(
        UserRecord(
            stable_id="user-0",
            email=_EMAIL,
            locale=locale,
            status=USER_STATUS_UNVERIFIED,
        )
    )
    mailer = Console_Mailer()
    use_case = ResendVerificationUseCase(
        users=users,
        verification_links=In_Memory_VerificationLinkRepository(),
        link_builder=Web_LinkBuilder(_BASE),
        mailer=mailer,
        clock=Test_Clock(start=_T0),
        token_factory=lambda: token,
    )

    use_case.execute(ResendVerificationRequest(email=_EMAIL))

    assert len(mailer.sent) == 1
    url = Web_LinkBuilder(_BASE).verification_url(token, locale)
    _assert_url_round_trips(url, token, locale)
    _assert_mail_carries_url(mailer.sent[0].body, url, locale)


@given(token=_tokens, locale=_locales, base=_bases)
@settings(max_examples=200)
def test_property_10_reset_url_round_trips(token: str, locale: str, base: str) -> None:
    """Feature: api-route-wiring, Property 10: Account link URLs round trip

    The reset URL parses back to the web base host, the locale path, and the
    original token, with no whitespace.
    """
    url = Web_LinkBuilder(base).reset_url(token, locale)

    _assert_url_round_trips(url, token, locale, "reset-password")


@given(token=_tokens, locale=_locales)
@settings(max_examples=100)
def test_property_10_reset_mail_carries_url_on_one_line(token: str, locale: str) -> None:
    """Feature: api-route-wiring, Property 10: Account link URLs round trip

    The reset mail body carries the reset URL on a single line, in the language
    matching the locale.
    """
    users = In_Memory_UserRepository()
    users.add(
        UserRecord(
            stable_id="user-0",
            email=_EMAIL,
            locale=locale,
            status=USER_STATUS_UNVERIFIED,
        )
    )
    mailer = Console_Mailer()
    use_case = RequestPasswordResetUseCase(
        users=users,
        reset_links=In_Memory_ResetLinkRepository(),
        link_builder=Web_LinkBuilder(_BASE),
        mailer=mailer,
        clock=Test_Clock(start=_T0),
        token_factory=lambda: token,
    )

    use_case.execute(RequestPasswordResetRequest(email=_EMAIL))

    assert len(mailer.sent) == 1
    url = Web_LinkBuilder(_BASE).reset_url(token, locale)
    _assert_url_round_trips(url, token, locale, "reset-password")
    _assert_mail_carries_url(mailer.sent[0].body, url, locale)
