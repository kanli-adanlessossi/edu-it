"""Unit tests for the Redis-backed Redis_SignInRateLimiter adapter (task 7.3, Req 5.1, 5.2, 12).

These exercise the sign-in rate limiter against a Redis test double (``fakeredis``)
and the controllable ``Test_Clock`` so the window boundary is deterministic.
Coverage: blocking only once failures exceed the threshold, the window reopening
once it elapses per the Clock, reset on success, and that the adapter reads the
business constants from ``domain/config.py`` by default.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import fakeredis
import pytest
from edu_it_domain.config import (
    SIGN_IN_RATE_LIMIT_THRESHOLD,
    SIGN_IN_RATE_LIMIT_WINDOW,
)
from edu_it_domain.ports import SignInRateLimiter
from edu_it_infrastructure.adapters import Redis_SignInRateLimiter
from edu_it_infrastructure.fakes.clock import Test_Clock

_T0 = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def client() -> fakeredis.FakeRedis:
    return fakeredis.FakeRedis(decode_responses=True)


@pytest.fixture
def clock() -> Test_Clock:
    c = Test_Clock()
    c.set(_T0)
    return c


def test_adapter_satisfies_the_domain_port(client: fakeredis.FakeRedis, clock: Test_Clock) -> None:
    assert isinstance(Redis_SignInRateLimiter(client, clock), SignInRateLimiter)


def test_defaults_use_the_domain_business_constants(
    client: fakeredis.FakeRedis, clock: Test_Clock
) -> None:
    # Req 12.3: the threshold/window come from domain/config.py, not hardcoded.
    limiter = Redis_SignInRateLimiter(client, clock)
    assert limiter._threshold == SIGN_IN_RATE_LIMIT_THRESHOLD
    assert limiter._window == SIGN_IN_RATE_LIMIT_WINDOW


def test_not_blocked_until_failures_exceed_the_threshold(
    client: fakeredis.FakeRedis, clock: Test_Clock
) -> None:
    limiter = Redis_SignInRateLimiter(client, clock, threshold=3, window=timedelta(minutes=5))
    assert limiter.is_blocked("alice") is False
    for _ in range(3):  # up to and including the threshold -> still allowed
        limiter.register_failure("alice")
    assert limiter.is_blocked("alice") is False
    limiter.register_failure("alice")  # one over the threshold -> blocked
    assert limiter.is_blocked("alice") is True


def test_window_reopens_after_it_elapses_per_the_clock(
    client: fakeredis.FakeRedis, clock: Test_Clock
) -> None:
    # Req 12.2: once the window elapses, attempts are allowed again.
    window = timedelta(minutes=5)
    limiter = Redis_SignInRateLimiter(client, clock, threshold=2, window=window)
    for _ in range(3):
        limiter.register_failure("bob")
    assert limiter.is_blocked("bob") is True

    clock.advance(window)  # window has elapsed
    assert limiter.is_blocked("bob") is False


def test_identifiers_are_counted_independently(
    client: fakeredis.FakeRedis, clock: Test_Clock
) -> None:
    limiter = Redis_SignInRateLimiter(client, clock, threshold=1, window=timedelta(minutes=5))
    limiter.register_failure("a")
    limiter.register_failure("a")
    assert limiter.is_blocked("a") is True
    assert limiter.is_blocked("b") is False


def test_reset_clears_the_counter(client: fakeredis.FakeRedis, clock: Test_Clock) -> None:
    limiter = Redis_SignInRateLimiter(client, clock, threshold=1, window=timedelta(minutes=5))
    limiter.register_failure("carol")
    limiter.register_failure("carol")
    assert limiter.is_blocked("carol") is True
    limiter.reset("carol")
    assert limiter.is_blocked("carol") is False


def test_a_failure_after_the_window_starts_a_fresh_window(
    client: fakeredis.FakeRedis, clock: Test_Clock
) -> None:
    window = timedelta(minutes=5)
    limiter = Redis_SignInRateLimiter(client, clock, threshold=2, window=window)
    for _ in range(3):
        limiter.register_failure("dave")
    assert limiter.is_blocked("dave") is True

    clock.advance(window)
    limiter.register_failure("dave")  # opens a new window with count 1
    assert limiter.is_blocked("dave") is False
