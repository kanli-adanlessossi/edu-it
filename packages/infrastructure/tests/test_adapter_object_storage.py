"""Unit tests for the production FileSystem_ObjectStorage adapter (task 7.2, Req 5.1, 5.2).

Example and edge-case coverage mirroring the in-memory fake's contract tests: the
adapter satisfies the domain ``ObjectStorage`` port, ``put`` stores and
overwrites, ``get`` returns the stored object (with content type and metadata) or
raises ``KeyError``, ``exists`` reports presence, and ``delete`` is idempotent.
A property-based test checks the put/get round trip across many keys and payloads.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from edu_it_domain.ports import ObjectStorage
from edu_it_infrastructure.adapters import FileSystem_ObjectStorage
from hypothesis import given
from hypothesis import strategies as st


def test_adapter_satisfies_the_domain_port(tmp_path: Path) -> None:
    # Substitutable for the fake / cloud adapter without changing domain code (Req 5.2).
    assert isinstance(FileSystem_ObjectStorage(tmp_path), ObjectStorage)


def test_put_then_get_round_trips_bytes_content_type_and_metadata(tmp_path: Path) -> None:
    store = FileSystem_ObjectStorage(tmp_path)
    store.put(
        "export/user-1.json",
        b"{}",
        content_type="application/json",
        metadata={"user": "1"},
    )
    stored = store.get("export/user-1.json")
    assert stored.key == "export/user-1.json"
    assert stored.data == b"{}"
    assert stored.content_type == "application/json"
    assert stored.metadata == {"user": "1"}


def test_get_defaults_content_type_and_metadata_when_omitted(tmp_path: Path) -> None:
    store = FileSystem_ObjectStorage(tmp_path)
    store.put("k", b"data")
    stored = store.get("k")
    assert stored.content_type == "application/octet-stream"
    assert stored.metadata == {}


def test_put_overwrites_an_existing_object(tmp_path: Path) -> None:
    store = FileSystem_ObjectStorage(tmp_path)
    store.put("k", b"first", content_type="text/plain")
    store.put("k", b"second", content_type="application/json")
    stored = store.get("k")
    assert stored.data == b"second"
    assert stored.content_type == "application/json"


def test_get_raises_key_error_for_a_missing_key(tmp_path: Path) -> None:
    with pytest.raises(KeyError):
        FileSystem_ObjectStorage(tmp_path).get("absent")


def test_exists_reflects_presence(tmp_path: Path) -> None:
    store = FileSystem_ObjectStorage(tmp_path)
    assert store.exists("k") is False
    store.put("k", b"data")
    assert store.exists("k") is True


def test_delete_removes_the_object(tmp_path: Path) -> None:
    store = FileSystem_ObjectStorage(tmp_path)
    store.put("k", b"data")
    store.delete("k")
    assert store.exists("k") is False


def test_delete_is_idempotent_for_a_missing_key(tmp_path: Path) -> None:
    store = FileSystem_ObjectStorage(tmp_path)
    store.delete("absent")  # no error
    assert store.exists("absent") is False


def test_keys_with_path_separators_do_not_escape_the_root(tmp_path: Path) -> None:
    # A key with traversal-looking segments maps to a file inside the root, not outside.
    store = FileSystem_ObjectStorage(tmp_path)
    store.put("../../etc/passwd", b"safe")
    assert store.get("../../etc/passwd").data == b"safe"
    # Nothing was written outside the configured root directory.
    escaped = [p for p in tmp_path.parent.iterdir() if p != tmp_path and p.name == "etc"]
    assert escaped == []


def test_a_new_store_reads_objects_persisted_by_a_previous_store(tmp_path: Path) -> None:
    # Durability: data survives across adapter instances pointed at the same root.
    FileSystem_ObjectStorage(tmp_path).put("k", b"persisted", content_type="text/plain")
    reopened = FileSystem_ObjectStorage(tmp_path)
    assert reopened.get("k").data == b"persisted"
    assert reopened.get("k").content_type == "text/plain"


@given(
    key=st.text(min_size=1, max_size=64),
    data=st.binary(max_size=256),
    content_type=st.sampled_from(
        ["application/octet-stream", "application/json", "text/plain", "image/png"]
    ),
)
def test_put_get_round_trips_across_many_inputs(
    tmp_path_factory: pytest.TempPathFactory,
    key: str,
    data: bytes,
    content_type: str,
) -> None:
    store = FileSystem_ObjectStorage(tmp_path_factory.mktemp("obj"))
    store.put(key, data, content_type=content_type)
    stored = store.get(key)
    assert stored.key == key
    assert stored.data == data
    assert stored.content_type == content_type
    assert store.exists(key) is True
