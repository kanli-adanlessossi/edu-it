"""Self-tests for the no-model-network pytest plugin and the provider-SDK boundary check.

Validates: ai-content Requirements 1.6, 2.5, 22.4.
"""

from __future__ import annotations

import socket
import subprocess
import sys
from pathlib import Path

import pytest
from pytest_no_model_network import NetworkBlockedError, is_loopback_host

REPO_ROOT = Path(__file__).resolve().parents[2]
BOUNDARY_SCRIPT = REPO_ROOT / "tools" / "check_domain_boundary.py"


@pytest.mark.network_guard_expected
def test_guard_trips_on_non_loopback_connect() -> None:
    with (
        socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock,
        pytest.raises(NetworkBlockedError),
    ):
        sock.connect(("203.0.113.7", 443))  # TEST-NET-3, never routable


@pytest.mark.network_guard_expected
def test_guard_trips_on_connect_ex_and_unresolved_host() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        with pytest.raises(NetworkBlockedError):
            sock.connect_ex(("198.51.100.1", 80))
        with pytest.raises(NetworkBlockedError):
            sock.connect(("api.model-vendor.example", 443))


def test_guard_fails_a_test_that_swallows_the_error() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            "-q",
            "-c",
            str(REPO_ROOT / "pyproject.toml"),
            "--rootdir",
            str(REPO_ROOT),
            "--no-cov",
            "-k",
            "swallow",
            str(Path(__file__).with_name("_network_guard_swallow_fixture.py")),
        ],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=False,
    )
    assert result.returncode != 0, result.stdout
    assert "no-model-network guard tripped" in result.stdout


@pytest.mark.parametrize("host", ["127.0.0.1", "127.5.5.5", "::1", "localhost", "::ffff:127.0.0.1"])
def test_loopback_hosts_allowed(host: str) -> None:
    assert is_loopback_host(host)


@pytest.mark.parametrize(
    "host", ["8.8.8.8", "10.0.0.5", "203.0.113.7", "example.com", "2001:db8::1"]
)
def test_non_loopback_hosts_blocked(host: str) -> None:
    assert not is_loopback_host(host)


def test_loopback_connect_is_not_blocked() -> None:
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        with socket.socket() as client:
            client.connect(server.getsockname())


@pytest.mark.parametrize(
    ("source", "expected_exit"),
    [
        ("import openai\n", 1),
        ("from anthropic import Anthropic\n", 1),
        ("import google.generativeai\n", 1),
        ("import boto3\n", 1),
        ("from edu_it_domain.ports import llm_provider\nimport json\n", 0),
    ],
)
def test_domain_boundary_rejects_provider_sdks(
    tmp_path: Path, source: str, expected_exit: int
) -> None:
    (tmp_path / "mod.py").write_text(source, encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(BOUNDARY_SCRIPT), str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == expected_exit, result.stderr
    if expected_exit:
        assert "mod.py" in result.stderr
