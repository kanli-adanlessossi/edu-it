"""Property-based test for request-log JSON and Request_ID (task 8.7).

Feature: foundations, Property 27: Every request log record is JSON and carries a Request_ID

Property 27 (design.md): *For any* request, with or without an inbound
Request_ID, the emitted request log record parses as JSON and carries a
non-empty Request_ID, and one is generated when none is supplied.

The request-logging path resolves a Request_ID from the inbound header value
(``resolve_request_id``), binds it into the logging context (``bind_request_id``),
and emits records through ``JsonLogFormatter``. This property exercises that
path end to end over arbitrary inbound header values -- present and usable,
absent, blank, or over-length -- and checks the two universal guarantees: the
emitted record is valid JSON and carries a non-empty ``request_id``, and when no
usable inbound Request_ID is supplied a fresh one is generated.

Specific examples, the formatter's core fields, and context round-tripping are
covered by the example/edge-case unit tests in ``test_structured_logging.py``.

Validates: Requirements 18.4, 18.5

Uses Hypothesis, the pinned Python property-based testing library
(docs/adr/0001-versions.md).
"""

from __future__ import annotations

import json
import logging

from edu_it_api.logging import (
    JsonLogFormatter,
    bind_request_id,
    reset_request_id,
    resolve_request_id,
)
from hypothesis import given, settings
from hypothesis import strategies as st

# The maximum accepted length of a usable inbound Request_ID (see
# ``edu_it_api.logging._MAX_REQUEST_ID_LENGTH``). An inbound value is "usable"
# when, after trimming, it is non-blank and no longer than this.
_MAX_REQUEST_ID_LENGTH = 128


def _is_usable_inbound(inbound: str | None) -> bool:
    """Mirror the resolver's rule for a usable inbound Request_ID (Req 18.4)."""
    if inbound is None:
        return False
    trimmed = inbound.strip()
    return bool(trimmed) and len(trimmed) <= _MAX_REQUEST_ID_LENGTH


# "With or without an inbound Request_ID": arbitrary header values the resolver
# must handle. ``None`` models an absent header; text() spans the usable case
# (short non-blank strings), the blank case (whitespace/empty), and -- via the
# length bound -- the over-long case the resolver rejects.
_inbound_request_ids = st.one_of(
    st.none(),
    st.text(max_size=160),
    # Whitespace-only values exercise the blank-after-trim rejection branch.
    st.text(alphabet=" \t\n", max_size=8),
)


def _emit_request_record(request_id: str) -> dict[str, object]:
    """Resolve+bind ``request_id`` and format an access record, as the request path does."""
    token = bind_request_id(request_id)
    try:
        record = logging.makeLogRecord(
            {
                "name": "edu_it_api.request",
                "levelname": "INFO",
                "levelno": logging.INFO,
                "msg": "request.handled",
            }
        )
        formatted = JsonLogFormatter().format(record)
    finally:
        reset_request_id(token)
    payload: dict[str, object] = json.loads(formatted)
    return payload


@given(inbound=_inbound_request_ids)
@settings(max_examples=200)
def test_property_27_request_log_is_json_and_carries_request_id(inbound: str | None) -> None:
    """Feature: foundations, Property 27: Every request log record is JSON and carries a Request_ID.

    For any inbound header value, the request's Request_ID resolves (Req 18.4,
    18.5), and the record emitted while it is bound parses as JSON and carries a
    non-empty ``request_id``. When the inbound value is not usable, the carried
    Request_ID is freshly generated rather than the inbound value (Req 18.5).
    """
    request_id = resolve_request_id(inbound)

    # The resolved Request_ID is always non-empty.
    assert request_id

    # The emitted record parses as JSON (json.loads in the helper would raise
    # otherwise) and carries a non-empty Request_ID (Req 18.4).
    payload = _emit_request_record(request_id)
    assert isinstance(payload, dict)
    assert isinstance(payload["request_id"], str)
    assert payload["request_id"]
    assert payload["request_id"] == request_id

    if _is_usable_inbound(inbound):
        # A usable inbound Request_ID is carried through, trimmed (Req 18.4).
        assert inbound is not None
        assert payload["request_id"] == inbound.strip()
    else:
        # No usable inbound Request_ID -> one is generated (Req 18.5): the
        # carried id is not the (unusable) inbound value.
        assert payload["request_id"] != inbound
