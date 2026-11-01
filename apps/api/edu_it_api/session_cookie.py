"""Session_Cookie and ``csrf_token`` cookie helpers (api-route-wiring Req 2.1-2.4).

The session cookie is ``HttpOnly``; the ``csrf_token`` cookie deliberately is not,
because the double-submit scheme needs scripts to read it. Both use
``SameSite=Lax`` and ``Path=/``, are ``Secure`` only in production, and carry the
configured ``Domain`` when one is set, so a Web_App on a sibling subdomain can read
the token. Cookie attributes come from :class:`CookieSettings`, never from the
request.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Final

from edu_it_infrastructure.composition import Environment
from starlette.responses import Response

from edu_it_api.csrf import CSRF_COOKIE_NAME
from edu_it_api.terminal import SESSION_COOKIE_NAME

__all__ = [
    "CookieSettings",
    "expire_session_cookie",
    "set_csrf_cookie",
    "set_session_cookie",
]

_SAME_SITE: Final = "lax"
_PATH: Final[str] = "/"


@dataclass(frozen=True, slots=True)
class CookieSettings:
    """Cookie attributes derived from configuration."""

    environment: Environment
    domain: str | None
    max_age: timedelta

    @property
    def secure(self) -> bool:
        """``Secure`` is set only in production."""
        return self.environment.is_production


def set_session_cookie(response: Response, ref: str, settings: CookieSettings) -> None:
    """Set the Session_Cookie carrying the opaque ``ref``."""
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=ref,
        max_age=int(settings.max_age.total_seconds()),
        path=_PATH,
        domain=settings.domain,
        secure=settings.secure,
        httponly=True,
        samesite=_SAME_SITE,
    )


def expire_session_cookie(response: Response, settings: CookieSettings) -> None:
    """Expire the Session_Cookie with the same scoping attributes it was set with."""
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        path=_PATH,
        domain=settings.domain,
        secure=settings.secure,
        httponly=True,
        samesite=_SAME_SITE,
    )


def set_csrf_cookie(response: Response, token: str, settings: CookieSettings) -> None:
    """Set the script-readable ``csrf_token`` cookie (not ``HttpOnly``)."""
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=token,
        max_age=int(settings.max_age.total_seconds()),
        path=_PATH,
        domain=settings.domain,
        secure=settings.secure,
        httponly=False,
        samesite=_SAME_SITE,
    )
