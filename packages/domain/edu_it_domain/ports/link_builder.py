"""The ``LinkBuilder`` port: absolute web URLs for emailed links (Req 8.3, 16.1).

The sign-up, resend, and request-reset use cases put a single-use token into an
email. Where that token points (the web origin and the locale path) is a
deployment concern, so the use cases ask this framework-free port for the
finished URL and never read configuration themselves. Infrastructure supplies
the concrete builder; tests supply a fake.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

__all__ = ["LinkBuilder"]


@runtime_checkable
class LinkBuilder(Protocol):
    """Port that builds the absolute URLs carried by verification and reset mail."""

    def verification_url(self, token: str, locale: str) -> str:
        """Return the absolute email-verification URL for ``token`` in ``locale``."""
        ...

    def reset_url(self, token: str, locale: str) -> str:
        """Return the absolute password-reset URL for ``token`` in ``locale``."""
        ...
