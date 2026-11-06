"""The browser-origin allowlist follows WEB_BASE_URL (kubernetes-deployment E2E).

A deployed Web_App is not on ``http://localhost:3000``; with a fixed allowlist the Api would
refuse its CORS calls and every terminal WebSocket (``Origin`` check).
"""

from __future__ import annotations

import pytest
from edu_it_domain.config import LAB_ALLOWED_WEB_ORIGINS
from edu_it_infrastructure.composition import CompositionSettings, web_origin


@pytest.mark.parametrize(
    ("url", "origin"),
    [
        ("https://learn.example.org", "https://learn.example.org"),
        ("https://Learn.Example.org/", "https://learn.example.org"),
        ("https://learn.example.org:443/fr", "https://learn.example.org"),
        ("http://localhost:80", "http://localhost"),
        ("https://edu-it.localtest.me:18443", "https://edu-it.localtest.me:18443"),
        ("http://localhost:3000", "http://localhost:3000"),
        ("http://[::1]:3000", "http://[::1]:3000"),
        ("ftp://learn.example.org", None),
        ("/relative", None),
        ("https://host:notaport", None),
    ],
)
def test_web_origin(url: str, origin: str | None) -> None:
    assert web_origin(url) == origin


def test_default_settings_keep_the_localhost_default() -> None:
    assert CompositionSettings.from_env({}).allowed_web_origins == LAB_ALLOWED_WEB_ORIGINS


def test_allowed_origins_follow_web_base_url() -> None:
    settings = CompositionSettings.from_env({"WEB_BASE_URL": "https://edu-it.localtest.me:18443"})
    assert settings.allowed_web_origins == ("https://edu-it.localtest.me:18443",)
    assert settings.web_base_url == "https://edu-it.localtest.me:18443"


def test_an_explicit_allowlist_wins() -> None:
    settings = CompositionSettings(
        web_base_url="https://learn.example.org",
        allowed_web_origins=("https://a.example", "https://b.example"),
    )
    assert settings.allowed_web_origins == ("https://a.example", "https://b.example")


def test_an_invalid_web_base_url_is_still_refused() -> None:
    with pytest.raises(ValueError, match="web_base_url"):
        CompositionSettings.from_env({"WEB_BASE_URL": "ftp://learn.example.org"})
