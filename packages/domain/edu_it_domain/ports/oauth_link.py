"""The ``OAuthLink`` record and its repository port (Req 10.1, 10.2, 10.4).

An :class:`OAuthLink` is the durable tie between an external OAuth identity
(GitHub in this phase) and a local :class:`~edu_it_domain.ports.repository.UserRecord`.
It is the ``oauth_link`` entity of the data model's identity group (D3 Section 2,
design "Domain entities active this phase"): a ``provider`` (GitHub) plus the
provider-assigned ``subject`` — the stable, opaque identifier GitHub hands back
for the account — mapped to the local user's ``stable_id``. The GitHub email is
*not* the link key: GitHub lets a user change their primary email, so the
``subject`` is the durable identity and the email is only used to decide, at
link time, whether an existing local User already owns that address (Req 10.1,
10.2).

Like the identity repositories (ADR 0004) and the sibling link ports, this is a
framework-free :class:`typing.Protocol`: the application layer depends on the
protocol, the infrastructure layer supplies a PostgreSQL implementation, and
tests use an in-memory one. The link is addressed by the ``(provider, subject)``
pair a completed OAuth callback returns; the owning user is referenced by its
``stable_id`` so the domain never sees an internal key.

A single ``(provider, subject)`` pair maps to at most one User, so a returning
visitor whose identity already has a link signs in the linked User without a
duplicate account being created (Req 10.4); :meth:`OAuthLinkRepository.add`
rejects a second link for the same pair to enforce that at the store boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Protocol, runtime_checkable

__all__ = [
    "PROVIDER_GITHUB",
    "OAuthLink",
    "OAuthLinkRepository",
]

#: The only OAuth provider this phase integrates (Req 10). GitHub is the
#: external identity provider; the constant names the ``provider`` field of an
#: :class:`OAuthLink` so callers and tests never spell the literal.
PROVIDER_GITHUB: Final[str] = "github"


@dataclass(frozen=True, slots=True)
class OAuthLink:
    """A durable link between a GitHub identity and a local User (Req 10.1, 10.4).

    The link is identified by the ``(provider, subject)`` pair a completed OAuth
    callback returns — ``subject`` being the provider-assigned, stable account
    identifier, not the email — and names the local user it authenticates by
    :attr:`user_stable_id`. One pair maps to at most one User, so an existing
    link signs in that User without creating a duplicate (Req 10.4).
    """

    provider: str
    """The OAuth provider (``github`` this phase)."""

    subject: str
    """The provider-assigned stable identifier for the external account."""

    user_stable_id: str
    """The ``stable_id`` of the local user this identity authenticates."""


@runtime_checkable
class OAuthLinkRepository(Protocol):
    """Port for persisting and resolving durable OAuth identity links (Req 10)."""

    def add(self, link: OAuthLink) -> None:
        """Store ``link`` as a new durable OAuth link.

        Raises :class:`ValueError` when a link already exists for the same
        ``(provider, subject)`` pair, so one external identity never maps to two
        local Users and a duplicate account cannot be created (Req 10.4).
        """
        ...

    def get(self, provider: str, subject: str) -> OAuthLink | None:
        """Return the link for ``(provider, subject)``, or ``None`` when absent.

        A returning visitor whose identity already has a link resolves here so
        the use case signs in the linked User directly (Req 10.4); an unknown
        pair yields ``None`` so the use case treats the identity as new or as an
        email-conflict candidate.
        """
        ...
