"""Unit tests for the key rotation Lambda handler (ai-content task 4.14).

Fake clients only; no network and no AWS.

**Validates: Requirements 1.10**
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path
from typing import Any

import pytest
from edu_it_infrastructure.llm.credentials import credential_fingerprint

_PATH = Path(__file__).resolve().parents[1] / "handler.py"
_spec = importlib.util.spec_from_file_location("key_rotation_handler", _PATH)
assert _spec is not None and _spec.loader is not None
handler = importlib.util.module_from_spec(_spec)
sys.modules["key_rotation_handler"] = handler
_spec.loader.exec_module(handler)

KEY = "bedrock-api-key-SECRETPAYLOAD123&Version=1"


class FakeSecrets:
    """Models Secrets Manager: a write adds a version, a failed write changes nothing."""

    def __init__(self, *, fail: Exception | None = None) -> None:
        self.versions: list[str] = ["old-key"]
        self.calls: list[dict[str, Any]] = []
        self._fail = fail

    def put_secret_value(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if self._fail is not None:
            raise self._fail
        self.versions.append(kwargs["SecretString"])
        return {}

    @property
    def current(self) -> str:
        return self.versions[-1]


def _rotate(secrets: FakeSecrets, token: str = KEY) -> str:
    return handler.rotate(
        secret_id="arn:aws:secretsmanager:eu-west-1:123456789012:secret:k",
        region="eu-west-1",
        secrets_client=secrets,
        generate_token=lambda _region: token,
    )


def test_new_version_written_and_fingerprint_returned() -> None:
    secrets = FakeSecrets()
    result = _rotate(secrets)
    assert secrets.versions == ["old-key", KEY]
    assert secrets.calls[0]["SecretId"].endswith(":secret:k")
    assert result == credential_fingerprint(KEY)


def test_fingerprint_matches_application_scheme() -> None:
    assert handler.fingerprint(KEY) == credential_fingerprint(KEY)


def test_failed_write_keeps_previous_version_and_hides_key() -> None:
    secrets = FakeSecrets(fail=RuntimeError(f"boom while writing {KEY}"))
    with pytest.raises(handler.RotationError) as info:
        _rotate(secrets)
    assert secrets.current == "old-key"
    assert secrets.versions == ["old-key"]
    assert KEY not in str(info.value)
    assert "SECRETPAYLOAD" not in repr(info.value)
    assert credential_fingerprint(KEY) in str(info.value)
    assert info.value.__cause__ is None
    assert info.value.__suppress_context__ is True


def test_generation_failure_writes_nothing_and_hides_details() -> None:
    secrets = FakeSecrets()

    def boom(_region: str) -> str:
        raise RuntimeError(KEY)

    with pytest.raises(handler.RotationError) as info:
        handler.rotate(secret_id="s", region="r", secrets_client=secrets, generate_token=boom)
    assert secrets.calls == []
    assert KEY not in str(info.value)


@pytest.mark.parametrize("token", ["", "   "])
def test_empty_token_is_rejected(token: str) -> None:
    secrets = FakeSecrets()
    with pytest.raises(handler.RotationError):
        _rotate(secrets, token)
    assert secrets.calls == []


def test_key_never_appears_in_logs(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG):
        _rotate(FakeSecrets())
        with pytest.raises(handler.RotationError):
            _rotate(FakeSecrets(fail=RuntimeError(KEY)))
    text = caplog.text
    assert "SECRETPAYLOAD" not in text
    assert credential_fingerprint(KEY) in text


def test_missing_configuration_is_reported() -> None:
    with pytest.raises(handler.RotationError):
        handler.rotate(secret_id="", region="r", secrets_client=FakeSecrets())
    with pytest.raises(handler.RotationError):
        handler.rotate(secret_id="s", region="", secrets_client=FakeSecrets())


def test_template_schedules_inside_key_lifetime_and_scopes_the_role() -> None:
    text = (Path(__file__).resolve().parents[1] / "template.yaml").read_text(encoding="utf-8")
    assert "rate(6 hours)" in text
    assert handler.KEY_LIFETIME.total_seconds() == 12 * 3600
    assert "secretsmanager:PutSecretValue" in text
    assert "secretsmanager:GetSecretValue" not in text
    assert "AWS/Lambda" in text and "Errors" in text
