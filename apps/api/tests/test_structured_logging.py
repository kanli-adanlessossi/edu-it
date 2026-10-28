"""Unit tests for structured JSON logging and Request_IDs (Req 18.4, 18.5, 18.6).

These cover the logging foundation directly (formatter output, Request_ID
resolution and generation, and context binding) without standing up an HTTP
client, so the test command stays dependency-light.
"""

from __future__ import annotations

import json
import logging

import pytest
from edu_it_api.logging import (
    JsonLogFormatter,
    bind_request_id,
    current_request_id,
    generate_request_id,
    reset_request_id,
    resolve_request_id,
)


def _record(message: str = "hello", **extra: object) -> logging.LogRecord:
    record = logging.makeLogRecord(
        {
            "name": "edu_it_api.test",
            "levelname": "INFO",
            "levelno": logging.INFO,
            "msg": message,
            **extra,
        }
    )
    return record


def test_formatter_emits_valid_json_with_core_fields() -> None:
    formatted = JsonLogFormatter().format(_record("structured"))
    payload = json.loads(formatted)
    assert payload["message"] == "structured"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "edu_it_api.test"
    assert "timestamp" in payload


def test_formatted_record_carries_bound_request_id() -> None:
    token = bind_request_id("req-abc")
    try:
        payload = json.loads(JsonLogFormatter().format(_record()))
    finally:
        reset_request_id(token)
    assert payload["request_id"] == "req-abc"


def test_formatted_record_omits_request_id_outside_a_request() -> None:
    # No Request_ID bound -> the field is absent rather than null.
    payload = json.loads(JsonLogFormatter().format(_record()))
    assert "request_id" not in payload


def test_formatter_merges_extra_structured_fields() -> None:
    payload = json.loads(JsonLogFormatter().format(_record(http_method="GET", http_status=200)))
    assert payload["http_method"] == "GET"
    assert payload["http_status"] == 200


def test_resolve_prefers_a_usable_inbound_request_id() -> None:
    assert resolve_request_id("inbound-123") == "inbound-123"
    assert resolve_request_id("  trimmed  ") == "trimmed"


@pytest.mark.parametrize("inbound", [None, "", "   ", "x" * 129])
def test_resolve_generates_when_inbound_is_missing_or_unusable(inbound: str | None) -> None:
    resolved = resolve_request_id(inbound)
    assert resolved
    assert resolved != inbound


def test_generated_request_ids_are_unique() -> None:
    assert generate_request_id() != generate_request_id()


def test_bind_and_reset_request_id_round_trip() -> None:
    assert current_request_id() is None
    token = bind_request_id("req-1")
    try:
        assert current_request_id() == "req-1"
    finally:
        reset_request_id(token)
    assert current_request_id() is None
