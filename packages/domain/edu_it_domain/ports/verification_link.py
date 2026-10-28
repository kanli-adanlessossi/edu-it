"""The email ``VerificationLink`` record and its repository port (Req 7.1-7.5).

A :class:`VerificationLink` is the transient, single-use, time-limited record a
sign-up emails so a visitor can confirm their email address. It is **not** a
Data_Model table (the data model migrated this phase carries no verification-link
entity, D3 Section 2); the design calls verification links "ephemeral and
single-use / time-limited" whose TTL is the business constant
:data:`~edu_it_domain.config.VERIFICATION_LINK_TTL` and whose expiry is measured
by the injected ``Clock`` (design, "Domain entities active this phase"). This
keeps the link out of the fixed table set while still giving the sign-up and
verification use cases a framework-free contract to persist and look links up.

Like the identity repositories (ADR 0004), the store is expressed as a
framework-free :class:`typing.Protocol`: the application layer depends on the
protocol, infrastructure supplies a concrete (Redis-backed) implementation, and
tests use an in-memory one. The link is addressed only by its opaque
single-use ``token``; the owning user is referenced by its ``stable_id`` so the
domain never sees an internal key.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

__all__ = [
    "VerificationLink",
    "VerificationLinkRepository",
]


@dataclass(frozen=True, slots=True)
class VerificationLink:
    """A single-use, time-limited email-verification link (Req 7.1, 7.3, 7.4).

    The link is identified by an opaque :attr:`token` the verification email
    carries; it belongs to the user referenced by :attr:`user_stable_id`. It is
    valid only strictly before :attr:`expires_at` as read through the injected
    ``Clock`` (the issuance instant plus
    :data:`~edu_it_domain.config.VERIFICATION_LINK_TTL`), and only while it has
    not been consumed. Once consumed, :attr:`consumed_at` records when, so a
    second use is rejected (single use, Req 7.4).
    """

    token: str
    """The opaque, single-use token the verification email carries."""

    user_stable_id: str
    """The ``stable_id`` of the user whose email the link confirms."""

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
        """Return whether the link may be opened at ``instant``.

        A link is openable only when it has not been consumed and ``instant`` is
        strictly before :attr:`expires_at` — i.e. it expires exactly at issuance
        plus the TTL as measured by the Clock (Req 7.3), and a consumed link is
        never valid (Req 7.4).
        """
        return not self.is_consumed and instant < self.expires_at


@runtime_checkable
class VerificationLinkRepository(Protocol):
    """Port for persisting and consuming email-verification links (Req 7)."""

    def add(self, link: VerificationLink) -> None:
        """Store ``link`` as a new, unused verification link.

        Raises :class:`ValueError` when a link with the same ``token`` already
        exists, so a token is never reused for two links.
        """
        ...

    def get(self, token: str) -> VerificationLink | None:
        """Return the link for ``token``, or ``None`` when unrecognized.

        An unrecognized token yields ``None`` so the verification use case can
        reject it as invalid without disclosing anything further (Req 7.5).
        """
        ...

    def mark_consumed(self, token: str, *, consumed_at: datetime) -> bool:
        """Mark the link for ``token`` consumed at ``consumed_at``.

        Returns whether an unused link was consumed: ``False`` when the token is
        unknown or the link was already consumed, so consumption is idempotent
        and single use is enforced at the store boundary (Req 7.4).
        """
        ...
