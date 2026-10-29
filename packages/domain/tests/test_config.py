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


# --- Learning-core business numbers (learning-core spec) ---------------------


def test_cooldown_interval_is_a_positive_duration() -> None:
    # Req 4.3: cooldown interval is a configurable business constant read from
    # config; it must be a strictly positive duration.
    assert isinstance(config.COOLDOWN_INTERVAL, timedelta)
    assert timedelta(0) < config.COOLDOWN_INTERVAL


def test_mastery_thresholds_are_30_60_85() -> None:
    # Req 5.2, 5.3, 5.5, 5.6 / domain-rules.md: the state bands are fixed at
    # 30 (Practiced), 60 (Proficient), and 85 (Mastered gate).
    assert config.MASTERY_THRESHOLDS.practiced == 30
    assert config.MASTERY_THRESHOLDS.proficient == 60
    assert config.MASTERY_THRESHOLDS.mastered == 85


def test_mastery_thresholds_are_strictly_increasing_within_bounds() -> None:
    # The bands must partition the scale: MIN < practiced < proficient
    # < mastered <= MAX (Req 5.1-5.3, 5.5).
    t = config.MASTERY_THRESHOLDS
    assert config.MASTERY_MIN < t.practiced < t.proficient < t.mastered <= config.MASTERY_MAX


def test_mastery_thresholds_is_immutable() -> None:
    import dataclasses

    import pytest

    with pytest.raises(dataclasses.FrozenInstanceError):
        config.MASTERY_THRESHOLDS.practiced = 1  # type: ignore[misc]


def test_mastery_scale_bounds_are_0_and_100() -> None:
    # Req 5.1, 5.8: mastery is bounded to the inclusive range 0-100.
    assert config.MASTERY_MIN == 0
    assert config.MASTERY_MAX == 100


def test_decay_rate_is_minus_one_per_day() -> None:
    # Req 8.1 / domain-rules.md: decay is exactly -1 point per overdue day.
    assert config.DECAY_RATE_PER_DAY == -1


def test_decay_floor_once_practiced_is_30() -> None:
    # Req 5.9, 5.10, 5.11, 8.2: decay never drops below 30 once Practiced, and
    # the floor coincides with the Practiced threshold.
    assert config.DECAY_FLOOR_ONCE_PRACTICED == 30
    assert config.MASTERY_THRESHOLDS.practiced == config.DECAY_FLOOR_ONCE_PRACTICED


def test_review_intervals_are_positive_durations() -> None:
    # Req 9.1, 9.4, 9.5: initial/passed/failed review intervals are configurable
    # business constants; all must be strictly positive durations.
    for interval in (
        config.INITIAL_REVIEW_INTERVAL,
        config.PASSED_REVIEW_INTERVAL,
        config.FAILED_REVIEW_INTERVAL,
    ):
        assert isinstance(interval, timedelta)
        assert timedelta(0) < interval


def test_passed_review_lengthens_and_failed_review_shortens_the_interval() -> None:
    # Req 9.4: a passed review interval is greater than the initial interval;
    # Req 9.5: a failed review interval is less than the initial interval.
    assert config.PASSED_REVIEW_INTERVAL > config.INITIAL_REVIEW_INTERVAL
    assert config.FAILED_REVIEW_INTERVAL < config.INITIAL_REVIEW_INTERVAL


def test_min_eligible_question_count_is_3() -> None:
    # Req 28.1, 28.2, 28.3: a review needs at least 3 eligible questions.
    assert config.MIN_ELIGIBLE_QUESTION_COUNT == 3


def test_hint_multiplier_tempers_gain_within_the_open_unit_interval() -> None:
    # Req 7.2, 7.3: the hint multiplier tempers the gain, so it lies strictly
    # between 0 and 1.
    assert isinstance(config.HINT_MULTIPLIER, float)
    assert 0.0 < config.HINT_MULTIPLIER < 1.0


def test_rubric_pass_threshold_is_a_fraction_of_max_points() -> None:
    # Req 10.1, 10.2, 10.3: the pass threshold is a fraction in (0, 1].
    assert isinstance(config.RUBRIC_PASS_THRESHOLD, float)
    assert 0.0 < config.RUBRIC_PASS_THRESHOLD <= 1.0


def test_anti_farming_window_is_24_hours() -> None:
    # Req 6.1, 6.4 / domain-rules.md: the anti-farming window is 24 hours.
    assert timedelta(hours=24) == config.ANTI_FARMING_WINDOW
