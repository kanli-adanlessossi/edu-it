"""``build_llm_provider`` selection table and ledger binding.

**Validates: Requirements 1.1, 1.7, 2.1**
"""

from __future__ import annotations

import inspect
import json
from collections.abc import Mapping
from pathlib import Path

import pytest
from edu_it_domain.ports.generation_ledger import JobKind, NewJob
from edu_it_domain.ports.llm_provider import (
    BudgetReachedError,
    LLMInput,
    LLMProvider,
    MissingFixtureError,
    PromptRef,
)
from edu_it_infrastructure.composition import (
    CompositionSettings,
    LLMRole,
    ProviderSelectionError,
    build_llm_provider,
)
from edu_it_infrastructure.fakes import In_Memory_GenerationLedger, Test_Clock
from edu_it_infrastructure.fakes.llm_provider import Fake_LLMProvider
from edu_it_infrastructure.llm import registry
from edu_it_infrastructure.llm.engine import LLMEngine
from edu_it_infrastructure.llm.ledger_binding import JobScope
from edu_it_infrastructure.llm.live import LiveTransport
from edu_it_infrastructure.llm.registry import HttpRequest, VendorReply
from edu_it_infrastructure.llm.transport import RenderedPrompt, TransportRequest

_PROMPT = PromptRef("p", "1")
_INPUT = LLMInput("verify_answer", {})


class _Schema:
    def validate(self, text: str) -> Mapping[str, object]:
        try:
            value = json.loads(text)
        except json.JSONDecodeError as err:
            raise ValueError(str(err)) from err
        if not isinstance(value, dict):
            raise ValueError("not an object")
        return value


def _scope(*, cap: int | None = None) -> JobScope:
    ledger = In_Memory_GenerationLedger(Test_Clock())
    job = ledger.open_job(NewJob(stable_id="job-1", kind=JobKind.EVAL))
    return JobScope(ledger=ledger, job=job, clock=Test_Clock(), cost_cap_cents=cap)


def _settings(env: str, backend: str, **extra: str) -> CompositionSettings:
    return CompositionSettings.from_env(
        {"EDU_IT_ENV": env, "LLM_PROVIDER_BACKEND": backend, **extra}
    )


class _StubVendor:
    """Test-only vendor adapter: construction only, never called (no network)."""

    def build_request(
        self, request: TransportRequest, *, endpoint: str, api_key: str
    ) -> HttpRequest:
        raise AssertionError("stub vendor must not be called")

    def parse_response(self, status: int, body: bytes) -> VendorReply:
        raise AssertionError("stub vendor must not be called")


@pytest.fixture
def stub_vendor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(registry._REGISTRY, "stub", _StubVendor)


def _live_settings(env: str) -> CompositionSettings:
    return _settings(
        env,
        "live",
        LLM_PROVIDER_VENDOR="stub",
        LLM_PROVIDER_ENDPOINT="https://eu.example.test/v1",
        LLM_PROVIDER_API_KEY="k",
        LLM_PROVIDER_ALLOWED_HOSTS="eu.example.test",
        LLM_MODEL_GENERATION="m",
        LLM_PRICES="m=1:2",
    )


@pytest.mark.parametrize(
    ("env", "backend", "allowed"),
    [
        ("test", "fake", True),
        ("test", "replay", True),
        ("test", "live", False),
        ("development", "fake", True),
        ("development", "replay", True),
        ("development", "live", True),
        ("production", "fake", False),
        ("production", "replay", False),
        ("production", "live", True),
    ],
)
def test_selection_table(
    env: str, backend: str, allowed: bool, tmp_path: Path, stub_vendor: None
) -> None:
    if backend == "live":
        settings = _live_settings(env)
    else:
        settings = _settings(env, backend, LLM_FIXTURE_DIR=str(tmp_path))
    if allowed:
        provider = build_llm_provider(settings, _scope(), prompt_renderer=_render)
        assert isinstance(provider, LLMProvider)
    else:
        with pytest.raises(ProviderSelectionError):
            build_llm_provider(settings, _scope(), prompt_renderer=_render)


def _render(ref: object) -> RenderedPrompt:
    return RenderedPrompt(text="rendered", output_schema="S")


def test_live_without_a_prompt_renderer_is_refused(stub_vendor: None) -> None:
    with pytest.raises(ProviderSelectionError, match="prompt renderer"):
        build_llm_provider(_live_settings("production"), _scope())


def _complete_signature(provider: object) -> inspect.Signature:
    return inspect.signature(provider.complete)  # type: ignore[attr-defined]


def test_live_replay_and_fake_satisfy_the_provider_protocol(
    tmp_path: Path, stub_vendor: None
) -> None:
    live = build_llm_provider(_live_settings("production"), _scope(), prompt_renderer=_render)
    replay = build_llm_provider(
        _settings("test", "replay", LLM_FIXTURE_DIR=str(tmp_path)), _scope()
    )
    fake = build_llm_provider(_settings("test", "fake"), _scope())
    assert isinstance(live, LLMEngine)
    assert isinstance(live._transport, LiveTransport)
    expected = list(inspect.signature(LLMProvider.complete).parameters)
    for provider in (live, replay, fake):
        assert isinstance(provider, LLMProvider)
        assert list(_complete_signature(provider).parameters) == expected[1:]


def test_fake_and_replay_types(tmp_path: Path) -> None:
    fake = build_llm_provider(_settings("test", "fake"), _scope())
    replay_settings = _settings("test", "replay", LLM_FIXTURE_DIR=str(tmp_path))
    replay = build_llm_provider(replay_settings, _scope())
    assert isinstance(fake, Fake_LLMProvider)
    assert isinstance(replay, LLMEngine)


def test_replay_without_fixture_is_missing_and_records_nothing(tmp_path: Path) -> None:
    scope = _scope()
    provider = build_llm_provider(_settings("test", "replay", LLM_FIXTURE_DIR=str(tmp_path)), scope)
    with pytest.raises(MissingFixtureError):
        provider.complete(_PROMPT, _INPUT, _Schema(), 0)
    assert scope.ledger.spent_for_job(scope.job) == 0


def test_fake_is_bound_to_the_ledger_and_the_gate() -> None:
    scope = _scope(cap=0)
    provider = build_llm_provider(_settings("test", "fake"), scope)
    with pytest.raises(BudgetReachedError) as err:
        provider.complete(_PROMPT, _INPUT, _Schema(), 0)
    assert err.value.kind == "job"


def test_fake_records_every_attempt_in_the_ledger() -> None:
    scope = _scope()
    spy: list[str] = []

    class _Counting(_Schema):
        def validate(self, text: str) -> Mapping[str, object]:
            spy.append(text)
            return super().validate(text)

    provider = build_llm_provider(_settings("test", "fake"), scope)
    result = provider.complete(_PROMPT, _INPUT, _Counting(), 2)
    assert len(result.attempts) == 1 == len(spy)


def test_live_missing_settings_are_named() -> None:
    with pytest.raises(ProviderSelectionError, match="LLM_PROVIDER_ENDPOINT"):
        build_llm_provider(_settings("development", "live"), _scope())


def test_live_unknown_vendor_is_a_selection_error() -> None:
    settings = _settings(
        "production",
        "live",
        LLM_PROVIDER_VENDOR="nope",
        LLM_PROVIDER_ENDPOINT="https://eu.example.test/v1",
        LLM_PROVIDER_API_KEY="k",
        LLM_PROVIDER_ALLOWED_HOSTS="eu.example.test",
        LLM_MODEL_GENERATION="m",
        LLM_PRICES="m=1:2",
    )
    with pytest.raises(ProviderSelectionError, match="nope"):
        build_llm_provider(settings, _scope(), prompt_renderer=_render)


def test_settings_parse_lists_prices_and_secrets() -> None:
    settings = _settings(
        "development",
        "fake",
        LLM_PROVIDER_ALLOWED_HOSTS="a.example.test, b.example.test",
        LLM_PRICES="m1=300:1500,m2=1:2",
        LLM_RECORD="true",
        ADVISOR_HANDLE_KEY="s3cret",
        LLM_MODEL_VERIFIER="",
    )
    assert settings.llm_provider_allowed_hosts == ("a.example.test", "b.example.test")
    assert settings.llm_prices == {"m1": (300, 1500), "m2": (1, 2)}
    assert settings.llm_record is True
    assert settings.llm_model_verifier is None
    assert "s3cret" not in repr(settings)


def test_bad_prices_are_rejected() -> None:
    with pytest.raises(ValueError, match="LLM_PRICES"):
        _settings("development", "fake", LLM_PRICES="m1=oops")


def test_verifier_role_uses_verifier_model(tmp_path: Path) -> None:
    settings = _settings(
        "test",
        "replay",
        LLM_FIXTURE_DIR=str(tmp_path),
        LLM_MODEL_VERIFIER="verifier-m",
    )
    assert isinstance(build_llm_provider(settings, _scope(), role=LLMRole.VERIFIER), LLMEngine)
