"""Repo-wide pytest fixtures.

``configure_logging`` (telemetry, task 10) installs a root handler carrying
``RedactionFilter``, which rewrites each ``LogRecord`` in place. Once any test
or app factory installs it, that handler stays on the root logger for the rest
of the session and scrubs records before pytest's ``caplog`` handler sees them,
so later tests that assert on raw text in ``caplog`` fail depending on test
order. This fixture keeps that global logging state from leaking between tests.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator

import pytest


def _is_redacting(handler: logging.Handler) -> bool:
    return any(type(f).__name__ == "RedactionFilter" for f in handler.filters)


def _strip_redacting_handlers() -> None:
    root = logging.getLogger()
    for handler in [h for h in root.handlers if _is_redacting(h)]:
        root.removeHandler(handler)


@pytest.fixture(autouse=True)
def _isolate_root_logging() -> Iterator[None]:
    """Run each test without a leaked redacting root handler, and drop any it installs."""
    root = logging.getLogger()
    level = root.level
    _strip_redacting_handlers()
    try:
        yield
    finally:
        _strip_redacting_handlers()
        root.setLevel(level)
