"""The ``PendingOAuthLink`` record and its repository port (Req 10.2, 10.6, 10.7, 26.2, 26.4, 26.5).

When a completed GitHub OAuth callback returns a *verified* email that matches an
**existing** local User, the identity is not linked outright: the design requires
an explicit, in-window confirmation step before the ``oauth_link`` is made (Req
10.2, 26.2). A :class:`PendingOAuthLink` is the transient record that holds that
proposed link while the visitor decides — the provider identity to link
(``provider`` + ``subject``), the existing User it would attach to, and the
confirmation window — addressed by an opaque single-use ``token`` the API hands
the temporary, unauthenticated session (Req 10.7).

It is **not** a Data_Model table (the migrated model carries no pending-link
entity, D3 Section 2); like verification and reset links the design calls OAuth
link-confirmation tokens "ephemeral and single-use / time-limited", with the TTL
the business constant
:data:`~edu_it_domain.config.OAUTH_LINK_CONFIRMATION_WINDOW` and expiry measured
by the injected ``Clock`` (design "Domain entities active this phase"). Keeping it
behind a framework-free :class:`typing.Protocol` lets the infrastructure layer
back it with Redis and tests use an in-memory one, exactly as for the reset and
verification links.

The state machine the use cases drive over this record is:

- **confirm within the window** → the link is made, the pending record is
  consumed (single use), and the User is signed in (Req 10.2, 26.2);
- **decline** → the pending record is invalidated *immediately* so the token can
  never be used again, and no link is made (Req 10.6, 26.4);
- **window expires before confirmation** → the record is no longer valid and no
  link is made, leaving the existing User's records unchanged (Req 10.6, 26.5).

Decline and expiry are distinct: a decline invalidates the token at once
(``mark_consumed``), whereas expiry is decided against the ``Clock`` by
:meth:`PendingOAuthLink.is_valid_at` without any state change. Both leave the
existing User and its links untouched.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

__all__ = [
    "PendingOAuthLink",
    "PendingOAuthLinkRepository",
]


@dataclass(frozen=True, slots=True)
class PendingOAuthLink:
    """A single-use, time-limited proposed OAuth link awaiting confirmation (Req 10.2, 26.2).

    The record is identified by an opaque :attr:`token` the temporary,
    unauthenticated session carries (Req 10.7). It names the provider identity the
    confirmation would link (:attr:`provider`, :attr:`subject`) and the existing
    local User it would attach to (:attr:`user_stable_id`). It is valid for
    confirmation only strictly before :attr:`expires_at` as read through the
    injected ``Clock`` (the issuance instant plus
    :data:`~edu_it_domain.config.OAUTH_LINK_CONFIRMATION_WINDOW`), and only while
    it has not been consumed. A decline consumes it immediately; a confirmation
    consumes it on success; once consumed, :attr:`consumed_at` records when, so a
    second use is rejected (single use, Req 26.4).
    """

    token: str
    """The opaque, single-use confirmation token the temporary session carries."""

    provider: str
    """The OAuth provider whose identity the confirmation would link (``github``)."""

    subject: str
    """The provider-assigned stable identifier for the external account."""

    user_stable_id: str
    """The ``stable_id`` of the existing User the confirmation would link to."""

    email: str
    """The verified GitHub email that matched the existing User (for display)."""

    issued_at: datetime
    """The instant (aware UTC) the confirmation step was presented, from the Clock."""

    expires_at: datetime
    """The instant (aware UTC) the window closes: ``issued_at`` + the window."""

    consumed_at: datetime | None = None
    """The instant the pending link was consumed (confirmed or declined), or ``None``."""

    @property
    def is_consumed(self) -> bool:
        """Whether the pending link has already been confirmed or declined."""
        return self.consumed_at is not None

    def is_valid_at(self, instant: datetime) -> bool:
        """Return whether the pending link may be confirmed at ``instant``.

        Confirmable only when it has not been consumed and ``instant`` is strictly
        before :attr:`expires_at` — i.e. the confirmation window closes exactly at
        issuance plus
        :data:`~edu_it_domain.config.OAUTH_LINK_CONFIRMATION_WINDOW` as measured by
        the Clock (Req 10.2, 26.5), and a consumed (confirmed or declined) record is
        never valid (Req 26.4).
        """
        return not self.is_consumed and instant < self.expires_at


@runtime_checkable
class PendingOAuthLinkRepository(Protocol):
    """Port for persisting and consuming pending OAuth link confirmations (Req 10.2)."""

    def add(self, pending: PendingOAuthLink) -> None:
        """Store ``pending`` as a new, unconsumed confirmation record.

        Raises :class:`ValueError` when a record already exists for the same
        ``token``, so a token is never reused for two pending links.
        """
        ...

    def get(self, token: str) -> PendingOAuthLink | None:
        """Return the pending link for ``token``, or ``None`` when unrecognized.

        An unrecognized token yields ``None`` so the confirm/decline use cases can
        reject it without disclosing anything further.
        """
        ...

    def mark_consumed(self, token: str, *, consumed_at: datetime) -> bool:
        """Mark the pending link for ``token`` consumed at ``consumed_at``.

        Returns whether an unconsumed record was consumed: ``False`` when the token
        is unknown or the record was already consumed, so consumption is idempotent
        and single use is enforced at the store boundary. Used by both confirmation
        (on success) and decline (immediately), so a declined token can never later
        be confirmed (Req 26.4).
        """
        ...
