"""Fixture run in a subprocess by test_no_model_network_guard (not collected directly)."""

import socket


def test_swallow() -> None:
    try:
        with socket.socket() as sock:
            sock.connect(("203.0.113.7", 443))
    except OSError:
        pass  # code under test hides the error; the guard must still fail the test
