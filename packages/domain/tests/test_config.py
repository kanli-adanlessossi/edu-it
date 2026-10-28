"""Unit tests for the domain business constants (Req 7.2, 12.3).

These assert the values fixed by the requirements (the verification- and
reset-link TTLs and the password policy) and that the configurable constants
are present, well-typed, and internally consistent.
"""

from datetime import timedelta

from edu_it_domain import config


def test_verification_link_ttl_is_24_hours() -> None:
    # Req 7.3: single-use verification link expires 24 h after issuance.
    assert timedelta(hours=24) == config.VERIFICATION_LINK_TTL


def test_reset_link_ttl_is_1_hour() -> None:
    # Req 9.1: single-use reset link expires 1 h after issuance.
    assert timedelta(hours=1) == config.RESET_LINK_TTL


def test_session_duration_is_a_positive_duration() -> None:
    # Req 8.1: session duration is a configured business constant.
    assert isinstance(config.SESSION_DURATION, timedelta)
    assert timedelta(0) < config.SESSION_DURATION


def test_oauth_link_confirmation_window_is_a_positive_duration() -> None:
    # Req 10.2 / 26.2: link-confirmation window is a configured constant.
    assert isinstance(config.OAUTH_LINK_CONFIRMATION_WINDOW, timedelta)
    assert timedelta(0) < config.OAUTH_LINK_CONFIRMATION_WINDOW


def test_sign_in_rate_limit_threshold_is_a_positive_integer() -> None:
    # Req 12.1 / 12.3: threshold is a configured business constant.
    assert isinstance(config.SIGN_IN_RATE_LIMIT_THRESHOLD, int)
    assert config.SIGN_IN_RATE_LIMIT_THRESHOLD > 0


def test_sign_in_rate_limit_window_is_a_positive_duration() -> None:
    # Req 12.2 / 12.3: window is a configured business constant.
    assert isinstance(config.SIGN_IN_RATE_LIMIT_WINDOW, timedelta)
    assert timedelta(0) < config.SIGN_IN_RATE_LIMIT_WINDOW


def test_password_policy_bounds_are_12_and_128() -> None:
    # Req 7.2: password of at least 12 and at most 128 characters.
    assert config.PASSWORD_POLICY.min_length == 12
    assert config.PASSWORD_POLICY.max_length == 128


def test_password_policy_accepts_lengths_on_and_within_the_bounds() -> None:
    policy = config.PASSWORD_POLICY
    assert policy.is_satisfied_by("a" * policy.min_length)
    assert policy.is_satisfied_by("a" * policy.max_length)
    assert policy.is_satisfied_by("a" * ((policy.min_length + policy.max_length) // 2))


def test_password_policy_rejects_lengths_outside_the_bounds() -> None:
    policy = config.PASSWORD_POLICY
    assert not policy.is_satisfied_by("a" * (policy.min_length - 1))
    assert not policy.is_satisfied_by("")
    assert not policy.is_satisfied_by("a" * (policy.max_length + 1))


def test_password_policy_is_immutable() -> None:
    import dataclasses

    import pytest

    with pytest.raises(dataclasses.FrozenInstanceError):
        config.PASSWORD_POLICY.min_length = 1  # type: ignore[misc]
