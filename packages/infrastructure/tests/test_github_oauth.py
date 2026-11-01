"""Unit tests for the GitHub OAuth adapters and PKCE helpers (api-route-wiring 6.1, 6.2, 6.8)."""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import pytest
from edu_it_api.oauth import new_pkce_verifier, pkce_challenge
from edu_it_domain.config import OAUTH_STATE_TTL
from edu_it_domain.ports.github_oauth import GitHubIdentity
from edu_it_infrastructure.composition import CompositionSettings
from edu_it_infrastructure.fakes.github_oauth import Fake_GitHubOAuthClient
from edu_it_infrastructure.github_oauth import Urllib_GitHubOAuthClient


def test_pkce_lengths_and_rfc7636_vector() -> None:
    assert len(new_pkce_verifier()) == 86
    assert len(pkce_challenge(new_pkce_verifier())) == 43
    # RFC 7636 appendix B.
    assert (
        pkce_challenge("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk")
        == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
    )


def test_oauth_state_ttl_is_ten_minutes() -> None:
    assert OAUTH_STATE_TTL.total_seconds() == 600


def test_fake_accepts_matching_verifier_and_rejects_wrong_one() -> None:
    fake = Fake_GitHubOAuthClient()
    identity = GitHubIdentity(subject="1", email="a@example.test", email_verified=True)
    fake.register_code("c", identity)
    verifier = new_pkce_verifier()
    fake.authorize_url("s", pkce_challenge(verifier))
    assert fake.exchange("c", verifier) == identity
    assert fake.exchange("unknown", verifier) is None
    with pytest.raises(AssertionError):
        fake.exchange("c", new_pkce_verifier())


def test_adapter_authorize_url_carries_pkce_and_state() -> None:
    client = Urllib_GitHubOAuthClient("id", "secret", "http://localhost:8000/cb")
    query = parse_qs(urlsplit(client.authorize_url("st", "ch")).query)
    assert query["state"] == ["st"]
    assert query["code_challenge"] == ["ch"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["client_id"] == ["id"]
    assert "secret" not in client.authorize_url("st", "ch")


def test_settings_read_github_variables_and_treat_empty_as_unset() -> None:
    settings = CompositionSettings.from_env(
        {
            "GITHUB_OAUTH_CLIENT_ID": "id",
            "GITHUB_OAUTH_CLIENT_SECRET": "",
            "GITHUB_OAUTH_CALLBACK_URL": "http://x/cb",
        }
    )
    assert settings.github_client_id == "id"
    assert settings.github_client_secret is None
    assert settings.github_callback_url == "http://x/cb"
