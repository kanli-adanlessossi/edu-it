"""The shared session-to-user resolver for authenticated routes (api-route-wiring Req 1.3, 1.4).

One :class:`SessionUserResolver` instance is handed to the lab, learning, list,
content, and account routers, so no two routers can disagree about who the caller
is. It reads the opaque Session_Cookie, resolves it against the server-side
:class:`~edu_it_domain.ports.session_store.SessionStore` (ADR 0004), and raises
:class:`NotAuthenticated` when the cookie is missing, unknown, expired, or revoked.
``errors/accounts.py`` maps that exception to a translated ``401`` Problem_Details.
"""

from __future__ import annotations

from dataclasses import dataclass

from edu_it_domain.ports.session_store import SessionStore
from starlette.requests import Request

from edu_it_api.terminal import SESSION_COOKIE_NAME

__all__ = ["NotAuthenticated", "SessionUserResolver"]


class NotAuthenticated(Exception):
    """Raised when a request carries no active Session (maps to ``401``)."""

    def __init__(self) -> None:
        super().__init__("request is not authenticated")


@dataclass(frozen=True, slots=True)
class SessionUserResolver:
    """Resolve a request's Session_Cookie to the owning user's ``stable_id``."""

    session_store: SessionStore
    cookie_name: str = SESSION_COOKIE_NAME

    def ref(self, request: Request) -> str | None:
        """Return the presented session reference, or ``None`` when absent or empty."""
        return request.cookies.get(self.cookie_name) or None

    def __call__(self, request: Request) -> str:
        """Return the authenticated user's ``stable_id`` or raise :class:`NotAuthenticated`."""
        ref = self.ref(request)
        resolved = None if ref is None else self.session_store.resolve(ref)
        if resolved is None:
            raise NotAuthenticated
        return resolved.user_stable_id
