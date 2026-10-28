"""Unit tests for the FastAPI application factory and logging wiring."""

from __future__ import annotations

import logging

from edu_it_api.app import create_app
from edu_it_api.logging import JsonLogFormatter
from edu_it_api.middleware import RequestLoggingMiddleware


def test_create_app_registers_request_logging_middleware() -> None:
    app = create_app()
    registered = [getattr(m.cls, "__name__", "") for m in app.user_middleware]
    assert RequestLoggingMiddleware.__name__ in registered


def test_create_app_installs_the_json_formatter_on_the_root_logger() -> None:
    create_app()
    root = logging.getLogger()
    assert any(isinstance(h.formatter, JsonLogFormatter) for h in root.handlers)
