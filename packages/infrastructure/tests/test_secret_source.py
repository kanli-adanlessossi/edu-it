"""Unit tests for mounted-file secret handling (task 8.1, Req 23.2, 23.6)."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from edu_it_infrastructure.secrets import (
    FileSecretSource,
    SecretHolder,
    check_required_secrets,
)

VALUE = "s3cr3t-value-XYZ"


def _write(root: Path, key: str, content: str) -> None:
    path = root / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_file_source_reads_current_content(tmp_path: Path) -> None:
    _write(tmp_path, "database/url", VALUE + "\n")
    assert FileSecretSource(tmp_path).read("database/url") == VALUE


@pytest.mark.parametrize("key", ["", "../x", "/etc/passwd"])
def test_file_source_rejects_invalid_keys(tmp_path: Path, key: str) -> None:
    with pytest.raises(KeyError):
        FileSecretSource(tmp_path).read(key)


def test_missing_keys_logged_once_each_without_values(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _write(tmp_path, "database/url", VALUE)
    source = FileSecretSource(tmp_path)
    with caplog.at_level(logging.DEBUG):
        missing = check_required_secrets(source, ["database/url", "redis/url", "llm/key"])
    assert missing == ("redis/url", "llm/key")
    assert len(caplog.records) == 2
    messages = [r.getMessage() for r in caplog.records]
    assert "redis/url" in messages[0]
    assert "llm/key" in messages[1]
    assert all(VALUE not in m for m in messages)


def test_all_present_returns_empty_and_logs_nothing(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _write(tmp_path, "a/b", VALUE)
    with caplog.at_level(logging.DEBUG):
        assert check_required_secrets(FileSecretSource(tmp_path), ["a/b"]) == ()
    assert caplog.records == []


def test_holder_returns_new_value_after_successful_refresh(tmp_path: Path) -> None:
    _write(tmp_path, "llm/key", "old")
    holder = SecretHolder(FileSecretSource(tmp_path), "llm/key")
    assert holder.value is None
    assert holder.refresh() is True
    assert holder.value == "old"
    _write(tmp_path, "llm/key", "new")
    assert holder.refresh() is True
    assert holder.value == "new"


def test_holder_keeps_last_good_value_and_never_raises(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _write(tmp_path, "llm/key", VALUE)
    holder = SecretHolder(FileSecretSource(tmp_path), "llm/key")
    holder.refresh()
    (tmp_path / "llm" / "key").unlink()
    with caplog.at_level(logging.DEBUG):
        assert holder.refresh() is False
    assert holder.value == VALUE
    assert len(caplog.records) == 1
    assert caplog.records[0].levelno == logging.WARNING
    assert VALUE not in caplog.records[0].getMessage()
    assert "llm/key" in caplog.records[0].getMessage()
