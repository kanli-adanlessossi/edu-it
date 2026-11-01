"""``Web_LinkBuilder``: absolute Web_App URLs for emailed links (Req 8.1, 8.3, 8.4, 8.5).

The verification URL is ``{WEB_BASE_URL}/{locale}/verify-email?token=<token>``.
The token is percent-encoded with no safe characters, so any token round trips
through :func:`urllib.parse.parse_qs`. The base URL is injected; this module
reads no environment variable. The reset URL is
``{WEB_BASE_URL}/{locale}/reset-password?token=<token>`` (Req 8.2).
"""

from __future__ import annotations

from urllib.parse import quote

__all__ = ["Web_LinkBuilder"]


class Web_LinkBuilder:
    """Build links that open Web_App pages."""

    def __init__(self, web_base_url: str) -> None:
        self._base = web_base_url.rstrip("/")

    def verification_url(self, token: str, locale: str) -> str:
        """Return the absolute email-verification URL for ``token`` in ``locale``."""
        return f"{self._base}/{locale}/verify-email?token={quote(token, safe='')}"

    def reset_url(self, token: str, locale: str) -> str:
        """Return the absolute password-reset URL for ``token`` in ``locale``."""
        return f"{self._base}/{locale}/reset-password?token={quote(token, safe='')}"
