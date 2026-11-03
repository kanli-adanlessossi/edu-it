"""Refreshable credential source for the Live_Provider, over a loopback stub (no model network).

The stub accepts a request only when its key is in ``Stub.valid``; any other key gets 401 (or 403).
Secrets Manager is replaced by a fake client, so nothing leaves the machine.

**Validates: Requirements 1.7, 1.8, 1.10, 1.11**
"""

from __future__ import annotations

import dataclasses
import json
import logging
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest
from edu_it_domain.ports.generation_ledger import JobKind, NewJob
from edu_it_domain.ports.llm_provider import (
    LLMInput,
    PromptRef,
    ProviderBindingError,
    ProviderUnavailableError,
)
from edu_it_infrastructure.composition import (
    CompositionSettings,
    ProviderSelectionError,
    build_credential_source,
    build_llm_provider,
)
from edu_it_infrastructure.fakes import In_Memory_GenerationLedger, Test_Clock
from edu_it_infrastructure.llm.credentials import (
    CredentialUnavailableError,
    EnvironmentCredentialSource,
    SecretsManagerCredentialSource,
    credential_fingerprint,
)
from edu_it_infrastructure.llm.engine import LLMEngine, ProviderBinding
from edu_it_infrastructure.llm.ledger_binding import JobScope
from edu_it_infrastructure.llm.live import LiveTransport, ModelPrice
from edu_it_infrastructure.llm.pydantic_schema import PydanticOutputSchema
from edu_it_infrastructure.llm.registry import HttpRequest, VendorProtocolError, VendorReply
from edu_it_infrastructure.llm.transport import RawCompletion, TransportRequest
from pydantic import BaseModel

PROMPT = PromptRef(prompt_id="brief", prompt_version="1")
INPUT = LLMInput(kind="brief", fields={"track": "networking"})
PRICES = {"m": ModelPrice(input_cents_per_mtok=100_000, output_cents_per_mtok=200_000)}
KEY_A = "secret-key-A-do-not-log"
KEY_B = "secret-key-B-do-not-log"


class _Adapter:
    def build_request(
        self, request: TransportRequest, *, endpoint: str, api_key: str
    ) -> HttpRequest:
        return HttpRequest(
            url=endpoint + "/complete",
            body=b"{}",
            headers={"x-test-key": api_key, "content-type": "application/json"},
        )

    def parse_response(self, status: int, body: bytes) -> VendorReply:
        try:
            data = json.loads(body)
            return VendorReply(data["text"], data["model"], int(data["in"]), int(data["out"]))
        except (ValueError, KeyError, TypeError) as err:
            raise VendorProtocolError(str(err)) from err


@dataclass
class Stub:
    port: int
    valid: set[str] = field(default_factory=set)
    refuse_with: int = 401
    seen_keys: list[str] = field(default_factory=list)

    @property
    def endpoint(self) -> str:
        return f"http://127.0.0.1:{self.port}"


@pytest.fixture
def stub() -> Iterator[Stub]:
    state: Stub

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            self.rfile.read(int(self.headers.get("content-length", "0")))
            key = self.headers.get("x-test-key", "")
            state.seen_keys.append(key)
            if key in state.valid:
                code = 200
                payload = json.dumps(
                    {"text": '{"title": "x"}', "model": "m", "in": 10, "out": 5}
                ).encode()
            else:
                code, payload = state.refuse_with, b"{}"
            self.send_response(code)
            self.send_header("content-length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

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


class FakeSecrets:
    """Fake Secrets Manager client; ``value`` can change between calls (a rotation)."""

    def __init__(self, value: Any = KEY_A, error: Exception | None = None) -> None:
        self.value = value
        self.error = error
        self.reads = 0

    def get_secret_value(self, *, SecretId: str) -> dict[str, Any]:
        self.reads += 1
        if self.error is not None:
            raise self.error
        return {"SecretString": self.value} if self.value is not None else {}


def _live(stub: Stub, source: Any) -> LiveTransport:
    return LiveTransport(
        adapter=_Adapter(),
        endpoint=stub.endpoint,
        credentials=source,
        allowed_hosts=("127.0.0.1",),
        prices=PRICES,
        allow_plain_http_loopback=True,
    )


def _request() -> TransportRequest:
    return TransportRequest(PROMPT, INPUT, "m", 1, timedelta(seconds=5))


class _Out(BaseModel):
    title: str


class _Gate:
    def before_attempt(self, attempt: int) -> None:
        return None


class _Sink:
    def record(self, usage: object) -> None:
        return None


def _engine(live: LiveTransport) -> LLMEngine:
    binding = ProviderBinding(frozenset({"127.0.0.1"}), lambda: live.credential_id)
    return LLMEngine(live, model="m", gate=_Gate(), sink=_Sink(), binding=binding)


# -- sources ----------------------------------------------------------------


def test_key_is_read_on_first_use_not_at_construction(stub: Stub) -> None:
    secrets = FakeSecrets()
    source = SecretsManagerCredentialSource("sid", client=secrets)
    live = _live(stub, source)
    assert secrets.reads == 0 and live.credential_id is None
    stub.valid = {KEY_A}
    live.complete(_request())
    live.complete(_request())
    assert secrets.reads == 1  # cached after the first read
    assert live.credential_id == credential_fingerprint(KEY_A)


def test_rotation_between_two_calls_is_picked_up_without_a_binding_failure(stub: Stub) -> None:
    secrets = FakeSecrets(KEY_A)
    live = _live(stub, SecretsManagerCredentialSource("sid", client=secrets))
    engine = _engine(live)
    stub.valid = {KEY_A}
    engine.complete(PROMPT, INPUT, PydanticOutputSchema(_Out), 0)
    assert live.credential_id == credential_fingerprint(KEY_A)

    secrets.value = KEY_B  # rotated: the old key stops working
    stub.valid = {KEY_B}
    result = engine.complete(PROMPT, INPUT, PydanticOutputSchema(_Out), 0)
    assert result.output == {"title": "x"}
    assert stub.seen_keys == [KEY_A, KEY_A, KEY_B]  # one refused, one retry under the new key
    assert live.credential_id == credential_fingerprint(KEY_B)
    assert secrets.reads == 2


@pytest.mark.parametrize("refusal", [401, 403])
def test_refusal_then_success_after_one_re_read(stub: Stub, refusal: int) -> None:
    stub.refuse_with = refusal
    secrets = FakeSecrets(KEY_A)
    live = _live(stub, SecretsManagerCredentialSource("sid", client=secrets))
    stub.valid = {KEY_A}
    live.complete(_request())
    secrets.value = KEY_B
    stub.valid = {KEY_B}
    raw = live.complete(_request())
    assert raw.credential_id == credential_fingerprint(KEY_B)
    assert len(stub.seen_keys) == 3


@pytest.mark.parametrize("refusal", [401, 403])
def test_repeated_refusal_is_provider_unavailable_after_one_retry(stub: Stub, refusal: int) -> None:
    stub.refuse_with = refusal
    secrets = FakeSecrets(KEY_A)
    live = _live(stub, SecretsManagerCredentialSource("sid", client=secrets))
    with pytest.raises(ProviderUnavailableError) as info:
        live.complete(_request())
    assert len(stub.seen_keys) == 2 and secrets.reads == 2
    assert KEY_A not in str(info.value)


def test_environment_key_is_retried_once_then_unavailable(stub: Stub) -> None:
    live = _live(stub, EnvironmentCredentialSource(KEY_A))
    with pytest.raises(ProviderUnavailableError):
        live.complete(_request())
    assert stub.seen_keys == [KEY_A, KEY_A]


@pytest.mark.parametrize(
    "secrets",
    [
        FakeSecrets(error=RuntimeError(f"ResourceNotFoundException {KEY_A}")),
        FakeSecrets(value=None),
        FakeSecrets(value="   "),
    ],
)
def test_missing_or_unreadable_secret_is_unavailable_and_sends_nothing(
    stub: Stub, secrets: FakeSecrets
) -> None:
    live = _live(stub, SecretsManagerCredentialSource("sid", client=secrets))
    with pytest.raises(ProviderUnavailableError) as info:
        live.complete(_request())
    assert stub.seen_keys == []
    assert KEY_A not in str(info.value) and KEY_A not in repr(info.value.__cause__)


def test_unreadable_secret_on_refresh_is_unavailable(stub: Stub) -> None:
    secrets = FakeSecrets(KEY_A)
    live = _live(stub, SecretsManagerCredentialSource("sid", client=secrets))
    stub.valid = {KEY_A}
    live.complete(_request())
    stub.valid = set()  # the cached key is no longer accepted
    secrets.error = RuntimeError("AccessDenied")
    with pytest.raises(ProviderUnavailableError):
        live.complete(_request())  # cached key refused, then the re-read fails
    assert secrets.reads == 2 and len(stub.seen_keys) == 2


def test_secret_without_boto3_client_is_a_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(__import__("sys").modules, "boto3", None)
    with pytest.raises(CredentialUnavailableError, match="boto3"):
        SecretsManagerCredentialSource("sid").current()


# -- binding ----------------------------------------------------------------


def test_a_response_under_another_key_is_still_rejected(stub: Stub) -> None:
    stub.valid = {KEY_A}
    live = _live(stub, EnvironmentCredentialSource(KEY_A))

    class OtherKey:
        """Reports a different credential than the one the transport is using."""

        def complete(self, request: TransportRequest) -> RawCompletion:
            return dataclasses.replace(
                live.complete(request), credential_id=credential_fingerprint(KEY_B)
            )

    binding = ProviderBinding(frozenset({"127.0.0.1"}), lambda: live.credential_id)
    engine = LLMEngine(OtherKey(), model="m", gate=_Gate(), sink=_Sink(), binding=binding)
    with pytest.raises(ProviderBindingError):
        engine.complete(PROMPT, INPUT, PydanticOutputSchema(_Out), 0)


def test_binding_with_no_key_read_yet_accepts_nothing() -> None:
    binding = ProviderBinding(frozenset({"h"}), lambda: None)
    assert not binding.accepts(RawCompletion("", "m", 0, 0, 0, origin_host="h"))


def test_only_the_fingerprint_is_logged(stub: Stub, caplog: pytest.LogCaptureFixture) -> None:
    secrets = FakeSecrets(KEY_A)
    live = _live(stub, SecretsManagerCredentialSource("sid", client=secrets))
    stub.valid = {KEY_A}
    live.complete(_request())
    secrets.value = KEY_B
    stub.valid = {KEY_B}
    with caplog.at_level(logging.DEBUG):
        live.complete(_request())
    assert credential_fingerprint(KEY_B) in caplog.text
    assert KEY_A not in caplog.text and KEY_B not in caplog.text


# -- provider selection -----------------------------------------------------


def _live_env(**extra: str) -> CompositionSettings:
    return CompositionSettings.from_env(
        {
            "EDU_IT_ENV": "development",
            "LLM_PROVIDER_BACKEND": "live",
            "LLM_PROVIDER_ENDPOINT": "https://eu.example.test/v1",
            "LLM_PROVIDER_ALLOWED_HOSTS": "eu.example.test",
            "LLM_MODEL_GENERATION": "m",
            "LLM_PRICES": "m=1:2",
            **extra,
        }
    )


def test_both_settings_set_is_named() -> None:
    settings = _live_env(LLM_PROVIDER_API_KEY="k", LLM_PROVIDER_API_KEY_SECRET_ID="sid")
    with pytest.raises(ProviderSelectionError, match="both set") as info:
        build_credential_source(settings)
    assert "LLM_PROVIDER_API_KEY_SECRET_ID" in str(info.value)


def test_neither_setting_set_is_named() -> None:
    with pytest.raises(ProviderSelectionError, match="neither is set"):
        build_credential_source(_live_env())


def test_build_llm_provider_refuses_both_and_neither() -> None:
    ledger = In_Memory_GenerationLedger(Test_Clock())
    job = ledger.open_job(NewJob(stable_id="job-1", kind=JobKind.EVAL))
    scope = JobScope(ledger=ledger, job=job, clock=Test_Clock(), cost_cap_cents=None)
    for extra in (
        {"LLM_PROVIDER_API_KEY": "k", "LLM_PROVIDER_API_KEY_SECRET_ID": "sid"},
        {},
    ):
        with pytest.raises(ProviderSelectionError):
            build_llm_provider(_live_env(**extra), scope)


def test_each_setting_selects_its_source() -> None:
    env = build_credential_source(_live_env(LLM_PROVIDER_API_KEY="k"))
    assert isinstance(env, EnvironmentCredentialSource)
    sm = build_credential_source(_live_env(LLM_PROVIDER_API_KEY_SECRET_ID="sid"))
    assert isinstance(sm, SecretsManagerCredentialSource)
    assert sm.fingerprint is None  # nothing is read at selection time
