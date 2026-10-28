"""Unit tests for the in-memory identity / verification-link fakes (task 12.1, Req 5.2).

These prove the new in-memory fakes satisfy their domain ports and enforce the
same invariants their production counterparts do, and that the sign-up use case
runs unchanged against them — the substitutability the fakes exist for (Req 5.2).
The use case here is wired to the real infrastructure fakes (not in-test doubles)
so the application-layer flow and the infrastructure fakes are exercised together.
"""

from __future__ import annotations

from datetime import UTC, datetime
from itertools import count

import pytest
from edu_it_application.sign_up import ACCEPTED, SignUpRequest, SignUpUseCase
from edu_it_application.verify_email import (
    VerifyEmailOutcome,
    VerifyEmailRequest,
    VerifyEmailUseCase,
)
from edu_it_domain.ports.repository import (
    USER_STATUS_UNVERIFIED,
    USER_STATUS_VERIFIED,
    UserRecord,
    UserRepository,
)
from edu_it_domain.ports.verification_link import (
    VerificationLink,
    VerificationLinkRepository,
)
from edu_it_infrastructure.fakes import (
    Console_Mailer,
    In_Memory_UserRepository,
    In_Memory_VerificationLinkRepository,
    Test_Clock,
)

_T0 = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)


def test_in_memory_user_repository_satisfies_the_port_and_rejects_duplicates() -> None:
    users: UserRepository = In_Memory_UserRepository()
    user = UserRecord(stable_id="u1", email="a@example.com", locale="en", status="unverified")

    users.add(user)
    assert users.get_by_stable_id("u1") == user
    assert users.get_by_email("a@example.com") == user
    assert users.get_by_email("missing@example.com") is None

    with pytest.raises(ValueError, match="stable_id"):
        users.add(UserRecord(stable_id="u1", email="b@example.com", locale="en", status="x"))
    with pytest.raises(ValueError, match="email"):
        users.add(UserRecord(stable_id="u2", email="a@example.com", locale="en", status="x"))


def test_in_memory_user_repository_mark_verified_is_idempotent() -> None:
    users = In_Memory_UserRepository()
    users.add(
        UserRecord(
            stable_id="u1",
            email="a@example.com",
            locale="en",
            status=USER_STATUS_UNVERIFIED,
        )
    )

    # First transition succeeds and flips the status; it is visible by both keys.
    assert users.mark_verified("u1") is True
    assert users.get_by_stable_id("u1").status == USER_STATUS_VERIFIED  # type: ignore[union-attr]
    assert users.get_by_email("a@example.com").status == USER_STATUS_VERIFIED  # type: ignore[union-attr]

    # A redundant transition and an unknown user both report no change (Req 7.4, 7.5).
    assert users.mark_verified("u1") is False
    assert users.mark_verified("missing") is False


def test_in_memory_verification_link_repository_enforces_single_use() -> None:
    links: VerificationLinkRepository = In_Memory_VerificationLinkRepository()
    link = VerificationLink(
        token="tok",
        user_stable_id="u1",
        issued_at=_T0,
        expires_at=_T0.replace(hour=13),
    )
    links.add(link)
    assert links.get("tok") == link
    assert links.get("unknown") is None

    with pytest.raises(ValueError, match="token"):
        links.add(link)

    # First consumption succeeds; a second is rejected (single use, Req 7.4).
    assert links.mark_consumed("tok", consumed_at=_T0.replace(hour=12, minute=30)) is True
    assert links.mark_consumed("tok", consumed_at=_T0.replace(hour=12, minute=45)) is False
    assert links.mark_consumed("unknown", consumed_at=_T0) is False
    consumed = links.get("tok")
    assert consumed is not None and consumed.is_consumed


def test_sign_up_runs_against_the_infrastructure_fakes() -> None:
    users = In_Memory_UserRepository()
    links = In_Memory_VerificationLinkRepository()
    mailer = Console_Mailer()
    clock = Test_Clock(start=_T0)
    tokens = (f"token-{n}" for n in count())
    ids = (f"user-{n}" for n in count())

    use_case = SignUpUseCase(
        users=users,
        verification_links=links,
        mailer=mailer,
        clock=clock,
        token_factory=lambda: next(tokens),
        stable_id_factory=lambda: next(ids),
        password_hasher=lambda raw: f"hash:{raw}",
    )

    result = use_case.execute(SignUpRequest(email="new@example.com", password="a valid passphrase"))

    assert result == ACCEPTED
    assert len(users) == 1
    assert len(mailer.sent) == 1
    assert mailer.sent[0].to == "new@example.com"
    # The verification mail carries the single-use token so task 12.2 can resolve
    # the opened link; the stored link is addressable by exactly that token.
    token = mailer.sent[0].headers["X-Verification-Token"]
    stored = links.get(token)
    assert stored is not None
    assert stored.user_stable_id == users.get_by_email("new@example.com").stable_id  # type: ignore[union-attr]


def test_sign_up_then_verify_runs_against_the_infrastructure_fakes() -> None:
    # The sign-up and verification use cases run end to end against the real
    # fakes: opening the emailed link verifies the user and invalidates the link,
    # and a second opening is rejected as already used (Req 7.4, 25.1).
    users = In_Memory_UserRepository()
    links = In_Memory_VerificationLinkRepository()
    mailer = Console_Mailer()
    clock = Test_Clock(start=_T0)
    tokens = (f"token-{n}" for n in count())
    ids = (f"user-{n}" for n in count())

    sign_up = SignUpUseCase(
        users=users,
        verification_links=links,
        mailer=mailer,
        clock=clock,
        token_factory=lambda: next(tokens),
        stable_id_factory=lambda: next(ids),
        password_hasher=lambda raw: f"hash:{raw}",
    )
    verify = VerifyEmailUseCase(users=users, verification_links=links, clock=clock)

    sign_up.execute(SignUpRequest(email="new@example.com", password="a valid passphrase"))
    token = mailer.sent[0].headers["X-Verification-Token"]
    assert users.get_by_email("new@example.com").status == USER_STATUS_UNVERIFIED  # type: ignore[union-attr]

    first = verify.execute(VerifyEmailRequest(token=token))
    assert first.outcome is VerifyEmailOutcome.VERIFIED
    assert users.get_by_email("new@example.com").status == USER_STATUS_VERIFIED  # type: ignore[union-attr]
    assert links.get(token).is_consumed  # type: ignore[union-attr]

    second = verify.execute(VerifyEmailRequest(token=token))
    assert second.outcome is VerifyEmailOutcome.INVALID_OR_EXPIRED
    assert second.offer_resend
