"""The ``GitHubOAuthClient`` port (api-route-wiring Req 6.1, 6.2, 6.8).

The GitHub OAuth handshake (the authorization redirect and the code exchange)
leaves the domain through this port. Production uses a ``urllib`` adapter; tests
use a fake that opens no socket. The port is PKCE-aware: the authorize URL
carries an S256 ``code_challenge`` and the exchange presents the matching
``code_verifier``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

__all__ = ["GitHubIdentity", "GitHubOAuthClient"]


@dataclass(frozen=True, slots=True)
class GitHubIdentity:
    """The GitHub account identity returned by a successful code exchange."""

    subject: str
    """GitHub's stable account identifier."""

    email: str
    """The primary email GitHub returned for the account."""

    email_verified: bool
    """Whether GitHub reports that email as verified."""


@runtime_checkable
class GitHubOAuthClient(Protocol):
    """Port for the GitHub OAuth authorization-code handshake with PKCE."""

    def authorize_url(self, state: str, code_challenge: str) -> str:
        """Return the GitHub authorization URL with ``state`` and an S256 ``code_challenge``."""
        ...

    def exchange(self, code: str, code_verifier: str) -> GitHubIdentity | None:
        """Exchange ``code`` and ``code_verifier`` for the account identity.

        Returns ``None`` on denial or any failure; never raises for a failed
        exchange, and never includes a secret in an error.
        """
        ...
