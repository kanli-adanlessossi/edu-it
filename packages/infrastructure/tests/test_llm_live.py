"""Contract suite for the live transport and vendor registry, against a loopback stub server.

The stub speaks a throwaway JSON envelope defined in this test file; it is not a vendor wire format.

**Validates: Requirements 1.1, 1.7, 2.2**
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
from edu_it_domain.ai.fixtures import fixture_key
from edu_it_domain.ports.llm_provider import (
    LLMInput,
    MissingFixtureError,
    PromptRef,
    ProviderBindingError,
    ProviderUnavailableError,
)
from edu_it_infrastructure.llm import registry
from edu_it_infrastructure.llm.credentials import (
    CredentialUnavailableError,
    EnvironmentCredentialSource,
    credential_fingerprint,
)
from edu_it_infrastructure.llm.engine import LLMEngine, ProviderBinding
from edu_it_infrastructure.llm.fixture_store import FixtureStore
from edu_it_infrastructure.llm.live import LiveTransport, ModelPrice
from edu_it_infrastructure.llm.pydantic_schema import PydanticOutputSchema
from edu_it_infrastructure.llm.registry import (
    HttpRequest,
    UnknownVendorError,
    VendorAdapter,
    VendorProtocolError,
    VendorReply,
)
from edu_it_infrastructure.llm.replay import ReplayTransport
from edu_it_infrastructure.llm.transport import (
    ModelTransport,
    TransportRequest,
    TransportTimeoutError,
)
from pydantic import BaseModel

PROMPT = PromptRef(prompt_id="brief", prompt_version="1")
INPUT = LLMInput(kind="brief", fields={"track": "networking"})
KEY = "test-key-123"
PRICES = {"m": ModelPrice(input_cents_per_mtok=100_000, output_cents_per_mtok=200_000)}


class StubAdapter:
    """Test-only envelope: ``{"text", "model", "in", "out"}``; key in ``x-test-key``."""

    def build_request(
        self, request: TransportRequest, *, endpoint: str, api_key: str
    ) -> HttpRequest:
        body = json.dumps({"fields": dict(request.input.fields), "attempt": request.attempt})
        return HttpRequest(
            url=endpoint + "/complete",
            body=body.encode(),
            headers={"x-test-key": api_key, "content-type": "application/json"},
        )

    def parse_response(self, status: int, body: bytes) -> VendorReply:
        if status in (400, 404):
            raise VendorProtocolError(f"configuration fault (HTTP {status})")
        if not 200 <= status < 300:
            raise ProviderUnavailableError(f"HTTP {status}")
        try:
            data = json.loads(body)
            return VendorReply(data["text"], data["model"], int(data["in"]), int(data["out"]))
        except (ValueError, KeyError, TypeError) as err:
            raise VendorProtocolError(str(err)) from err


@dataclass
class Stub:
    port: int
    seen: list[dict[str, str]] = field(default_factory=list)
    script: list[Callable[[BaseHTTPRequestHandler], None]] = field(default_factory=list)

    @property
    def endpoint(self) -> str:
        return f"http://127.0.0.1:{self.port}"


def ok(text: str = '{"title": "x"}', tin: int = 1000, tout: int = 500) -> Callable[..., None]:
    def handle(h: BaseHTTPRequestHandler) -> None:
        payload = json.dumps({"text": text, "model": "m", "in": tin, "out": tout}).encode()
        h.send_response(200)
        h.send_header("content-length", str(len(payload)))
        h.end_headers()
        h.wfile.write(payload)

    return handle


def status(code: int, body: bytes = b"") -> Callable[..., None]:
    def handle(h: BaseHTTPRequestHandler) -> None:
        h.send_response(code)
        h.send_header("content-length", str(len(body)))
        h.end_headers()
        h.wfile.write(body)

    return handle


def redirect(location: str) -> Callable[..., None]:
    def handle(h: BaseHTTPRequestHandler) -> None:
        h.send_response(307)
        h.send_header("location", location)
        h.send_header("content-length", "0")
        h.end_headers()

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
            self.rfile.read(length)
            state.seen.append({k.casefold(): v for k, v in self.headers.items()})
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


def transport(
    stub: Stub,
    *,
    store: FixtureStore | None = None,
    hosts: tuple[str, ...] = ("127.0.0.1",),
    adapter: VendorAdapter | None = None,
) -> LiveTransport:
    return LiveTransport(
        adapter=adapter or StubAdapter(),
        endpoint=stub.endpoint,
        credentials=EnvironmentCredentialSource(KEY),
        allowed_hosts=hosts,
        prices=PRICES,
        record_to=store,
        allow_plain_http_loopback=True,
    )


def request(n: int = 1, timeout: float = 5.0) -> TransportRequest:
    return TransportRequest(PROMPT, INPUT, "m", n, timedelta(seconds=timeout))


def test_success_maps_text_usage_cost_host_and_credential(stub: Stub) -> None:
    stub.script = [ok(tin=1000, tout=500)]
    live = transport(stub)
    assert isinstance(live, ModelTransport)
    raw = live.complete(request())
    assert raw.text == '{"title": "x"}'
    assert (raw.input_tokens, raw.output_tokens) == (1000, 500)
    # 1000*100000 + 500*200000 = 2e8 micro-cents -> 200 cents
    assert raw.cost_cents == 200
    assert raw.origin_host == "127.0.0.1"
    assert raw.credential_id == credential_fingerprint(KEY)
    assert KEY not in (raw.credential_id or "")
    assert stub.seen[0]["x-test-key"] == KEY


def test_cost_rounds_up_to_a_whole_cent() -> None:
    assert ModelPrice(1, 1).cost_cents(1, 0) == 1
    assert ModelPrice(1, 1).cost_cents(0, 0) == 0


@pytest.mark.parametrize("code", [408, 424, 429, 500, 503])
def test_error_statuses_are_unavailable(stub: Stub, code: int) -> None:
    stub.script = [status(code)]
    with pytest.raises(ProviderUnavailableError):
        transport(stub).complete(request())


@pytest.mark.parametrize("code", [401, 403])
def test_auth_refusals_stay_unavailable(stub: Stub, code: int) -> None:
    stub.script = [status(code), status(code)]
    with pytest.raises(ProviderUnavailableError):
        transport(stub).complete(request())
    assert len(stub.seen) == 2  # the single re-read retry, no more


@pytest.mark.parametrize("code", [400, 404])
def test_configuration_faults_keep_the_vendor_error_and_are_not_retried(
    stub: Stub, code: int
) -> None:
    stub.script = [status(code)]
    with pytest.raises(VendorProtocolError):
        transport(stub).complete(request())
    assert len(stub.seen) == 1


@pytest.mark.parametrize("code", [400, 404])
def test_engine_does_not_retry_a_configuration_fault(stub: Stub, code: int) -> None:
    class _Out(BaseModel):
        title: str

    class _Gate:
        def before_attempt(self, attempt: int) -> None:
            return None

    usages: list[object] = []

    class _Sink:
        def record(self, usage: object) -> None:
            usages.append(usage)

    live = transport(stub)
    stub.script = [status(code), ok()]
    engine = LLMEngine(
        live,
        model="m",
        gate=_Gate(),
        sink=_Sink(),
        binding=ProviderBinding(frozenset({"127.0.0.1"}), lambda: live.credential_id),
    )
    with pytest.raises(VendorProtocolError):
        engine.complete(PROMPT, INPUT, PydanticOutputSchema(_Out), 2)
    assert len(stub.seen) == 1
    assert len(usages) == 1


def test_malformed_envelope_is_unavailable(stub: Stub) -> None:
    stub.script = [status(200, b"not json")]
    with pytest.raises(ProviderUnavailableError):
        transport(stub).complete(request())


def test_timeout_is_transport_timeout(stub: Stub) -> None:
    stub.script = [sleepy(1.0)]
    with pytest.raises(TransportTimeoutError):
        transport(stub).complete(request(timeout=0.2))


def test_connection_refused_is_unavailable() -> None:
    dead = Stub(port=1)
    with pytest.raises(ProviderUnavailableError):
        transport(dead).complete(request())


def test_redirect_to_a_disallowed_host_is_not_followed_and_fails_binding(stub: Stub) -> None:
    stub.script = [redirect("http://localhost:1/elsewhere")]
    live = transport(stub)
    raw = live.complete(request())
    assert raw.origin_host == "localhost"
    assert raw.credential_id is None
    assert len(stub.seen) == 1
    binding = ProviderBinding(frozenset({"127.0.0.1"}), lambda: live.credential_id)
    assert not binding.accepts(raw)


def test_redirect_to_an_allowed_host_is_followed(stub: Stub) -> None:
    stub.script = [redirect(f"{stub.endpoint}/other"), ok()]
    raw = transport(stub).complete(request())
    assert raw.text == '{"title": "x"}'
    assert len(stub.seen) == 2


def test_redirect_loop_is_unavailable(stub: Stub) -> None:
    stub.script = [redirect(f"{stub.endpoint}/a") for _ in range(4)]
    with pytest.raises(ProviderUnavailableError):
        transport(stub).complete(request())


def test_unpriced_model_makes_no_call(stub: Stub) -> None:
    stub.script = [ok()]
    bad = TransportRequest(PROMPT, INPUT, "other", 1, timedelta(seconds=5))
    with pytest.raises(ProviderUnavailableError):
        transport(stub).complete(bad)
    assert stub.seen == []


def test_construction_enforces_host_scheme_and_key(stub: Stub) -> None:
    with pytest.raises(ProviderBindingError):
        transport(stub, hosts=("eu.example.org",))
    with pytest.raises(ProviderBindingError):
        LiveTransport(
            adapter=StubAdapter(),
            endpoint="http://127.0.0.1:1",
            credentials=EnvironmentCredentialSource(KEY),
            allowed_hosts=("127.0.0.1",),
            prices=PRICES,
        )  # plain http refused unless loopback testing is enabled
    with pytest.raises(CredentialUnavailableError):  # no key at all
        EnvironmentCredentialSource("")
    with pytest.raises(ProviderBindingError):  # loopback testing flag never allows a real host
        LiveTransport(
            adapter=StubAdapter(),
            endpoint="http://eu.example.org",
            credentials=EnvironmentCredentialSource(KEY),
            allowed_hosts=("eu.example.org",),
            prices=PRICES,
            allow_plain_http_loopback=True,
        )


def test_record_mode_writes_fixtures_replayable_in_order(stub: Stub, tmp_path: Path) -> None:
    stub.script = [ok("bad"), ok('{"title": "x"}')]
    store = FixtureStore(tmp_path)
    live = transport(stub, store=store)
    live.complete(request(1))
    live.complete(request(2))
    assert (tmp_path / "brief" / f"{fixture_key(PROMPT, INPUT)}.json").is_file()
    replay = ReplayTransport(store)
    assert replay.complete(request(1)).text == "bad"
    assert replay.complete(request(2)).text == '{"title": "x"}'
    with pytest.raises(MissingFixtureError):
        replay.complete(request(3))


def test_rerecording_starts_a_fresh_fixture(stub: Stub, tmp_path: Path) -> None:
    stub.script = [ok("old1"), ok("old2"), ok("new")]
    store = FixtureStore(tmp_path)
    live = transport(stub, store=store)
    live.complete(request(1))
    live.complete(request(2))
    live.complete(request(1))
    fixture = store.read(PROMPT, INPUT)
    assert fixture is not None
    assert [a.text for a in fixture.attempts] == ["new"]


def test_no_fixture_without_record_mode(stub: Stub, tmp_path: Path) -> None:
    stub.script = [ok()]
    transport(stub).complete(request())
    assert list(tmp_path.rglob("*.json")) == []


def test_record_mode_refuses_personal_data_in_the_stored_payload(
    stub: Stub, tmp_path: Path
) -> None:
    from edu_it_domain.ports.llm_provider import PersonalDataInRequestError

    stub.script = [ok()]
    live = transport(stub, store=FixtureStore(tmp_path))
    bad = LLMInput(kind="brief", fields={"track": "ada@example.org"})
    with pytest.raises(PersonalDataInRequestError):
        live.complete(TransportRequest(PROMPT, bad, "m", 1, timedelta(seconds=5)))
    assert list(tmp_path.rglob("*.json")) == []


def test_engine_over_live_transport_validates_and_binds(stub: Stub) -> None:
    class Out(BaseModel):
        title: str

    class Gate:
        def before_attempt(self, attempt: int) -> None:
            return None

    class Sink:
        def __init__(self) -> None:
            self.seen: list[object] = []

        def record(self, usage: object) -> None:
            self.seen.append(usage)

    stub.script = [ok("nope"), ok('{"title": "x"}')]
    live = transport(stub)
    binding = ProviderBinding(frozenset({"127.0.0.1"}), credential_fingerprint(KEY))
    engine = LLMEngine(live, model="m", gate=Gate(), sink=Sink(), binding=binding)
    result = engine.complete(PROMPT, INPUT, PydanticOutputSchema(Out), 2)
    assert result.output == {"title": "x"}
    assert [a.outcome for a in result.attempts] == ["invalid", "ok"]

    stub.script = [ok()]
    wrong = ProviderBinding(frozenset({"127.0.0.1"}), credential_fingerprint("other"))
    engine = LLMEngine(live, model="m", gate=Gate(), sink=Sink(), binding=wrong)
    with pytest.raises(ProviderBindingError):
        engine.complete(PROMPT, INPUT, PydanticOutputSchema(Out), 0)


# -- registry ---------------------------------------------------------------


@pytest.fixture
def clean_registry(monkeypatch: pytest.MonkeyPatch) -> dict[str, registry.VendorFactory]:
    fresh: dict[str, registry.VendorFactory] = {}
    monkeypatch.setattr(registry, "_REGISTRY", fresh)
    return fresh


def test_registry_registers_and_resolves_by_name(
    clean_registry: dict[str, registry.VendorFactory],
) -> None:
    registry.register_vendor("stub")(StubAdapter)
    assert registry.registered_vendors() == ("stub",)
    assert isinstance(registry.get_vendor("stub"), StubAdapter)


def test_registry_rejects_duplicates_bad_names_and_unknown(
    clean_registry: dict[str, registry.VendorFactory],
) -> None:
    registry.register_vendor("stub")(StubAdapter)
    with pytest.raises(ValueError):
        registry.register_vendor("stub")(StubAdapter)
    with pytest.raises(ValueError):
        registry.register_vendor("Bad Name ")(StubAdapter)
    with pytest.raises(UnknownVendorError, match="nope"):
        registry.get_vendor("nope")


def test_vendor_module_in_the_vendors_package_is_discovered(
    clean_registry: dict[str, registry.VendorFactory],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import edu_it_infrastructure.llm.vendors as vendors

    (tmp_path / "zz_probe.py").write_text(
        "from edu_it_infrastructure.llm.registry import register_vendor\n"
        "@register_vendor('zz-probe')\n"
        "def make():\n    return object()\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(vendors, "__path__", [*vendors.__path__, str(tmp_path)])
    registry.load_vendors()
    assert "zz-probe" in registry.registered_vendors()
