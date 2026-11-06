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

from collections.abc import Mapping
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

#: How long the authorize-time ``state`` and PKCE ``code_verifier`` cookie stays
#: valid; it is the ``Max-Age`` of that cookie (api-route-wiring Req 6.1).
OAUTH_STATE_TTL: Final[timedelta] = timedelta(minutes=10)

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

# --- Password hashing (scrypt) -----------------------------------------------

#: scrypt CPU/memory cost ``N`` (a power of two). With ``r = 8`` this uses about
#: 16 MiB, within the ``hashlib.scrypt`` default memory limit. Stored hashes carry
#: their own parameters, so raising it later does not invalidate old hashes.
PASSWORD_HASH_SCRYPT_N: Final[int] = 2**14

#: scrypt block size ``r``.
PASSWORD_HASH_SCRYPT_R: Final[int] = 8

#: scrypt parallelization ``p``.
PASSWORD_HASH_SCRYPT_P: Final[int] = 1

#: Length of the random per-password salt, in bytes.
PASSWORD_HASH_SALT_BYTES: Final[int] = 16

#: Length of the derived key, in bytes.
PASSWORD_HASH_KEY_BYTES: Final[int] = 32


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

# =============================================================================
# Hands-on-labs business numbers (hands-on-labs spec)
# =============================================================================
#
# Every tunable number of the lab, sandbox, terminal, grading, and retention
# features lives here and nowhere else (design "Config additions"). Durations
# are ``timedelta``; sizes are bytes; counts are ``int``. Values marked "fixed"
# come from the requirements or steering; the rest are initial values for
# review. ``validate_lab_config`` (task 1.3) checks these at startup.

_KIB: Final[int] = 1024
_MIB: Final[int] = 1024 * 1024

# --- Lab session lifetime ----------------------------------------------------

#: Maximum lifetime of a Lab_Session from creation (Req 3.3).
LAB_SESSION_TTL: Final[timedelta] = timedelta(minutes=60)

#: Inactivity after which a Lab_Session is expired (fixed, Req 3.3).
LAB_IDLE_TIMEOUT: Final[timedelta] = timedelta(minutes=15)

# --- Grading -----------------------------------------------------------------

#: Minimum spacing between accepted grading requests per session (fixed).
LAB_GRADING_MIN_INTERVAL: Final[timedelta] = timedelta(seconds=10)

#: Total wall-clock deadline of a grading run from acceptance (fixed).
LAB_GRADING_RUN_DEADLINE: Final[timedelta] = timedelta(seconds=15)

#: Total time allowed to verify the learner processes are frozen.
LAB_GRADING_FREEZE_BOUND: Final[timedelta] = timedelta(seconds=1)

#: Freeze verification attempts within :data:`LAB_GRADING_FREEZE_BOUND`.
LAB_GRADING_FREEZE_ATTEMPTS: Final[int] = 5

#: Time reserved at the end of a run for teardown and thawing.
LAB_GRADING_TEARDOWN_RESERVE: Final[timedelta] = timedelta(seconds=2)

#: The time actually available for running Checks. Derived, never stored
#: separately: deadline - freeze bound - teardown reserve (15 - 1 - 2 = 12 s).
USABLE_RUN_BUDGET: Final[timedelta] = (
    LAB_GRADING_RUN_DEADLINE - LAB_GRADING_FREEZE_BOUND - LAB_GRADING_TEARDOWN_RESERVE
)


def usable_run_budget(
    run_deadline: timedelta = LAB_GRADING_RUN_DEADLINE,
    freeze_bound: timedelta = LAB_GRADING_FREEZE_BOUND,
    teardown_reserve: timedelta = LAB_GRADING_TEARDOWN_RESERVE,
) -> timedelta:
    """Return ``run_deadline - freeze_bound - teardown_reserve`` (Usable_Run_Budget)."""

    return run_deadline - freeze_bound - teardown_reserve


#: Default timeout for a single Check that declares none.
LAB_CHECK_TIMEOUT_DEFAULT: Final[timedelta] = timedelta(seconds=5)

#: Poll interval for grading results (client and SSE loop).
LAB_RESULT_POLL_INTERVAL: Final[timedelta] = timedelta(milliseconds=500)

# --- Sandbox provider --------------------------------------------------------

#: Bound on every call to the sandbox backend (Docker daemon).
SANDBOX_OPERATION_TIMEOUT: Final[timedelta] = timedelta(seconds=10)

#: Origins allowed to open the terminal WebSocket. Defaults to the existing
#: configured web origin (``WEB_BASE_URL`` default); composition may override
#: it from settings. Must be non-empty.
LAB_ALLOWED_WEB_ORIGINS: Final[tuple[str, ...]] = ("http://localhost:3000",)

# --- Provisioning ------------------------------------------------------------

#: Retries after the first failed provisioning attempt (fixed); attempts = 3.
LAB_PROVISION_MAX_RETRIES: Final[int] = 2

#: Minimum spacing between provisioning attempts of one session (fixed).
LAB_PROVISION_MIN_INTERVAL: Final[timedelta] = timedelta(seconds=10)

#: Timeout of one provisioning attempt.
LAB_PROVISION_TIMEOUT: Final[timedelta] = timedelta(seconds=30)

#: Target time for a session to become ready.
LAB_PROVISION_READY_TARGET: Final[timedelta] = timedelta(seconds=20)

#: Lab starts allowed per user within :data:`LAB_PROVISION_RATE_WINDOW`.
LAB_PROVISION_RATE_LIMIT: Final[int] = 5

#: Trailing window of :data:`LAB_PROVISION_RATE_LIMIT`.
LAB_PROVISION_RATE_WINDOW: Final[timedelta] = timedelta(minutes=10)

# --- Sandbox resource limits -------------------------------------------------

#: CPU limit of a Sandbox, in CPUs.
SANDBOX_CPU_LIMIT: Final[int] = 1

#: Memory limit of a Sandbox, in bytes (512 MiB).
SANDBOX_MEMORY_LIMIT: Final[int] = 512 * _MIB

#: Process-count limit of a Sandbox.
SANDBOX_PID_LIMIT: Final[int] = 128

#: Total writable quota of a Sandbox, in bytes (256 MiB).
SANDBOX_WRITABLE_QUOTA: Final[int] = 256 * _MIB

#: Split of the writable quota (bytes) across /workspace, /home/learner, /tmp,
#: /grader. The parts sum exactly to :data:`SANDBOX_WRITABLE_QUOTA`.
SANDBOX_QUOTA_WORKSPACE: Final[int] = 160 * _MIB
SANDBOX_QUOTA_HOME: Final[int] = 32 * _MIB
SANDBOX_QUOTA_TMP: Final[int] = 32 * _MIB
SANDBOX_QUOTA_GRADER: Final[int] = 32 * _MIB

#: Memory limit for a learner program run inside a Sandbox, in bytes (128 MiB).
LAB_PROGRAM_MEMORY_LIMIT: Final[int] = 128 * _MIB

#: Process limit for a learner program run inside a Sandbox.
LAB_PROGRAM_PROC_LIMIT: Final[int] = 32

# --- Periodic jobs and retention ---------------------------------------------

#: Interval of the session reaper job.
LAB_REAPER_INTERVAL: Final[timedelta] = timedelta(seconds=30)

#: Interval of the provisioning sweep job.
LAB_SWEEP_INTERVAL: Final[timedelta] = timedelta(seconds=5)

#: How long lab data is retained before purge (proposed, to confirm).
LAB_RETENTION_PERIOD: Final[timedelta] = timedelta(days=90)

#: Interval of the retention purge job.
LAB_RETENTION_INTERVAL: Final[timedelta] = timedelta(hours=24)

# --- Terminal gateway --------------------------------------------------------

#: Interval between server pings on a terminal connection.
TERMINAL_PING_INTERVAL: Final[timedelta] = timedelta(seconds=20)

#: Missed pongs after which a terminal connection is closed.
TERMINAL_MAX_MISSED_PONGS: Final[int] = 2

#: Largest accepted terminal message, in bytes (64 KiB).
TERMINAL_MAX_MESSAGE_BYTES: Final[int] = 64 * _KIB

#: Terminal message rate limit, messages per second.
TERMINAL_MAX_MESSAGES_PER_SECOND: Final[int] = 1000

#: How often the gateway re-checks the session status.
TERMINAL_STATUS_POLL_INTERVAL: Final[timedelta] = timedelta(seconds=2)

#: Minimum spacing of ``last_activity_at`` writes.
LAB_ACTIVITY_WRITE_INTERVAL: Final[timedelta] = timedelta(seconds=5)

#: Client reconnect delays (1, 2, 4, 8 s); the last repeats until
#: :data:`LAB_RECONNECT_WINDOW` has passed.
LAB_RECONNECT_BACKOFF: Final[tuple[timedelta, ...]] = (
    timedelta(seconds=1),
    timedelta(seconds=2),
    timedelta(seconds=4),
    timedelta(seconds=8),
)

#: Total time the client keeps reconnecting before giving up.
LAB_RECONNECT_WINDOW: Final[timedelta] = timedelta(seconds=60)

# --- Kubernetes deployment ---------------------------------------------------

#: Bound on destroying a sandbox and waiting until it is gone (kubernetes Req 6.3).
LAB_DESTROY_TIMEOUT: Final[timedelta] = timedelta(seconds=30)

#: Delay before a capacity-rejected provisioning request is retried (Req 22.6).
#: Must be at least :data:`LAB_PROVISION_MIN_INTERVAL`.
LAB_PROVISION_CAPACITY_BACKOFF: Final[timedelta] = timedelta(seconds=10)

#: Hard ceiling on waiting for a Lab_Pod to become Ready (Req 1.5); the provider
#: waits ``min(LAB_PROVISION_TIMEOUT, K8S_READY_CEILING)``.
K8S_READY_CEILING: Final[timedelta] = timedelta(seconds=60)

#: CPU (millicores) of the ``owner`` helper container of a Lab_Pod (tar and chmod only);
#: requests equal limits (ADR 0009, layout B).
K8S_HELPER_CPU_MILLI: Final[int] = 50

#: Memory (bytes) of the ``owner`` helper container of a Lab_Pod (16 MiB); requests equal limits.
K8S_HELPER_MEMORY_BYTES: Final[int] = 16 * _MIB

#: CPU (millicores) of the ``grader`` container, where every Check runs; requests equal limits.
#: In Docker a Check gets the whole Sandbox (1 CPU, 512 MiB, the learner being frozen). At the
#: helper size (50 m, 16 MiB) an abusive Check livelocks its cgroup in memory reclaim under CPU
#: throttling, so not even ``timeout`` or SIGKILL can end it (ADR 0009, checkpoint 7).
K8S_GRADER_CPU_MILLI: Final[int] = 500

#: Memory (bytes) of the ``grader`` container (256 MiB): the PID limit, not memory, stops a fork
#: bomb, and the code-lab harness fits with its 128 MiB per-case address-space limit.
K8S_GRADER_MEMORY_BYTES: Final[int] = 256 * _MIB

#: Bound on closing terminals after a lab node drain or failure (Req 19.1).
LAB_NODE_LOSS_TERMINAL_CLOSE_BOUND: Final[timedelta] = timedelta(seconds=10)

#: Bound on marking a session ended-by-infrastructure after detecting the loss
#: (Req 19.2, 19.3).
LAB_INFRA_LOSS_MARK_BOUND: Final[timedelta] = timedelta(seconds=30)

#: Bound on removing an evicted pod object after detecting the eviction (Req 19.5).
LAB_EVICTED_POD_REMOVAL_BOUND: Final[timedelta] = timedelta(seconds=60)

# Reconnect schedules (Req 14.5, 14.6, 20.1, 20.2).

#: ``drain`` schedule, used after a reconnect message: delays between attempts.
TERMINAL_DRAIN_RECONNECT_DELAYS: Final[tuple[timedelta, ...]] = (
    timedelta(seconds=1),
    timedelta(seconds=2),
    timedelta(seconds=4),
    timedelta(seconds=8),
    timedelta(seconds=16),
    timedelta(seconds=30),
    timedelta(seconds=30),
    timedelta(seconds=30),
    timedelta(seconds=30),
    timedelta(seconds=30),
)

#: Most attempts of the ``drain`` schedule.
TERMINAL_DRAIN_RECONNECT_MAX_ATTEMPTS: Final[int] = 10

#: No ``drain`` delay exceeds this cap.
TERMINAL_DRAIN_RECONNECT_CAP: Final[timedelta] = timedelta(seconds=30)

#: The first ``drain`` attempt starts within this time.
TERMINAL_DRAIN_RECONNECT_FIRST_WITHIN: Final[timedelta] = timedelta(seconds=2)

#: ``restart`` schedule, used after an abrupt close: delays between attempts;
#: the last delay repeats until :data:`TERMINAL_RESTART_RECONNECT_TOTAL` has passed.
TERMINAL_RESTART_RECONNECT_DELAYS: Final[tuple[timedelta, ...]] = (
    timedelta(seconds=1),
    timedelta(seconds=2),
    timedelta(seconds=4),
    timedelta(seconds=5),
)

#: Largest gap between two ``restart`` attempts.
TERMINAL_RESTART_RECONNECT_MAX_INTERVAL: Final[timedelta] = timedelta(seconds=5)

#: The ``restart`` schedule stops after this total time.
TERMINAL_RESTART_RECONNECT_TOTAL: Final[timedelta] = timedelta(seconds=60)

# Api drain (Req 14.2, 14.12).

#: Default drain period of the Api, in seconds.
API_DRAIN_PERIOD_DEFAULT_SECONDS: Final[int] = 30

#: Allowed range of the drain period, in seconds (inclusive).
API_DRAIN_PERIOD_BOUNDS_SECONDS: Final[tuple[int, int]] = (5, 300)

#: Default preStop delay before the drain starts, in seconds.
API_PRESTOP_DELAY_DEFAULT_SECONDS: Final[int] = 5

#: Allowed range of the preStop delay, in seconds (inclusive).
API_PRESTOP_DELAY_BOUNDS_SECONDS: Final[tuple[int, int]] = (0, 60)

#: Margin added on top of drain plus preStop delay for the pod's
#: terminationGracePeriodSeconds, in seconds (Requirement 14.4).
API_TERMINATION_GRACE_MARGIN_SECONDS: Final[int] = 10

# Worker shutdown (Req 14.10, 14.11).

#: Seconds before ``terminationGracePeriodSeconds`` elapses at which the Worker re-queues
#: every job that is still in flight and exits.
WORKER_SHUTDOWN_MARGIN_SECONDS: Final[int] = 5

#: ``terminationGracePeriodSeconds`` assumed when the Chart does not pass one to the Worker.
WORKER_TERMINATION_GRACE_DEFAULT_SECONDS: Final[int] = 60

# --- Learner files, warnings, progression ------------------------------------

#: Maximum number of learner files per save.
LAB_FILE_MAX_COUNT: Final[int] = 10

#: Maximum size of one learner file, in bytes (64 KiB).
LAB_FILE_MAX_BYTES: Final[int] = 64 * _KIB

#: Time before expiry at which the learner is warned.
LAB_WARNING_THRESHOLD: Final[timedelta] = timedelta(minutes=2)

#: Mastery points per first-passed lab task per covered concept. A hint
#: tempers it by :data:`HINT_MULTIPLIER` (reused, not duplicated).
LAB_TASK_MASTERY_GAIN: Final[int] = 15

# =============================================================================
# AI-content business numbers (ai-content spec)
# =============================================================================
#
# Every tunable number of the AI content features lives here and nowhere else
# (design "Config additions"). Values marked "fixed" come from the requirements
# or design decision D6; the rest are initial Config values for the user to
# confirm. ``validate_ai_config`` checks these at startup.

# --- LLM call attempts -------------------------------------------------------

#: Retries after the first failed model call (fixed, D6, Req 1.3).
LLM_MAX_RETRIES: Final[int] = 2

#: Total attempts per model call: the first try plus the retries (fixed).
LLM_MAX_ATTEMPTS: Final[int] = 1 + LLM_MAX_RETRIES

#: Timeout of one model call attempt.
LLM_ATTEMPT_TIMEOUT: Final[timedelta] = timedelta(seconds=60)

# --- Budgets (cents) ---------------------------------------------------------

#: Daily generation budget, in cents (initial Config value).
AI_BUDGET_DAILY_CENTS: Final[int] = 2500

#: Generation budget per track, in cents (initial Config value).
AI_BUDGET_PER_TRACK_CENTS: Final[int] = 5000

#: Cost cap of the Live_Run, in cents (fixed, Req 24.1).
LIVE_RUN_COST_CAP_CENTS: Final[int] = 2000

# --- Variant pool and cycles -------------------------------------------------

#: Pool size below which a variant refill is needed (fixed, glossary).
VARIANT_POOL_THRESHOLD: Final[int] = 3

#: Largest number of variants generated by one refill.
VARIANTS_PER_REFILL_MAX: Final[int] = 3

#: Interval of the variant refill cycle.
REFILL_CYCLE_INTERVAL: Final[timedelta] = timedelta(hours=1)

#: Interval of the item quality cycle.
QUALITY_CYCLE_INTERVAL: Final[timedelta] = timedelta(minutes=5)

#: Interval of the path advisor cycle.
ADVISOR_CYCLE_INTERVAL: Final[timedelta] = timedelta(hours=24)

#: Number of concepts in a path recommendation.
PATH_RECOMMENDATION_LENGTH: Final[int] = 5

# --- Problem reports ---------------------------------------------------------

#: Number of reports that flags an item (fixed, glossary).
REPORT_THRESHOLD: Final[int] = 3

#: Maximum characters of a problem report comment.
PROBLEM_REPORT_COMMENT_MAX_CHARS: Final[int] = 1000

#: Allowed problem report categories.
PROBLEM_REPORT_CATEGORIES: Final[tuple[str, ...]] = (
    "incorrect",
    "unclear",
    "translation",
    "outdated",
    "technical",
    "other",
)

# --- Item quality statistics -------------------------------------------------

#: Samples an item needs before its statistics can flag it.
ITEM_STAT_MIN_SAMPLES: Final[int] = 20

#: Acceptable (low, high) correct rate of an item.
ITEM_CORRECT_RATE_BOUNDS: Final[tuple[float, float]] = (0.10, 0.98)

#: Acceptable (low, high) average answer time of an item, in milliseconds.
ITEM_AVG_TIME_BOUNDS_MS: Final[tuple[int, int]] = (3000, 300000)

#: Largest elapsed time counted for one answer, in milliseconds.
ITEM_ELAPSED_MS_MAX: Final[int] = 600000

# --- Generated-content checks ------------------------------------------------

#: Acceptable (low, high) readability score.
READABILITY_BOUNDS: Final[tuple[float, float]] = (20.0, 80.0)

#: Largest accepted 5-word shingle containment against the source corpus.
SIMILARITY_LIMIT: Final[float] = 0.25

# --- Retention ---------------------------------------------------------------

#: Retention of item observations (answers not yet folded or kept for audit).
AI_RETENTION_OBSERVATION: Final[timedelta] = timedelta(days=90)

#: Retention of problem reports.
AI_RETENTION_PROBLEM_REPORT: Final[timedelta] = timedelta(days=365)

#: Retention of path recommendations.
AI_RETENTION_RECOMMENDATION: Final[timedelta] = timedelta(days=90)

#: Retention of AI audit records.
AI_RETENTION_AUDIT: Final[timedelta] = timedelta(days=90)

#: Interval of the AI retention purge job.
AI_RETENTION_INTERVAL: Final[timedelta] = timedelta(hours=24)


class AiConfigError(ValueError):
    """Raised when the AI Config is invalid; ``keys`` names the offending keys."""

    def __init__(self, keys: tuple[str, ...], message: str) -> None:
        super().__init__(message)
        self.keys: tuple[str, ...] = keys


_AI_MISSING: Final[object] = object()

#: Keys that must be present and strictly positive (int, float or timedelta).
_AI_POSITIVE_KEYS: Final[tuple[str, ...]] = (
    "LLM_MAX_RETRIES",
    "LLM_MAX_ATTEMPTS",
    "LLM_ATTEMPT_TIMEOUT",
    "AI_BUDGET_DAILY_CENTS",
    "AI_BUDGET_PER_TRACK_CENTS",
    "LIVE_RUN_COST_CAP_CENTS",
    "VARIANT_POOL_THRESHOLD",
    "VARIANTS_PER_REFILL_MAX",
    "REFILL_CYCLE_INTERVAL",
    "QUALITY_CYCLE_INTERVAL",
    "ADVISOR_CYCLE_INTERVAL",
    "PATH_RECOMMENDATION_LENGTH",
    "REPORT_THRESHOLD",
    "PROBLEM_REPORT_COMMENT_MAX_CHARS",
    "ITEM_STAT_MIN_SAMPLES",
    "ITEM_ELAPSED_MS_MAX",
    "SIMILARITY_LIMIT",
    "AI_RETENTION_OBSERVATION",
    "AI_RETENTION_PROBLEM_REPORT",
    "AI_RETENTION_RECOMMENDATION",
    "AI_RETENTION_AUDIT",
    "AI_RETENTION_INTERVAL",
)

#: Keys holding an ordered (low, high) pair with 0 <= low < high.
_AI_BOUNDS_KEYS: Final[tuple[str, ...]] = (
    "ITEM_CORRECT_RATE_BOUNDS",
    "ITEM_AVG_TIME_BOUNDS_MS",
    "READABILITY_BOUNDS",
)

#: Every AI Config key that :func:`validate_ai_config` checks.
AI_CONFIG_KEYS: Final[tuple[str, ...]] = (
    *_AI_POSITIVE_KEYS,
    *_AI_BOUNDS_KEYS,
    "PROBLEM_REPORT_CATEGORIES",
)


def _ai_get(config: object, key: str) -> object:
    if isinstance(config, Mapping):
        return config.get(key, _AI_MISSING)
    return getattr(config, key, _AI_MISSING)


def _ai_is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _ai_is_positive(value: object) -> bool:
    if isinstance(value, timedelta):
        return value > timedelta(0)
    return _ai_is_number(value) and value > 0  # type: ignore[operator]


def _ai_bounds_valid(value: object) -> bool:
    if not isinstance(value, (tuple, list)) or len(value) != 2:
        return False
    low, high = value
    return _ai_is_number(low) and _ai_is_number(high) and 0 <= low < high


def validate_ai_config(config: object) -> None:
    """Return normally for a valid AI Config, else raise :class:`AiConfigError`.

    ``config`` is a mapping of key to value or an object exposing the keys as
    attributes (for example this module). Offending keys are: every missing or
    non-positive key; every bounds pair that is not ordered ``0 <= low < high``;
    empty or non-string ``PROBLEM_REPORT_CATEGORIES``; ``LLM_MAX_ATTEMPTS``
    (and ``LLM_MAX_RETRIES``) when attempts differ from ``1 + retries``; and
    ``LIVE_RUN_COST_CAP_CENTS`` (and ``AI_BUDGET_DAILY_CENTS``) when the cap
    exceeds the daily budget. Retentions must be positive.
    """
    offending: list[str] = []

    def add(*keys: str) -> None:
        for key in keys:
            if key not in offending:
                offending.append(key)

    for key in _AI_POSITIVE_KEYS:
        if not _ai_is_positive(_ai_get(config, key)):
            add(key)
    for key in _AI_BOUNDS_KEYS:
        if not _ai_bounds_valid(_ai_get(config, key)):
            add(key)
    categories = _ai_get(config, "PROBLEM_REPORT_CATEGORIES")
    if not (
        isinstance(categories, (tuple, list))
        and len(categories) > 0
        and all(isinstance(c, str) and c != "" for c in categories)
    ):
        add("PROBLEM_REPORT_CATEGORIES")

    attempts = _ai_get(config, "LLM_MAX_ATTEMPTS")
    retries = _ai_get(config, "LLM_MAX_RETRIES")
    if (
        _ai_is_number(attempts) and _ai_is_number(retries) and attempts != 1 + retries  # type: ignore[operator]
    ):
        add("LLM_MAX_ATTEMPTS", "LLM_MAX_RETRIES")

    cap = _ai_get(config, "LIVE_RUN_COST_CAP_CENTS")
    daily = _ai_get(config, "AI_BUDGET_DAILY_CENTS")
    if _ai_is_number(cap) and _ai_is_number(daily) and cap > daily:  # type: ignore[operator]
        add("LIVE_RUN_COST_CAP_CENTS", "AI_BUDGET_DAILY_CENTS")

    if offending:
        keys = tuple(offending)
        raise AiConfigError(keys, "invalid AI Config: " + ", ".join(keys))


# --- Trace export (kubernetes-deployment Req 13.1, 13.2) ---------------------

#: Most times one batch of spans is handed to the collector exporter; after the last failed
#: attempt the batch is dropped (Req 13.2). Fixed at 3 by the requirements.
TRACE_EXPORT_MAX_ATTEMPTS: Final[int] = 3

#: Time one export attempt may take before it counts as failed. Together with
#: :data:`TRACE_EXPORT_RETRY_BACKOFF` it bounds the work spent on one batch, off the request path.
TRACE_EXPORT_ATTEMPT_TIMEOUT: Final[timedelta] = timedelta(seconds=2)

#: Pause between two export attempts of the same batch (background thread only).
TRACE_EXPORT_RETRY_BACKOFF: Final[timedelta] = timedelta(milliseconds=200)

#: Spans buffered for export. When the queue is full new spans are dropped, so a slow or
#: unreachable collector can never grow memory or block a request or a job (Req 13.2).
TRACE_QUEUE_MAX_SPANS: Final[int] = 2048

#: Most spans sent in one export call.
TRACE_EXPORT_BATCH_MAX_SPANS: Final[int] = 512

#: Delay between two background export runs.
TRACE_EXPORT_SCHEDULE_DELAY: Final[timedelta] = timedelta(seconds=5)


__all__ = [
    "ADVISOR_CYCLE_INTERVAL",
    "AI_BUDGET_DAILY_CENTS",
    "AI_BUDGET_PER_TRACK_CENTS",
    "AI_CONFIG_KEYS",
    "AI_RETENTION_AUDIT",
    "AI_RETENTION_INTERVAL",
    "AI_RETENTION_OBSERVATION",
    "AI_RETENTION_PROBLEM_REPORT",
    "AI_RETENTION_RECOMMENDATION",
    "ANTI_FARMING_WINDOW",
    "API_DRAIN_PERIOD_BOUNDS_SECONDS",
    "API_DRAIN_PERIOD_DEFAULT_SECONDS",
    "API_PRESTOP_DELAY_BOUNDS_SECONDS",
    "API_PRESTOP_DELAY_DEFAULT_SECONDS",
    "API_TERMINATION_GRACE_MARGIN_SECONDS",
    "COOLDOWN_INTERVAL",
    "CURSOR_TTL",
    "DECAY_FLOOR_ONCE_PRACTICED",
    "DECAY_RATE_PER_DAY",
    "DEFAULT_PAGE_SIZE",
    "FAILED_REVIEW_INTERVAL",
    "HINT_MULTIPLIER",
    "HINT_REVEAL_MASTERY_DELTA",
    "INITIAL_REVIEW_INTERVAL",
    "ITEM_AVG_TIME_BOUNDS_MS",
    "ITEM_CORRECT_RATE_BOUNDS",
    "ITEM_ELAPSED_MS_MAX",
    "ITEM_STAT_MIN_SAMPLES",
    "K8S_GRADER_CPU_MILLI",
    "K8S_GRADER_MEMORY_BYTES",
    "K8S_HELPER_CPU_MILLI",
    "K8S_HELPER_MEMORY_BYTES",
    "K8S_READY_CEILING",
    "LAB_ACTIVITY_WRITE_INTERVAL",
    "LAB_ALLOWED_WEB_ORIGINS",
    "LAB_CHECK_TIMEOUT_DEFAULT",
    "LAB_DESTROY_TIMEOUT",
    "LAB_EVICTED_POD_REMOVAL_BOUND",
    "LAB_FILE_MAX_BYTES",
    "LAB_FILE_MAX_COUNT",
    "LAB_GRADING_FREEZE_ATTEMPTS",
    "LAB_GRADING_FREEZE_BOUND",
    "LAB_GRADING_MIN_INTERVAL",
    "LAB_GRADING_RUN_DEADLINE",
    "LAB_GRADING_TEARDOWN_RESERVE",
    "LAB_IDLE_TIMEOUT",
    "LAB_INFRA_LOSS_MARK_BOUND",
    "LAB_NODE_LOSS_TERMINAL_CLOSE_BOUND",
    "LAB_PROGRAM_MEMORY_LIMIT",
    "LAB_PROGRAM_PROC_LIMIT",
    "LAB_PROVISION_CAPACITY_BACKOFF",
    "LAB_PROVISION_MAX_RETRIES",
    "LAB_PROVISION_MIN_INTERVAL",
    "LAB_PROVISION_RATE_LIMIT",
    "LAB_PROVISION_RATE_WINDOW",
    "LAB_PROVISION_READY_TARGET",
    "LAB_PROVISION_TIMEOUT",
    "LAB_REAPER_INTERVAL",
    "LAB_RECONNECT_BACKOFF",
    "LAB_RECONNECT_WINDOW",
    "LAB_RESULT_POLL_INTERVAL",
    "LAB_RETENTION_INTERVAL",
    "LAB_RETENTION_PERIOD",
    "LAB_SESSION_TTL",
    "LAB_SWEEP_INTERVAL",
    "LAB_TASK_MASTERY_GAIN",
    "LAB_WARNING_THRESHOLD",
    "LIVE_RUN_COST_CAP_CENTS",
    "LLM_ATTEMPT_TIMEOUT",
    "LLM_MAX_ATTEMPTS",
    "LLM_MAX_RETRIES",
    "MASTERY_GAIN_PER_CORRECT_ANSWER",
    "MASTERY_MAX",
    "MASTERY_MIN",
    "MASTERY_THRESHOLDS",
    "MAX_PAGE_SIZE",
    "MIN_ELIGIBLE_QUESTION_COUNT",
    "MIN_PROJECT_EVIDENCE_COUNT",
    "OAUTH_LINK_CONFIRMATION_WINDOW",
    "OAUTH_STATE_TTL",
    "PASSED_REVIEW_INTERVAL",
    "PASSWORD_HASH_KEY_BYTES",
    "PASSWORD_HASH_SALT_BYTES",
    "PASSWORD_HASH_SCRYPT_N",
    "PASSWORD_HASH_SCRYPT_P",
    "PASSWORD_HASH_SCRYPT_R",
    "PASSWORD_POLICY",
    "PATH_RECOMMENDATION_LENGTH",
    "PROBLEM_REPORT_CATEGORIES",
    "PROBLEM_REPORT_COMMENT_MAX_CHARS",
    "QUALITY_CYCLE_INTERVAL",
    "READABILITY_BOUNDS",
    "REFILL_CYCLE_INTERVAL",
    "REPORT_THRESHOLD",
    "RESET_LINK_TTL",
    "REVIEW_PASSED_MASTERY_GAIN",
    "REVIEW_PASS_SCORE",
    "REVIEW_QUESTION_COUNT",
    "RUBRIC_PASS_THRESHOLD",
    "SANDBOX_CPU_LIMIT",
    "SANDBOX_MEMORY_LIMIT",
    "SANDBOX_OPERATION_TIMEOUT",
    "SANDBOX_PID_LIMIT",
    "SANDBOX_QUOTA_GRADER",
    "SANDBOX_QUOTA_HOME",
    "SANDBOX_QUOTA_TMP",
    "SANDBOX_QUOTA_WORKSPACE",
    "SANDBOX_WRITABLE_QUOTA",
    "SESSION_DURATION",
    "SIGN_IN_RATE_LIMIT_THRESHOLD",
    "SIGN_IN_RATE_LIMIT_WINDOW",
    "SIMILARITY_LIMIT",
    "TERMINAL_DRAIN_RECONNECT_CAP",
    "TERMINAL_DRAIN_RECONNECT_DELAYS",
    "TERMINAL_DRAIN_RECONNECT_FIRST_WITHIN",
    "TERMINAL_DRAIN_RECONNECT_MAX_ATTEMPTS",
    "TERMINAL_MAX_MESSAGES_PER_SECOND",
    "TERMINAL_MAX_MESSAGE_BYTES",
    "TERMINAL_MAX_MISSED_PONGS",
    "TERMINAL_PING_INTERVAL",
    "TERMINAL_RESTART_RECONNECT_DELAYS",
    "TERMINAL_RESTART_RECONNECT_MAX_INTERVAL",
    "TERMINAL_RESTART_RECONNECT_TOTAL",
    "TERMINAL_STATUS_POLL_INTERVAL",
    "TRACE_EXPORT_ATTEMPT_TIMEOUT",
    "TRACE_EXPORT_BATCH_MAX_SPANS",
    "TRACE_EXPORT_MAX_ATTEMPTS",
    "TRACE_EXPORT_RETRY_BACKOFF",
    "TRACE_EXPORT_SCHEDULE_DELAY",
    "TRACE_QUEUE_MAX_SPANS",
    "USABLE_RUN_BUDGET",
    "VARIANTS_PER_REFILL_MAX",
    "VARIANT_POOL_THRESHOLD",
    "VERIFICATION_LINK_TTL",
    "WORKER_SHUTDOWN_MARGIN_SECONDS",
    "WORKER_TERMINATION_GRACE_DEFAULT_SECONDS",
    "AiConfigError",
    "MasteryThresholds",
    "PasswordPolicy",
    "usable_run_budget",
    "validate_ai_config",
]
