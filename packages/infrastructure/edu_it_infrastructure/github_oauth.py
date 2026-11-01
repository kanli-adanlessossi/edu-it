"""The ``urllib`` GitHub OAuth adapter (api-route-wiring Req 6.1, 6.2, 6.8).

:class:`Urllib_GitHubOAuthClient` implements the domain
:class:`~edu_it_domain.ports.github_oauth.GitHubOAuthClient` port with
``urllib.request`` only (no new dependency). Every request has a 10 second
timeout. A denial or any failure of the exchange returns ``None``; no error
carries or logs the client secret, the code, or a token.
"""

from __future__ import annotations

import json
import logging
import urllib.request
from typing import Any
from urllib.parse import urlencode

from edu_it_domain.ports.github_oauth import GitHubIdentity, GitHubOAuthClient

__all__ = ["Urllib_GitHubOAuthClient"]

_LOGGER = logging.getLogger(__name__)

_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
_TOKEN_URL = "https://github.com/login/oauth/access_token"
_USER_URL = "https://api.github.com/user"
_EMAILS_URL = "https://api.github.com/user/emails"
_SCOPE = "read:user user:email"
_TIMEOUT_SECONDS = 10


class Urllib_GitHubOAuthClient(GitHubOAuthClient):
    """A :class:`GitHubOAuthClient` that talks to github.com with ``urllib``."""

    def __init__(self, client_id: str, client_secret: str, callback_url: str) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._callback_url = callback_url

    def authorize_url(self, state: str, code_challenge: str) -> str:
        """Build the GitHub authorization URL with ``state`` and an S256 challenge."""
        query = urlencode(
            {
                "client_id": self._client_id,
                "redirect_uri": self._callback_url,
                "scope": _SCOPE,
                "state": state,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
            }
        )
        return f"{_AUTHORIZE_URL}?{query}"

    def exchange(self, code: str, code_verifier: str) -> GitHubIdentity | None:
        """Exchange the code for an identity; ``None`` on denial or any failure."""
        try:
            token_response = self._request_json(
                _TOKEN_URL,
                body={
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "code": code,
                    "redirect_uri": self._callback_url,
                    "code_verifier": code_verifier,
                },
            )
            token = token_response.get("access_token")
            if not isinstance(token, str) or not token:
                return None
            user = self._request_json(_USER_URL, token=token)
            emails = self._request_json(_EMAILS_URL, token=token)
            subject = user.get("id")
            if subject is None or not isinstance(emails, list):
                return None
            primary = next(
                (
                    entry
                    for entry in emails
                    if isinstance(entry, dict)
                    and entry.get("primary") is True
                    and isinstance(entry.get("email"), str)
                ),
                None,
            )
            if primary is None:
                return None
            return GitHubIdentity(
                subject=str(subject),
                email=primary["email"],
                email_verified=primary.get("verified") is True,
            )
        except Exception as exc:
            # Log the exception type only: messages may echo request details.
            _LOGGER.warning("github oauth exchange failed: %s", type(exc).__name__)
            return None

    @staticmethod
    def _request_json(
        url: str, *, body: dict[str, str] | None = None, token: str | None = None
    ) -> Any:
        headers = {"Accept": "application/json", "User-Agent": "edu-it"}
        data: bytes | None = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(url, data=data, headers=headers)
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8"))
