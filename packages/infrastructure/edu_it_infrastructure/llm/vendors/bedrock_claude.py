"""Anthropic Claude on Amazon Bedrock, via the Converse API (ai-content Req 1.7, 1.8, 1.10, 1.11).

Implements the "Wire format the adapter will implement" and "Constraints" sections of
ADR 0007, Addendum 1 (decision D1). The adapter only maps requests and replies. Host
binding, redirects, timeouts, price-based cost and record mode stay in ``LiveTransport``;
validation, retry and recording stay in the engine.

Prompt. The rendered prompt text of ``TransportRequest.rendered_prompt`` is the ``system`` text.
The output schema name rides along with it but no ``outputConfig`` is sent (ADR 0007); the
engine validates the reply. A request without a rendered prompt is refused.

Credential. The adapter receives the key from ``LiveTransport`` for one request and puts it in
the ``Authorization: Bearer`` header, the only credential sent. It never stores, logs or
formats the key into any message; only :func:`credential_fingerprint` ever identifies it.

Error mapping. ``LiveTransport`` hands every non-2xx reply to :meth:`parse_response`, which
classifies it: 400 and 404 are configuration faults (``VendorProtocolError``, not retried);
the other listed statuses are ``ProviderUnavailableError``.
"""

from __future__ import annotations

import json
import threading
from typing import Any, Final
from urllib.parse import quote, urlsplit

from edu_it_domain.ports.llm_provider import (
    ProviderBindingError,
    ProviderUnavailableError,
)

from edu_it_infrastructure.llm.registry import (
    HttpRequest,
    VendorProtocolError,
    VendorReply,
    register_vendor,
)
from edu_it_infrastructure.llm.transport import TransportRequest

__all__ = [
    "VENDOR_NAME",
    "BedrockClaudeAdapter",
    "validate_model_id",
]

VENDOR_NAME: Final = "bedrock_claude"

#: Output cap of one Converse call and the sampling temperature (adapter constants).
MAX_OUTPUT_TOKENS: Final = 4096
TEMPERATURE: Final = 0.2

#: Statuses ADR 0007 maps to ``ProviderUnavailableError``.
_UNAVAILABLE_STATUSES: Final = frozenset({401, 403, 408, 424, 429, 500, 503})
#: Request or configuration faults: not retried, reported as ``VendorProtocolError``.
_CONFIG_FAULT_STATUSES: Final = frozenset({400, 404})


def validate_model_id(model_id: str) -> None:
    """Reject an unusable model id; a ``global.`` profile can route outside the EU (ADR 0007)."""
    if not model_id or model_id != model_id.strip():
        raise ProviderBindingError("the Bedrock model id must be non-empty and trimmed")
    if model_id.casefold().startswith("global."):
        raise ProviderBindingError(
            "a global. inference profile can route outside the EU and is not allowed"
        )


class BedrockClaudeAdapter:
    """Builds Converse requests and parses Converse replies."""

    def __init__(self) -> None:
        # Converse does not echo the model, so remember the configured one per thread.
        self._local = threading.local()

    def build_request(
        self, request: TransportRequest, *, endpoint: str, api_key: str
    ) -> HttpRequest:
        validate_model_id(request.model)
        if not api_key:
            raise ProviderBindingError("no API key configured for the live provider")
        rendered = request.rendered_prompt
        if rendered is None or not rendered.text.strip():
            raise ProviderBindingError(
                f"no rendered prompt for {request.prompt.prompt_id} "
                f"v{request.prompt.prompt_version}"
            )
        parts = urlsplit(endpoint)
        base = f"{parts.scheme}://{parts.netloc}{parts.path.rstrip('/')}"
        url = f"{base}/model/{quote(request.model, safe='')}/converse"
        user_text = json.dumps(dict(request.input.fields), sort_keys=True, ensure_ascii=False)
        payload = {
            "system": [{"text": rendered.text}],
            "messages": [{"role": "user", "content": [{"text": user_text}]}],
            "inferenceConfig": {"maxTokens": MAX_OUTPUT_TOKENS, "temperature": TEMPERATURE},
        }
        self._local.model = request.model
        return HttpRequest(
            url=url,
            body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )

    def parse_response(self, status: int, body: bytes) -> VendorReply:
        if status in _CONFIG_FAULT_STATUSES:
            raise VendorProtocolError(
                f"Bedrock rejected the request (HTTP {status}): configuration fault, not retried"
            )
        if status in _UNAVAILABLE_STATUSES or not 200 <= status < 300:
            raise ProviderUnavailableError(f"Bedrock answered HTTP {status}")

        model = getattr(self._local, "model", None)
        if not isinstance(model, str):
            raise VendorProtocolError("no configured model for this response")
        try:
            data = json.loads(body)
        except (ValueError, UnicodeDecodeError) as err:
            raise VendorProtocolError("response body is not JSON") from err

        stop_reason = _field(data, "stopReason", str)
        if stop_reason != "end_turn":
            raise VendorProtocolError(f"abnormal stopReason {stop_reason!r}; reply not used")
        message = _field(_field(data, "output", dict), "message", dict)
        blocks = _field(message, "content", list)
        texts = [
            b["text"] for b in blocks if isinstance(b, dict) and isinstance(b.get("text"), str)
        ]
        text = "".join(texts)
        if not text.strip():
            raise VendorProtocolError("response has no text")
        usage = _field(data, "usage", dict)
        return VendorReply(
            text=text,
            model=model,
            input_tokens=_count(usage, "inputTokens"),
            output_tokens=_count(usage, "outputTokens"),
        )


def _field(container: Any, key: str, kind: type) -> Any:
    if not isinstance(container, dict) or not isinstance(container.get(key), kind):
        raise VendorProtocolError(f"response field {key!r} is missing or has the wrong type")
    return container[key]


def _count(usage: dict[str, Any], key: str) -> int:
    value = usage.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise VendorProtocolError(f"usage field {key!r} is missing or invalid")
    return value


@register_vendor(VENDOR_NAME)
def _factory() -> BedrockClaudeAdapter:
    return BedrockClaudeAdapter()
