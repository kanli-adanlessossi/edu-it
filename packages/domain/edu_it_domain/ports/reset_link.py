"""The password ``ResetLink`` record and its repository port (Req 9.1-9.4, 25.3).

A :class:`ResetLink` is the transient, single-use, time-limited record a
password-reset request emails so a learner can set a new password. It is **not**
a Data_Model table (the data model migrated this phase carries no reset-link
entity, D3 Section 2); the design calls verification and reset links "ephemeral
and single-use / time-limited" whose TTL is the business constant
:data:`~edu_it_domain.config.RESET_LINK_TTL` (1 h, Req 9.1) and whose expiry is
measured by the injected ``Clock``. This keeps the link out of the fixed table
set while still giving the reset-request and reset-completion use cases a
framework-free contract to persist and look links up.

Like the identity repositories (ADR 0004) and the sibling Verification_Link port,
the store is expressed as a framework-free :class:`typing.Protocol`: the
application layer depends on the protocol, infrastructure supplies a concrete
(Redis-backed) implementation, and tests use an in-memory one. The link is
addressed only by its opaque single-use ``token``; the owning user is referenced
by its ``stable_id`` so the domain never sees an internal key.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

__all__ = [
    "ResetLink",
    "ResetLinkRepository",
]


@dataclass(frozen=True, slots=True)
class ResetLink:
    """A single-use, time-limited password-reset link (Req 9.1, 9.2, 25.3).

    The link is identified by an opaque :attr:`token` the reset email carries; it
    belongs to the user referenced by :attr:`user_stable_id`. It is valid only
    strictly before :attr:`expires_at` as read through the injected ``Clock``
    (the issuance instant plus :data:`~edu_it_domain.config.RESET_LINK_TTL`), and
    only while it has not been consumed. Once consumed, :attr:`consumed_at`
    records when, so a second use is rejected (single use, Req 9.2).
    """

    token: str
    """The opaque, single-use token the reset email carries."""

    user_stable_id: str
    """The ``stable_id`` of the user whose password the link resets."""

    issued_at: datetime
    """The instant (aware UTC) the link was issued, read from the Clock."""

    expires_at: datetime
    """The instant (aware UTC) the link expires: ``issued_at`` + the TTL."""

    consumed_at: datetime | None = None
    """The instant the link was consumed, or ``None`` while it is unused."""

    @property
    def is_consumed(self) -> bool:
        """Whether the link has already been used (and so cannot be reused)."""
        return self.consumed_at is not None

    def is_valid_at(self, instant: datetime) -> bool:
        """Return whether the link may be used at ``instant``.

        A link is usable only when it has not been consumed and ``instant`` is
        strictly before :attr:`expires_at` — i.e. it expires exactly at issuance
        plus the TTL as measured by the Clock (Req 9.1), and a consumed link is
        never valid (Req 9.2).
        """
        return not self.is_consumed and instant < self.expires_at


@runtime_checkable
class ResetLinkRepository(Protocol):
    """Port for persisting and consuming password-reset links (Req 9)."""

    def add(self, link: ResetLink) -> None:
        """Store ``link`` as a new, unused reset link.

        Raises :class:`ValueError` when a link with the same ``token`` already
        exists, so a token is never reused for two links.
        """
        ...

    def get(self, token: str) -> ResetLink | None:
        """Return the link for ``token``, or ``None`` when unrecognized.

        An unrecognized token yields ``None`` so the reset-completion use case can
        reject it as invalid without disclosing anything further (Req 9.3).
        """
        ...

    def mark_consumed(self, token: str, *, consumed_at: datetime) -> bool:
        """Mark the link for ``token`` consumed at ``consumed_at``.

        Returns whether an unused link was consumed: ``False`` when the token is
        unknown or the link was already consumed, so consumption is idempotent
        and single use is enforced at the store boundary (Req 9.2).
        """
        ...
