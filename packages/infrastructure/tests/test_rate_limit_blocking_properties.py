"""Property-based test for sign-in rate-limit blocking (task 17.4).

Feature: foundations, Property 17: Rate limiting blocks over-threshold sign-ins without evaluating credentials

Property 17 (design.md): *For any* identifier whose failed sign-in attempts
exceed the configured threshold within the configured window, each further
attempt within the window is rejected without evaluating the submitted
credentials and establishes no Session even when the credentials are valid; once
the window elapses as measured by the Clock, attempts for that identifier are
allowed again.

This drives :class:`edu_it_application.authenticate.AuthenticateUseCase`
directly, wired to the infrastructure port fakes exactly as the test/dev
composition root wires them — a :class:`In_Memory_SignInRateLimiter` measuring
its window against a controllable :class:`Test_Clock`, plus the in-memory
user / session fakes — and an *exploding spy* credential store that proves the
property's "without evaluating the submitted credentials" clause structurally:
while the identifier is rate-limited, :meth:`verify_password` must never be
called, so a store that raises on any such call would turn a leaked credential
check into a test failure. Everywhere else the spy delegates to a real
:class:`In_Memory_CredentialStore`, so a genuinely valid credential still
authenticates once the window reopens.

Each drawn example fixes a small threshold and window (the limiter accepts both
as parameters), registers exactly one verified account whose stored credential
matches the submitted password, and walks the identifier through the property's
three phases:

1. **Drive over threshold.** ``threshold + 1`` failed attempts (wrong password)
   are submitted so the identifier's current-window failure count strictly
   exceeds the threshold — the limiter's strict ``> threshold`` boundary, so the
   identifier is now blocked. The spy *is* consulted here (these are real
   credential evaluations) and each returns a mismatch.
2. **Blocked within the window.** With the clock still inside the window, further
   attempts — including ones submitting the genuinely *valid* password — are
   rejected with :class:`SignInFailure.RATE_LIMITED`. The exploding spy records
   that no :meth:`verify_password` call happened for these attempts (the check is
   short-circuited before credentials are touched), and neither the durable
   session repository nor the ephemeral session store holds any Session, so none
   was established even for valid credentials (Req 12.1, 12.4).
3. **Window elapses.** The clock is advanced to or past the window boundary. The
   same valid credential now authenticates: ``execute`` returns a
   :class:`SignInResult`, the credential was evaluated (the spy was consulted
   again), and a Session exists in both stores — attempts for the identifier are
   allowed again (Req 12.2).

The blocked-then-reopened behaviour across those phases is the biconditional the
property names.

Validates: Requirements 12.1, 12.2, 12.4

Uses Hypothesis, the pinned Python property-based testing library
(docs/adr/0001-versions.md).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from itertools import count

import pytest
from edu_it_application.authenticate import (
    AuthenticateUseCase,
    SignInError,
    SignInFailure,
    SignInRequest,
    SignInResult,
)
from edu_it_domain.ports.credential_store import CredentialStore
from edu_it_domain.ports.repository import USER_STATUS_VERIFIED, UserRecord
from edu_it_infrastructure.fakes import (
    In_Memory_CredentialStore,
    In_Memory_SessionRepository,
    In_Memory_SessionStore,
    In_Memory_SignInRateLimiter,
    In_Memory_UserRepository,
    Test_Clock,
)
from hypothesis import given, settings
from hypothesis import strategies as st

_T0 = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)

# --- Generators constrained to the property's input space --------------------

# A well-formed email, drawn the same minimal accepted shape the other sign-in
# property tests use; it is the rate-limit identifier here.
_EMAIL_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"
_emails = st.builds(
    lambda local, label, tld: f"{local}@{label}.{tld}",
    local=st.text(alphabet=_EMAIL_ALPHABET, min_size=1, max_size=20),
    label=st.text(alphabet=_EMAIL_ALPHABET, min_size=1, max_size=20),
    tld=st.text(alphabet="abcdefghijklmnopqrstuvwxyz", min_size=2, max_size=6),
)

_locales = st.sampled_from(["fr", "en"])

# The account's correct password. Non-empty so a wrong guess is easy to form.
_passwords = st.text(min_size=1, max_size=40)

# A small threshold so the "drive over threshold" phase stays cheap; the limiter
# accepts the threshold as a parameter precisely so tests can use small bounds.
_thresholds = st.integers(min_value=1, max_value=6)

# A window measured in whole minutes; the Test_Clock advances by exact durations.
_windows = st.integers(min_value=1, max_value=120).map(lambda m: timedelta(minutes=m))

# How far into the still-open window the blocked attempts are made (a fraction of
# the window, always strictly inside it).
_within_fractions = st.sampled_from([0.0, 0.25, 0.5, 0.75])


class _ExplodingSpyCredentialStore(CredentialStore):
    """A :class:`CredentialStore` that records and can forbid credential checks.

    Wraps a real :class:`In_Memory_CredentialStore` so genuine verification still
    works, while counting every :meth:`verify_password` call. When
    :attr:`forbidden` is set, any such call raises — turning a leaked credential
    evaluation during a rate-limited attempt into an immediate test failure, which
    is how the property's "without evaluating the submitted credentials" clause is
    proven structurally (Req 12.1, 12.4).
    """

    __test__ = False

    def __init__(self) -> None:
        self._inner = In_Memory_CredentialStore()
        self.verify_calls = 0
        self.forbidden = False

    def set_password(self, user_stable_id: str, hashed_secret: str) -> None:
        self._inner.set_password(user_stable_id, hashed_secret)

    def verify_password(self, user_stable_id: str, password: str) -> bool:
        if self.forbidden:
            raise AssertionError(
                "verify_password was called while the identifier was rate-limited; "
                "credentials must not be evaluated for a blocked attempt"
            )
        self.verify_calls += 1
        return self._inner.verify_password(user_stable_id, password)


def _hash(raw: str) -> str:
    """The trivial deterministic "hash" the test/dev credential store compares."""
    return f"hash:{raw}"


def _make_use_case(
    *,
    threshold: int,
    window: timedelta,
) -> tuple[
    AuthenticateUseCase,
    In_Memory_UserRepository,
    _ExplodingSpyCredentialStore,
    In_Memory_SessionRepository,
    In_Memory_SessionStore,
    Test_Clock,
]:
    """Wire the sign-in use case to fresh fakes with a small rate-limit bound.

    Mirrors the test/dev composition root: an in-memory limiter measuring its
    window against the ``Test_Clock``, the exploding spy credential store, and a
    deterministic session-reference factory, so the whole flow is reproducible
    under Hypothesis with empty stores.
    """
    users = In_Memory_UserRepository()
    credentials = _ExplodingSpyCredentialStore()
    sessions = In_Memory_SessionRepository()
    clock = Test_Clock(start=_T0)
    session_store = In_Memory_SessionStore(clock)
    rate_limiter = In_Memory_SignInRateLimiter(clock, threshold=threshold, window=window)
    refs = (f"ref-{n}" for n in count())
    use_case = AuthenticateUseCase(
        users=users,
        credentials=credentials,
        sessions=sessions,
        session_store=session_store,
        rate_limiter=rate_limiter,
        clock=clock,
        session_ref_factory=lambda: next(refs),
    )
    return use_case, users, credentials, sessions, session_store, clock


@given(
    email=_emails,
    locale=_locales,
    password=_passwords,
    threshold=_thresholds,
    window=_windows,
    within_fraction=_within_fractions,
)
@settings(max_examples=200)
def test_property_17_rate_limiting_blocks_over_threshold_sign_ins_without_evaluating_credentials(
    email: str,
    locale: str,
    password: str,
    threshold: int,
    window: timedelta,
    within_fraction: float,
) -> None:
    """Feature: foundations, Property 17: Rate limiting blocks over-threshold sign-ins without evaluating credentials.

    Once an identifier's failed attempts exceed the configured threshold within
    the window, each further attempt is rejected with ``RATE_LIMITED`` without
    evaluating credentials and establishes no Session even for a valid password;
    once the window elapses per the Clock, the same valid credential
    authenticates again (Req 12.1, 12.2, 12.4).
    """
    use_case, users, credentials, sessions, session_store, clock = _make_use_case(
        threshold=threshold, window=window
    )

    stable_id = "user-0"
    users.add(
        UserRecord(stable_id=stable_id, email=email, locale=locale, status=USER_STATUS_VERIFIED)
    )
    credentials.set_password(stable_id, _hash(password))

    correct = _hash(password)
    wrong = _hash(password + "x")
    assert correct != wrong

    # --- Phase 1: drive the identifier strictly over the threshold -----------
    # threshold + 1 failed (wrong-password) attempts make the current-window
    # failure count exceed the threshold, so the limiter's strict ">" boundary
    # now reports the identifier as blocked. These are genuine credential
    # evaluations, so the spy is consulted and each returns a mismatch.
    for _ in range(threshold + 1):
        with pytest.raises(SignInError) as raised:
            use_case.execute(SignInRequest(email=email, password=wrong))
        assert raised.value.failure == SignInFailure.INVALID_CREDENTIALS

    # No Session was established by any failed attempt.
    assert sessions.get("ref-0") is None
    assert session_store.resolve("ref-0") is None
    calls_before_block = credentials.verify_calls

    # --- Phase 2: blocked within the still-open window -----------------------
    # Stay strictly inside the window. From here, credentials must never be
    # evaluated, so arm the exploding spy: any verify_password call now fails.
    clock.advance(timedelta(seconds=window.total_seconds() * within_fraction))
    credentials.forbidden = True

    # A further attempt with the genuinely VALID password is still rejected as
    # RATE_LIMITED, proving the block short-circuits credential evaluation and
    # establishes no Session even for valid credentials (Req 12.1, 12.4).
    with pytest.raises(SignInError) as blocked:
        use_case.execute(SignInRequest(email=email, password=correct))
    assert blocked.value.failure == SignInFailure.RATE_LIMITED

    # The spy was not consulted for the blocked attempt (credentials untouched).
    assert credentials.verify_calls == calls_before_block
    # Still no Session anywhere — none was minted for a blocked attempt.
    assert sessions.get("ref-0") is None
    assert session_store.resolve("ref-0") is None

    # --- Phase 3: the window elapses -----------------------------------------
    # Advance to the window boundary (and the fraction already consumed), so the
    # stored window has elapsed per the Clock and attempts are allowed again.
    credentials.forbidden = False
    before = clock.now()
    clock.advance(window)

    # The same valid credential now authenticates and establishes a Session.
    result = use_case.execute(SignInRequest(email=email, password=correct))
    assert isinstance(result, SignInResult)
    assert result.user_stable_id == stable_id

    # Credentials were evaluated again for the allowed attempt.
    assert credentials.verify_calls == calls_before_block + 1

    # A Session now exists in both stores, owned by the account.
    durable = sessions.get(result.server_side_ref)
    assert durable is not None
    assert durable.user_stable_id == stable_id
    assert not durable.is_revoked
    resolved = session_store.resolve(result.server_side_ref)
    assert resolved is not None
    assert resolved.user_stable_id == stable_id
    # Sanity: the session was minted after the window elapsed.
    assert durable.expires_at > before
