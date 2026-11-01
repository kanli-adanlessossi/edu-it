"""Conditional GitHub OAuth wiring and its startup checks (api-route-wiring Req 1.9, 1.10)."""

from __future__ import annotations

import logging

import pytest
from edu_it_api.composition import build_adapters
from edu_it_api.startup import StartupRefused
from edu_it_infrastructure.composition import CompositionSettings
from edu_it_infrastructure.fakes.github_oauth import Fake_GitHubOAuthClient
from edu_it_infrastructure.github_oauth import Urllib_GitHubOAuthClient

_ALL = {
    "GITHUB_OAUTH_CLIENT_ID": "id-value",
    "GITHUB_OAUTH_CLIENT_SECRET": "secret-value",
    "GITHUB_OAUTH_CALLBACK_URL": "http://localhost:8000/v1/auth/github/callback",
}


def test_none_set_leaves_oauth_off() -> None:
    settings = CompositionSettings.from_env({"GITHUB_OAUTH_CLIENT_ID": ""})
    assert build_adapters(settings, fakes=True).oauth_client is None
    assert build_adapters(settings, fakes=False).oauth_client is None


def test_all_set_turns_oauth_on() -> None:
    settings = CompositionSettings.from_env(_ALL)
    assert isinstance(build_adapters(settings, fakes=True).oauth_client, Fake_GitHubOAuthClient)
    assert isinstance(build_adapters(settings, fakes=False).oauth_client, Urllib_GitHubOAuthClient)


def test_partial_configuration_refuses_and_logs_names_not_values(
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = CompositionSettings.from_env({"GITHUB_OAUTH_CLIENT_ID": "id-value"})
    with caplog.at_level(logging.CRITICAL), pytest.raises(StartupRefused):
        build_adapters(settings, fakes=True)
    text = " ".join(f"{r.getMessage()} {r.__dict__}" for r in caplog.records)
    assert "GITHUB_OAUTH_CLIENT_SECRET" in text and "GITHUB_OAUTH_CALLBACK_URL" in text
    assert "id-value" not in text


def test_force_oauth_uses_the_fake_whatever_the_settings() -> None:
    adapters = build_adapters(CompositionSettings(), fakes=True, force_oauth=True)
    assert isinstance(adapters.oauth_client, Fake_GitHubOAuthClient)
