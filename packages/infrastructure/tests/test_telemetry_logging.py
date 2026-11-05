"""Unit tests for JSON logging with redaction (kubernetes-deployment Req 13.8, 13.11)."""

from __future__ import annotations

import io
import json
import logging
from typing import cast

from edu_it_infrastructure.telemetry import (
    REDACTED,
    JsonLogFormatter,
    RedactionFilter,
    bind_lab_session_id,
    bind_request_id,
    configure_logging,
    current_lab_session_id,
    current_request_id,
    reset_lab_session_id,
    reset_request_id,
)


def _record(message: str = "hello", *args: object, **extra: object) -> logging.LogRecord:
    return logging.makeLogRecord(
        {
            "name": "edu_it.test",
            "levelname": "INFO",
            "levelno": logging.INFO,
            "msg": message,
            "args": args or None,
            **extra,
        }
    )


def _emit(record: logging.LogRecord) -> dict[str, object]:
    RedactionFilter().filter(record)
    line = JsonLogFormatter().format(record)
    assert "\n" not in line
    return cast("dict[str, object]", json.loads(line))


def test_record_is_single_line_json_with_core_fields() -> None:
    payload = _emit(_record("multi\nline message"))
    assert payload["message"] == "multi\nline message"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "edu_it.test"
    assert "timestamp" in payload


def test_request_id_present_only_when_bound() -> None:
    assert "request_id" not in _emit(_record())
    token = bind_request_id("req-1")
    try:
        assert _emit(_record())["request_id"] == "req-1"
    finally:
        reset_request_id(token)
    assert current_request_id() is None


def test_lab_session_id_present_only_when_bound() -> None:
    assert "lab_session_id" not in _emit(_record())
    token = bind_lab_session_id("lab-9")
    try:
        assert _emit(_record())["lab_session_id"] == "lab-9"
    finally:
        reset_lab_session_id(token)
    assert current_lab_session_id() is None


def test_correlation_ids_accepted_through_extra() -> None:
    payload = _emit(_record(request_id="req-x", lab_session_id="lab-x"))
    assert payload["request_id"] == "req-x"
    assert payload["lab_session_id"] == "lab-x"


def test_sensitive_fields_are_redacted() -> None:
    payload = _emit(
        _record(
            password="hunter2",
            api_token="abc123",
            terminal_data="rm -rf /",
            stdout="secret output",
            email="learner@example.org",
            learner_id="u-42",
            http_status=200,
        )
    )
    for key in ("password", "api_token", "terminal_data", "stdout", "email", "learner_id"):
        assert payload[key] == REDACTED
    assert payload["http_status"] == 200


def test_nested_mappings_are_redacted() -> None:
    payload = _emit(_record(context={"user": "ada", "step": 3, "headers": {"Authorization": "x"}}))
    assert payload["context"] == {
        "user": REDACTED,
        "step": 3,
        "headers": {"Authorization": REDACTED},
    }


def test_message_text_patterns_are_scrubbed() -> None:
    message = "login ada@example.org with password=hunter2 Bearer abcdefghij12345"
    text = str(_emit(_record(message))["message"])
    assert "ada@example.org" not in text
    assert "hunter2" not in text
    assert "abcdefghij12345" not in text


def test_message_args_are_scrubbed_after_interpolation() -> None:
    text = str(_emit(_record("user %s", "ada@example.org"))["message"])
    assert "ada@example.org" not in text


def test_jwt_cloud_key_and_pem_are_scrubbed() -> None:
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.sig"
    pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIabc\n-----END RSA PRIVATE KEY-----"
    text = str(_emit(_record(f"{jwt} AKIAABCDEFGHIJKLMNOP {pem}"))["message"])
    assert "eyJhbGci" not in text
    assert "AKIAABCDEFGHIJKLMNOP" not in text
    assert "MIIabc" not in text


def test_exception_text_is_scrubbed() -> None:
    try:
        raise ValueError("bad token=sekret for ada@example.org")
    except ValueError:
        record = _record("failed", exc_info=__import__("sys").exc_info())
    exception = str(_emit(record)["exception"])
    assert "ValueError" in exception
    assert "sekret" not in exception
    assert "ada@example.org" not in exception


def test_formatter_redacts_without_the_filter() -> None:
    payload = json.loads(JsonLogFormatter().format(_record("hi", password="x", email="a@b.co")))
    assert payload["password"] == REDACTED
    assert payload["email"] == REDACTED


def test_configure_logging_writes_redacted_json_lines() -> None:
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers, root.level
    try:
        configure_logging()
        stream = io.StringIO()
        handler = root.handlers[0]
        assert isinstance(handler, logging.StreamHandler)
        handler.setStream(stream)
        token = bind_request_id("req-7")
        try:
            logging.getLogger("edu_it.cfg").info("sign in", extra={"email": "a@b.co"})
        finally:
            reset_request_id(token)
        lines = stream.getvalue().splitlines()
        assert len(lines) == 1
        payload = json.loads(lines[0])
        assert payload["request_id"] == "req-7"
        assert payload["email"] == REDACTED
    finally:
        root.handlers = saved_handlers
        root.setLevel(saved_level)
