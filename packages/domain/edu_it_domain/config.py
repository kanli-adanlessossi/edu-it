"""Business constants for the EDU-IT foundations phase (Req 7.2, 12.3).

This module is the single framework-free home for every tunable business
number of the account system, per the business-numbers rule in
``.kiro/steering/structure.md`` and the foundations design (Req 12.3). Keeping
these constants in one place in the domain layer -- which imports no web or API
framework (Req 1.8) -- lets the application, infrastructure, and API layers
read the same authoritative values while the domain reads time only through the
injected ``Clock`` (Req 5.5).

Durations are expressed as :class:`datetime.timedelta` so callers compare them
against instants read from the injected ``Clock`` without unit ambiguity.

Values fixed by the requirements
--------------------------------
- Verification-link TTL: 24 hours after issuance (Req 7.3).
- Reset-link TTL: 1 hour after issuance (Req 9.1).
- Password policy: minimum 12 and maximum 128 characters (Req 7.2, and the
  ``Password_Policy`` glossary entry in the foundations requirements).

Values the requirements leave as configurable business constants
-----------------------------------------------------------------
The session duration (Req 8.1), the OAuth link-confirmation window (Req 10.2,
26.2), and the sign-in rate-limit threshold and window (Req 12.1-12.4) are
specified as "configured" business constants without a fixed number. The
defaults chosen here are conventional, security-conscious starting points; they
live here precisely so they can be tuned in one framework-free location without
touching the surrounding logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Final

# --- Link time-to-live constants ---------------------------------------------

#: How long a single-use email Verification_Link stays valid after issuance,
#: measured by the injected Clock (Req 7.3).
VERIFICATION_LINK_TTL: Final[timedelta] = timedelta(hours=24)

#: How long a single-use password Reset_Link stays valid after issuance,
#: measured by the injected Clock (Req 9.1).
RESET_LINK_TTL: Final[timedelta] = timedelta(hours=1)

# --- Session constant --------------------------------------------------------

#: How long a server-side Session remains valid after it is established,
#: measured by the injected Clock (Req 8.1). A configurable business constant;
#: two weeks is a conventional "remembered" web-session lifetime.
SESSION_DURATION: Final[timedelta] = timedelta(days=14)

# --- GitHub OAuth account-linking constant -----------------------------------

#: How long the explicit GitHub account-link confirmation step stays open after
#: it is presented, measured by the injected Clock (Req 10.2, 26.2, 26.5). A
#: configurable business constant; the window is deliberately short so a pending
#: link cannot linger.
OAUTH_LINK_CONFIRMATION_WINDOW: Final[timedelta] = timedelta(minutes=15)

# --- Sign-in rate-limiting constants -----------------------------------------

#: Number of failed sign-in attempts for a single identifier that may occur
#: within :data:`SIGN_IN_RATE_LIMIT_WINDOW` before further attempts are blocked
#: without evaluating credentials (Req 12.1, 12.4). A configurable business
#: constant.
SIGN_IN_RATE_LIMIT_THRESHOLD: Final[int] = 5

#: The rolling window over which :data:`SIGN_IN_RATE_LIMIT_THRESHOLD` failed
#: sign-in attempts are counted, measured by the injected Clock; once it elapses
#: attempts are allowed again (Req 12.2). A configurable business constant.
SIGN_IN_RATE_LIMIT_WINDOW: Final[timedelta] = timedelta(minutes=15)


# --- Password policy ---------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PasswordPolicy:
    """The configured password-strength policy (Req 7.2).

    The policy constrains password length only: a password must be at least
    :attr:`min_length` and at most :attr:`max_length` characters. The bundled
    :func:`is_satisfied_by` predicate lets the application layer check a
    candidate password without duplicating the bounds.
    """

    min_length: int
    max_length: int

    def is_satisfied_by(self, password: str) -> bool:
        """Return whether ``password`` meets the length policy.

        Both bounds are inclusive: a password whose length equals
        :attr:`min_length` or :attr:`max_length` satisfies the policy.
        """

        return self.min_length <= len(password) <= self.max_length


#: The configured password policy: 12-128 characters inclusive (Req 7.2, and
#: the ``Password_Policy`` glossary entry).
PASSWORD_POLICY: Final[PasswordPolicy] = PasswordPolicy(min_length=12, max_length=128)


# =============================================================================
# Learning-core business numbers (learning-core spec)
# =============================================================================
#
# Everything below is the single framework-free home for the learning engine's
# tunable numbers, read by the domain learning services (unlocking, scoring,
# cooldown, mastery, anti-farming, hints, decay, review, rubric). No learning
# service hardcodes any of these values; they all read them from here, per the
# business-numbers rule in ``.kiro/steering/structure.md`` and
# ``.kiro/steering/quality.md`` and the learning-core design.
#
# Values fixed by the domain rules (``.kiro/steering/domain-rules.md``) are the
# mastery thresholds (30 / 60 / 85), the decay rate (-1 point per overdue day),
# the "never below 30 once Practiced" floor, and the 24-hour anti-farming
# window. The cooldown interval, the review intervals, the hint multiplier, the
# rubric pass threshold, and the minimum eligible-question count are left by the
# requirements as configurable business constants (Req 4.3, 7.3, 9.4, 9.5,
# 10.2, 28.3); the defaults chosen here are conventional, pedagogy-conscious
# starting points that live here so they can be tuned in one place without
# touching the learning services.

# --- Quiz-attempt cooldown ---------------------------------------------------

#: How long a learner must wait after an attempt on a quiz before that quiz is
#: attemptable again, measured by the injected Clock (Req 4.1, 4.2, 4.3). A
#: configurable business constant; the cooldown spaces attempts without blocking
#: a same-day retry.
COOLDOWN_INTERVAL: Final[timedelta] = timedelta(hours=1)

# --- Mastery thresholds, bands, and bounds -----------------------------------


@dataclass(frozen=True, slots=True)
class MasteryThresholds:
    """The mastery-state threshold bands on the 0-100 scale (Req 5.1-5.3, 5.5).

    The thresholds partition the inclusive 0-100 mastery scale into the four
    state bands fixed verbatim by the domain rules
    (``.kiro/steering/domain-rules.md``):

    - below :attr:`practiced` (< 30): ``Introduced`` on first encounter.
    - :attr:`practiced` to :attr:`proficient` - 1 (30-59): ``Practiced``.
    - :attr:`proficient` to :attr:`mastered` - 1 (60-84): ``Proficient``.
    - :attr:`mastered` and above (>= 85): the numeric part of the ``Mastered``
      gate, which also requires a passed review (and a passed lab check where a
      lab covers the concept).

    The integer boundaries sit at the natural cut points (30 enters Practiced,
    60 enters Proficient, 85 enters the Mastered gate), matching the boundary
    note in ``docs/RULES-REVIEW.md`` D7 section 3.2.
    """

    practiced: int
    proficient: int
    mastered: int


#: The mastery-state thresholds fixed by the domain rules: 30 / 60 / 85
#: (Req 5.2, 5.3, 5.5, 5.6; ``.kiro/steering/domain-rules.md``).
MASTERY_THRESHOLDS: Final[MasteryThresholds] = MasteryThresholds(
    practiced=30,
    proficient=60,
    mastered=85,
)

#: The inclusive lower bound of the mastery scale; a mastery change computing a
#: value below this is floored here (Req 5.1, 5.8).
MASTERY_MIN: Final[int] = 0

#: The inclusive upper bound of the mastery scale; a mastery change computing a
#: value above this is capped here (Req 5.1, 5.8).
MASTERY_MAX: Final[int] = 100

# --- Decay -------------------------------------------------------------------

#: The decay rate fixed by the domain rules: mastery drops by exactly this many
#: points for each complete 24-hour day a concept's review is overdue, computed
#: deterministically from the injected Clock (Req 8.1; ADR A6;
#: ``.kiro/steering/domain-rules.md``). Expressed as a negative delta per day.
DECAY_RATE_PER_DAY: Final[int] = -1

#: The floor that decay may never breach once a concept has ever reached
#: Practiced: decay leaves mastery at or above this value and never relocks
#: content or drops the state to Introduced/Unseen (Req 5.9, 5.10, 5.11, 8.2;
#: ``.kiro/steering/domain-rules.md``). Fixed by the domain rules at 30.
DECAY_FLOOR_ONCE_PRACTICED: Final[int] = 30

# --- Spaced-review intervals -------------------------------------------------

#: The interval from which a concept's first ``review_schedule`` entry computes
#: its ``due_at`` (current Clock time plus this interval) when the concept first
#: reaches Practiced (Req 9.1). A configurable business constant.
INITIAL_REVIEW_INTERVAL: Final[timedelta] = timedelta(days=7)

#: The next review interval set after a review is passed; strictly greater than
#: :data:`INITIAL_REVIEW_INTERVAL` and than a failed review's interval so a pass
#: lengthens the spacing (Req 9.4). A configurable business constant.
PASSED_REVIEW_INTERVAL: Final[timedelta] = timedelta(days=14)

#: The next review interval set after a review is failed; strictly less than
#: :data:`INITIAL_REVIEW_INTERVAL` so a failure shortens the spacing (Req 9.5).
#: A configurable business constant.
FAILED_REVIEW_INTERVAL: Final[timedelta] = timedelta(days=3)

#: The minimum number of eligible questions a concept's pool must contain before
#: a review can be scheduled or started; below this the review is declined
#: without creating or advancing a schedule entry (Req 28.1, 28.2, 28.3). Fixed
#: at 3 by the requirements.
MIN_ELIGIBLE_QUESTION_COUNT: Final[int] = 3

# --- Hints -------------------------------------------------------------------

#: The multiplier applied to an attempt's mastery gain when a hint for that
#: attempt's question was revealed before the attempt was submitted, tempering
#: the gain relative to a no-hint answer; never applied retroactively (Req 7.2,
#: 7.3, 7.4). A configurable business constant in the half-open range (0, 1);
#: 0.5 halves the gain, matching the illustrative +9-vs-+18 temper in the worked
#: mastery example (``docs/RULES-REVIEW.md`` D7 section 9).
HINT_MULTIPLIER: Final[float] = 0.5

# --- Rubric ------------------------------------------------------------------

#: The fraction of a rubric's ``max_points`` a project submission's weighted
#: score must meet or exceed, once the ``ProjectReviewer`` evaluation completes,
#: for the ``review_outcome`` to be recorded as passing (Req 10.1, 10.2, 10.3).
#: A configurable business constant in the inclusive range (0, 1]; 0.7 is a
#: conventional pass mark.
RUBRIC_PASS_THRESHOLD: Final[float] = 0.7

# --- Anti-farming ------------------------------------------------------------

#: The window, measured from an answer's ``first_answered_at`` via the injected
#: Clock, during which re-answering the same question earns zero additional
#: mastery (``counted_for_mastery`` false); at or after the boundary the
#: standard mastery rules apply (Req 6.1, 6.2, 6.4, 6.5;
#: ``.kiro/steering/domain-rules.md``). Fixed at 24 hours by the requirements.
ANTI_FARMING_WINDOW: Final[timedelta] = timedelta(hours=24)

# --- Mastery gains awarded by application use cases --------------------------
#
# The per-event point magnitudes the learning use cases award. They are the
# illustrative values of ``docs/RULES-REVIEW.md`` D7 section 9, promoted to named
# config so no use case hardcodes a business number (task 17.1).

#: Mastery points one counted, correct, un-hinted quiz answer earns each concept
#: its question covers. A hint revealed before submission tempers this by
#: :data:`HINT_MULTIPLIER` (Req 7.2); a re-answer inside
#: :data:`ANTI_FARMING_WINDOW` earns none (Req 6.1).
MASTERY_GAIN_PER_CORRECT_ANSWER: Final[int] = 18

#: Mastery points a passed spaced review earns its concept (Req 9.4). A failed
#: review earns none (Req 9.5).
REVIEW_PASSED_MASTERY_GAIN: Final[int] = 6

#: The mastery delta a hint reveal records on its ``mastery_event`` (Req 7.1).
#: Zero: revealing a hint costs nothing by itself -- the cost is the tempered
#: gain of the attempt that follows (Req 7.2) -- but the reveal is still audited
#: with exactly one event, and repeated reveals cannot farm mastery.
HINT_REVEAL_MASTERY_DELTA: Final[int] = 0

# --- Review sessions ----------------------------------------------------------

#: How many questions one spaced-review session presents, drawn from the
#: concept's eligible pool (Req 9.3). Never below
#: :data:`MIN_ELIGIBLE_QUESTION_COUNT`.
REVIEW_QUESTION_COUNT: Final[int] = 5

#: The review-session score (0-100, percent correct) at or above which a review
#: counts as passed (Req 9.4, 9.5).
REVIEW_PASS_SCORE: Final[int] = 70

# --- Projects ----------------------------------------------------------------

#: The minimum number of non-blank evidence items a project submission must carry
#: to be accepted (Req 20.2).
MIN_PROJECT_EVIDENCE_COUNT: Final[int] = 1

# --- Cursor pagination -------------------------------------------------------

#: How long an opaque pagination cursor stays usable after it is issued, measured
#: by the injected Clock; an older cursor is rejected as expired (Req 17.5).
CURSOR_TTL: Final[timedelta] = timedelta(hours=1)

#: The page size a list endpoint uses when the client does not ask for one (Req 17.5).
DEFAULT_PAGE_SIZE: Final[int] = 50

#: The largest page size a client may request from a list endpoint (Req 17.5).
MAX_PAGE_SIZE: Final[int] = 100


__all__ = [
    "ANTI_FARMING_WINDOW",
    "COOLDOWN_INTERVAL",
    "CURSOR_TTL",
    "DECAY_FLOOR_ONCE_PRACTICED",
    "DECAY_RATE_PER_DAY",
    "DEFAULT_PAGE_SIZE",
    "FAILED_REVIEW_INTERVAL",
    "HINT_MULTIPLIER",
    "HINT_REVEAL_MASTERY_DELTA",
    "INITIAL_REVIEW_INTERVAL",
    "MASTERY_GAIN_PER_CORRECT_ANSWER",
    "MASTERY_MAX",
    "MASTERY_MIN",
    "MASTERY_THRESHOLDS",
    "MAX_PAGE_SIZE",
    "MIN_ELIGIBLE_QUESTION_COUNT",
    "MIN_PROJECT_EVIDENCE_COUNT",
    "OAUTH_LINK_CONFIRMATION_WINDOW",
    "PASSED_REVIEW_INTERVAL",
    "PASSWORD_POLICY",
    "RESET_LINK_TTL",
    "REVIEW_PASSED_MASTERY_GAIN",
    "REVIEW_PASS_SCORE",
    "REVIEW_QUESTION_COUNT",
    "RUBRIC_PASS_THRESHOLD",
    "SESSION_DURATION",
    "SIGN_IN_RATE_LIMIT_THRESHOLD",
    "SIGN_IN_RATE_LIMIT_WINDOW",
    "VERIFICATION_LINK_TTL",
    "MasteryThresholds",
    "PasswordPolicy",
]
