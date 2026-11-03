"""Pytest plugin: no test may reach a model (or any non-loopback host) over the network.

ai-content Requirements 1.6, 2.5 and 22.4. Tests run against the Replay_Provider and the
fake only, so any socket connection to a non-loopback address is a bug: it would mean a test
reached a real model endpoint (or any external service).

The plugin patches ``socket.socket.connect`` and ``connect_ex``. A connection to a
non-loopback IP address raises ``NetworkBlockedError`` and is also recorded, so the test is
failed even if the code under test swallows the exception. Unix-domain sockets and loopback
(``127.0.0.0/8``, ``::1``, ``localhost``) stay allowed, which keeps the mocked OAuth, Docker
and local-service tests working.

Enabled through ``addopts`` in the root ``pyproject.toml`` (``-p pytest_no_model_network``).
A test that deliberately exercises the guard carries ``@pytest.mark.network_guard_expected``.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Generator
from typing import Any

import pytest

_LOOPBACK_NAMES = frozenset({"localhost", "localhost.localdomain", ""})

_original_connect = socket.socket.connect
_original_connect_ex = socket.socket.connect_ex
_violations: list[str] = []


class NetworkBlockedError(OSError):
    """Raised when a test tries to connect to a non-loopback address."""


def is_loopback_host(host: object) -> bool:
    """True when ``host`` is a loopback IP address or name."""
    if isinstance(host, bytes):
        host = host.decode("ascii", "replace")
    if not isinstance(host, str):
        return False
    if host.lower() in _LOOPBACK_NAMES:
        return True
    try:
        addr = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return False  # an unresolved host name is not provably loopback
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    return addr.is_loopback


def _check(sock: socket.socket, address: Any) -> None:
    if sock.family not in (socket.AF_INET, socket.AF_INET6):
        return  # AF_UNIX and friends never leave the machine
    host = address[0] if isinstance(address, tuple) and address else address
    if is_loopback_host(host):
        return
    message = f"network access blocked in tests: connect to {address!r}"
    _violations.append(message)
    raise NetworkBlockedError(message)


def _guarded_connect(self: socket.socket, address: Any) -> None:
    _check(self, address)
    _original_connect(self, address)


def _guarded_connect_ex(self: socket.socket, address: Any) -> int:
    _check(self, address)
    return _original_connect_ex(self, address)


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "network_guard_expected: the test deliberately trips the no-model-network guard",
    )
    socket.socket.connect = _guarded_connect  # type: ignore[method-assign,assignment]
    socket.socket.connect_ex = _guarded_connect_ex  # type: ignore[method-assign,assignment]


def pytest_unconfigure(config: pytest.Config) -> None:
    socket.socket.connect = _original_connect  # type: ignore[method-assign]
    socket.socket.connect_ex = _original_connect_ex  # type: ignore[method-assign]


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item: pytest.Item) -> None:
    _violations.clear()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo[None]
) -> Generator[None, Any, None]:
    outcome = yield
    if call.when != "call" or not _violations:
        return
    if item.get_closest_marker("network_guard_expected") is not None:
        return
    report = outcome.get_result()
    if report.passed:
        report.outcome = "failed"
        report.longrepr = "no-model-network guard tripped:\n" + "\n".join(_violations)
