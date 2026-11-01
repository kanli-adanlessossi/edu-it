"""Startup refusal for the served API (api-route-wiring Req 1.8, 8.6).

The API refuses to start, rather than serve a half-wired app, when a required adapter
cannot be built or ``WEB_BASE_URL`` is not an absolute ``http`` or ``https`` URL. Every
refusal is logged with the *name* of the failing adapter or variable and never with a
value, a connection URL, or an exception message, because those can carry secrets.
"""

from __future__ import annotations

import logging
from urllib.parse import urlsplit

__all__ = [
    "StartupRefused",
    "check_github_oauth_settings",
    "refuse_start",
    "validate_web_base_url",
]

_LOG = logging.getLogger("edu_it_api.startup")

WEB_BASE_URL_VARIABLE = "WEB_BASE_URL"


class StartupRefused(RuntimeError):
    """Raised when the API must not start; ``component`` names the failing part."""

    def __init__(self, component: str) -> None:
        super().__init__(f"refusing to start: {component}")
        self.component = component


def refuse_start(component: str, cause: BaseException | None = None) -> StartupRefused:
    """Log a critical record naming ``component`` and return the exception to raise."""
    _LOG.critical(
        "refusing to start: required component unavailable",
        extra={
            "adapter": component,
            "error_type": None if cause is None else type(cause).__name__,
        },
    )
    return StartupRefused(component)


def check_github_oauth_settings(
    client_id: str | None, client_secret: str | None, callback_url: str | None
) -> bool:
    """Return whether GitHub OAuth is configured (api-route-wiring Req 1.9, 1.10).

    ``True`` when all three values are non-empty, ``False`` when none is. When only some
    are set, log the *names* of the missing variables (never a value) and refuse to start.
    """
    named = (
        ("GITHUB_OAUTH_CLIENT_ID", client_id),
        ("GITHUB_OAUTH_CLIENT_SECRET", client_secret),
        ("GITHUB_OAUTH_CALLBACK_URL", callback_url),
    )
    missing = [name for name, value in named if not value]
    if not missing:
        return True
    if len(missing) == len(named):
        return False
    _LOG.critical(
        "refusing to start: incomplete GitHub OAuth configuration",
        extra={"adapter": "github_oauth", "missing_variables": missing},
    )
    raise StartupRefused(f"github_oauth: missing {', '.join(missing)}")


def validate_web_base_url(value: str) -> str:
    """Return ``value`` when it is an absolute http(s) URL, else refuse to start."""
    try:
        parsed = urlsplit(value)
        valid = parsed.scheme in ("http", "https") and bool(parsed.hostname)
    except ValueError:
        valid = False
    if not valid:
        raise refuse_start(WEB_BASE_URL_VARIABLE)
    return value
