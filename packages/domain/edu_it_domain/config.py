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


__all__ = [
    "OAUTH_LINK_CONFIRMATION_WINDOW",
    "PASSWORD_POLICY",
    "RESET_LINK_TTL",
    "SESSION_DURATION",
    "SIGN_IN_RATE_LIMIT_THRESHOLD",
    "SIGN_IN_RATE_LIMIT_WINDOW",
    "VERIFICATION_LINK_TTL",
    "PasswordPolicy",
]
