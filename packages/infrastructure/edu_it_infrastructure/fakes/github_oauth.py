"""The ``GitHubOAuthClient`` fake (api-route-wiring Req 6.1, 6.2, 6.8).

:class:`Fake_GitHubOAuthClient` opens no socket. It records the ``code_challenge``
handed to :meth:`authorize_url` and, on :meth:`exchange`, asserts that the
received ``code_verifier`` hashes (unpadded base64url SHA-256) to that
challenge, so a test fails if a route sends the wrong verifier.
"""

from __future__ import annotations

import base64
import hashlib
from urllib.parse import urlencode

from edu_it_domain.ports.github_oauth import GitHubIdentity, GitHubOAuthClient

__all__ = ["Fake_GitHubOAuthClient"]


class Fake_GitHubOAuthClient(GitHubOAuthClient):
    """A scripted :class:`GitHubOAuthClient` with PKCE verification."""

    __test__ = False

    def __init__(self, base_url: str = "https://github.test/login/oauth/authorize") -> None:
        self._base_url = base_url
        self._identities: dict[str, GitHubIdentity] = {}
        self._challenges: dict[str, str] = {}
        self._last_challenge: str | None = None
        self.exchanged_codes: list[str] = []

    def register_code(self, code: str, identity: GitHubIdentity) -> None:
        """Make ``code`` exchange successfully for ``identity``."""
        self._identities[code] = identity

    def authorize_url(self, state: str, code_challenge: str) -> str:
        """Return a fake authorize URL and remember ``code_challenge``."""
        self._challenges[state] = code_challenge
        self._last_challenge = code_challenge
        query = urlencode(
            {"state": state, "code_challenge": code_challenge, "code_challenge_method": "S256"}
        )
        return f"{self._base_url}?{query}"

    def exchange(self, code: str, code_verifier: str) -> GitHubIdentity | None:
        """Assert the verifier matches the issued challenge; return the scripted identity."""
        assert self._last_challenge is not None, "exchange called before authorize_url"
        digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
        computed = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
        assert computed == self._last_challenge, "code_verifier does not hash to the code_challenge"
        self.exchanged_codes.append(code)
        return self._identities.get(code)
