"""Contract tests for the ``bedrock_claude`` vendor adapter, on vendor-shaped fixtures, no network.

Requests go through ``LiveTransport`` to a loopback stub that replays the Converse-shaped
fixtures under ``tests/fixtures/bedrock_claude``. The keys here are fake.

**Validates: Requirements 1.7, 1.8, 1.10, 1.11**
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from edu_it_domain.ports.llm_provider import (
    LLMInput,
    PromptRef,
    ProviderBindingError,
    ProviderUnavailableError,
)
from edu_it_infrastructure.llm.credentials import (
    EnvironmentCredentialSource,
    credential_fingerprint,
)
from edu_it_infrastructure.llm.engine import ProviderBinding
from edu_it_infrastructure.llm.live import LiveTransport, ModelPrice
from edu_it_infrastructure.llm.registry import (
    VendorAdapter,
    VendorProtocolError,
    get_vendor,
    registered_vendors,
)
from edu_it_infrastructure.llm.transport import (
    RenderedPrompt,
    TransportRequest,
    TransportTimeoutError,
)
from edu_it_infrastructure.llm.vendors.bedrock_claude import (
    VENDOR_NAME,
    BedrockClaudeAdapter,
    validate_model_id,
)

FIXTURES = Path(__file__).parent / "fixtures" / "bedrock_claude"
MODEL = "eu.anthropic.claude-sonnet-4-6"
KEY = "fake-bedrock-key-for-tests"
PROMPT = PromptRef(prompt_id="brief", prompt_version="1")
INPUT = LLMInput(kind="brief", fields={"track": "networking"})
RENDERED = RenderedPrompt(text="GLOSSARY TEXT\n\nBRIEF PROMPT BODY", output_schema="BriefSchema")
PRICES = {MODEL: ModelPrice(input_cents_per_mtok=300, output_cents_per_mtok=1500)}


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def req(model: str = MODEL, timeout: float = 5.0) -> TransportRequest:
    return TransportRequest(PROMPT, INPUT, model, 1, timedelta(seconds=timeout), RENDERED)


@dataclass
class Stub:
    port: int
    seen: list[dict[str, object]] = field(default_factory=list)
    script: list[Callable[[BaseHTTPRequestHandler], None]] = field(default_factory=list)

    @property
    def endpoint(self) -> str:
        return f"http://127.0.0.1:{self.port}"


def reply(code: int, body: bytes, location: str | None = None) -> Callable[..., None]:
    def handle(h: BaseHTTPRequestHandler) -> None:
        h.send_response(code)
        if location:
            h.send_header("location", location)
        h.send_header("content-length", str(len(body)))
        h.end_headers()
        h.wfile.write(body)

    return handle


def sleepy(seconds: float) -> Callable[..., None]:
    def handle(h: BaseHTTPRequestHandler) -> None:
        time.sleep(seconds)

    return handle


@pytest.fixture
def stub() -> Iterator[Stub]:
    state: Stub

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("content-length", "0"))
            body = self.rfile.read(length)
            state.seen.append(
                {
                    "path": self.path,
                    "headers": {k.casefold(): v for k, v in self.headers.items()},
                    "body": json.loads(body),
                }
            )
            state.script.pop(0)(self)

        def log_message(self, *_a: object) -> None:
            return None

    server = HTTPServer(("127.0.0.1", 0), Handler)
    state = Stub(port=server.server_address[1])
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield state
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def live(stub: Stub, adapter: VendorAdapter | None = None) -> LiveTransport:
    return LiveTransport(
        adapter=adapter or BedrockClaudeAdapter(),
        endpoint=stub.endpoint,
        credentials=EnvironmentCredentialSource(KEY),
        allowed_hosts=("127.0.0.1",),
        prices=PRICES,
        allow_plain_http_loopback=True,
    )


def test_registered_under_its_name() -> None:
    assert VENDOR_NAME == "bedrock_claude"
    assert isinstance(get_vendor("bedrock_claude"), BedrockClaudeAdapter)
    assert "bedrock_claude" in registered_vendors()


def test_success_wire_format_and_mapping(stub: Stub) -> None:
    stub.script = [reply(200, fixture("converse_success.json"))]
    raw = live(stub).complete(req())

    assert raw.text == '{"title": "Networking basics"}'  # blocks concatenated in order
    assert raw.model == MODEL  # Converse does not echo it; the configured id is reported
    assert raw.origin_host == "127.0.0.1"
    assert raw.credential_id == credential_fingerprint(KEY)
    sent = stub.seen[0]
    assert sent["path"] == f"/model/{MODEL}/converse"
    headers = sent["headers"]
    assert isinstance(headers, dict)
    assert headers["authorization"] == f"Bearer {KEY}"
    assert not {"x-api-key", "api-key"} & set(headers)  # the bearer token is the only credential
    body = sent["body"]
    assert isinstance(body, dict)
    assert set(body) == {"system", "messages", "inferenceConfig"}  # no outputConfig
    assert body["messages"][0]["role"] == "user"
    assert set(body["inferenceConfig"]) == {"maxTokens", "temperature"}
    assert KEY not in json.dumps(body)


def test_model_id_is_url_encoded() -> None:
    built = BedrockClaudeAdapter().build_request(
        req("anthropic.claude-3:0/x"), endpoint="https://h.example", api_key=KEY
    )
    assert built.url == "https://h.example/model/anthropic.claude-3%3A0%2Fx/converse"


def test_token_usage_and_cost_mapping(stub: Stub) -> None:
    stub.script = [reply(200, fixture("converse_success.json"))]
    raw = live(stub).complete(req())
    assert (raw.input_tokens, raw.output_tokens) == (1000, 500)
    # (1000*300 + 500*1500) / 1e6 = 1.05 cents -> rounded up to 2
    assert raw.cost_cents == 2


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b"[]",
        b'{"stopReason": "end_turn"}',
        fixture("converse_no_usage.json"),
        b'{"stopReason":"end_turn","output":{"message":{"content":[{"text":"{}"}]}},'
        b'"usage":{"inputTokens":"7","outputTokens":1}}',
    ],
)
def test_malformed_2xx_body_is_a_protocol_error(body: bytes) -> None:
    adapter = BedrockClaudeAdapter()
    adapter.build_request(req(), endpoint="https://h.example", api_key=KEY)
    with pytest.raises(VendorProtocolError):
        adapter.parse_response(200, body)


def test_malformed_body_through_the_transport_is_unavailable(stub: Stub) -> None:
    stub.script = [reply(200, b"not json")]
    with pytest.raises(ProviderUnavailableError):
        live(stub).complete(req())


@pytest.mark.parametrize(
    "stop", ["max_tokens", "content_filtered", "guardrail_intervened", "tool_use", "new_value"]
)
def test_only_end_turn_is_a_normal_stop(stop: str) -> None:
    adapter = BedrockClaudeAdapter()
    adapter.build_request(req(), endpoint="https://h.example", api_key=KEY)
    data = json.loads(fixture("converse_success.json"))
    data["stopReason"] = stop
    with pytest.raises(VendorProtocolError):
        adapter.parse_response(200, json.dumps(data).encode())


def test_max_tokens_fixture_is_malformed_and_empty_text_too() -> None:
    adapter = BedrockClaudeAdapter()
    adapter.build_request(req(), endpoint="https://h.example", api_key=KEY)
    with pytest.raises(VendorProtocolError):
        adapter.parse_response(200, fixture("converse_max_tokens.json"))
    data = json.loads(fixture("converse_success.json"))
    data["output"]["message"]["content"] = [{"text": "  "}]
    with pytest.raises(VendorProtocolError):
        adapter.parse_response(200, json.dumps(data).encode())


@pytest.mark.parametrize("code", [429, 408, 424, 500, 503, 401, 403])
def test_unavailable_statuses(stub: Stub, code: int) -> None:
    stub.script = [reply(code, fixture("throttling.json"))]
    with pytest.raises(ProviderUnavailableError):
        live(stub).complete(req())
    adapter = BedrockClaudeAdapter()
    with pytest.raises(ProviderUnavailableError):
        adapter.parse_response(code, fixture("throttling.json"))


@pytest.mark.parametrize("code", [400, 404])
def test_configuration_faults_use_the_vendor_error(code: int) -> None:
    with pytest.raises(VendorProtocolError):
        BedrockClaudeAdapter().parse_response(code, b"{}")


@pytest.mark.parametrize("code", [400, 404])
def test_configuration_faults_through_live_transport_are_not_retried(stub: Stub, code: int) -> None:
    stub.script = [reply(code, b"{}")]
    with pytest.raises(VendorProtocolError):
        live(stub).complete(req())
    assert len(stub.seen) == 1


def test_timeout(stub: Stub) -> None:
    stub.script = [sleepy(1.0)]
    with pytest.raises(TransportTimeoutError):
        live(stub).complete(req(timeout=0.2))


def test_redirect_to_another_host_is_not_followed_and_fails_binding(stub: Stub) -> None:
    stub.script = [reply(307, b"", location="http://localhost:1/elsewhere")]
    transport = live(stub)
    raw = transport.complete(req())
    assert len(stub.seen) == 1  # nothing was sent to the other host, so no key either
    assert raw.origin_host == "localhost"
    assert raw.credential_id is None
    assert not ProviderBinding(frozenset({"127.0.0.1"}), lambda: transport.credential_id).accepts(
        raw
    )


def test_response_under_another_key_fails_binding(stub: Stub) -> None:
    stub.script = [reply(200, fixture("converse_success.json"))]
    raw = live(stub).complete(req())
    other = ProviderBinding(frozenset({"127.0.0.1"}), credential_fingerprint("another-fake-key"))
    assert not other.accepts(raw)
    assert ProviderBinding(frozenset({"127.0.0.1"}), credential_fingerprint(KEY)).accepts(raw)


def test_response_under_another_json_key_is_a_protocol_error() -> None:
    adapter = BedrockClaudeAdapter()
    adapter.build_request(req(), endpoint="https://h.example", api_key=KEY)
    with pytest.raises(VendorProtocolError):  # "result" instead of "output"
        adapter.parse_response(200, fixture("converse_other_key.json"))


def test_missing_price_fails_the_call_before_any_request(stub: Stub) -> None:
    transport = LiveTransport(
        adapter=BedrockClaudeAdapter(),
        endpoint=stub.endpoint,
        credentials=EnvironmentCredentialSource(KEY),
        allowed_hosts=("127.0.0.1",),
        prices={},
        allow_plain_http_loopback=True,
    )
    with pytest.raises(ProviderUnavailableError):
        transport.complete(req())
    assert stub.seen == []


@pytest.mark.parametrize("model", ["global.anthropic.claude-sonnet-4-6", "GLOBAL.x", "", " x"])
def test_unusable_model_ids_are_rejected(model: str) -> None:
    with pytest.raises(ProviderBindingError):
        validate_model_id(model)
    with pytest.raises(ProviderBindingError):
        BedrockClaudeAdapter().build_request(req(model), endpoint="https://h.example", api_key=KEY)


def test_the_key_is_never_in_an_error_message() -> None:
    adapter = BedrockClaudeAdapter()
    adapter.build_request(req(), endpoint="https://h.example", api_key=KEY)
    for status, body in [(200, b"not json"), (400, b"{}"), (429, b"{}")]:
        with pytest.raises((VendorProtocolError, ProviderUnavailableError)) as info:
            adapter.parse_response(status, body)
        assert KEY not in str(info.value)


def test_rendered_prompt_is_the_system_text_and_no_output_config_is_sent(stub: Stub) -> None:
    stub.script = [reply(200, fixture("converse_success.json"))]
    live(stub).complete(req())

    body = stub.seen[0]["body"]
    assert isinstance(body, dict)
    assert body["system"] == [{"text": RENDERED.text}]
    assert "outputConfig" not in body
    assert "content drafting model" not in json.dumps(body)  # the old one-line fallback is gone


def test_request_without_a_rendered_prompt_is_refused() -> None:
    bare = TransportRequest(PROMPT, INPUT, MODEL, 1, timedelta(seconds=5))
    with pytest.raises(ProviderBindingError, match="rendered prompt"):
        BedrockClaudeAdapter().build_request(bare, endpoint="https://h.example", api_key=KEY)
