"""Unit tests for the in-memory In_Memory_ObjectStorage fake (task 5.3, Req 5.2).

Example and edge-case coverage: the fake satisfies the domain ``ObjectStorage``
port, ``put`` stores and overwrites, ``get`` returns the stored object or raises
``KeyError``, ``exists`` reports presence, and ``delete`` is idempotent. The
formal substitutability assertions are task 5.4.
"""

from __future__ import annotations

import pytest
from edu_it_domain.ports import ObjectStorage
from edu_it_infrastructure.fakes import In_Memory_ObjectStorage


def test_object_storage_satisfies_the_domain_port() -> None:
    # Substitutable for the production S3/Blob adapter without changing domain code (Req 5.2).
    assert isinstance(In_Memory_ObjectStorage(), ObjectStorage)


def test_put_then_get_round_trips_bytes_and_content_type() -> None:
    store = In_Memory_ObjectStorage()
    store.put("export/user-1.json", b"{}", content_type="application/json")
    stored = store.get("export/user-1.json")
    assert stored.key == "export/user-1.json"
    assert stored.data == b"{}"
    assert stored.content_type == "application/json"


def test_put_overwrites_an_existing_object() -> None:
    store = In_Memory_ObjectStorage()
    store.put("k", b"first")
    store.put("k", b"second")
    assert store.get("k").data == b"second"


def test_get_raises_key_error_for_a_missing_key() -> None:
    with pytest.raises(KeyError):
        In_Memory_ObjectStorage().get("absent")


def test_exists_reflects_presence() -> None:
    store = In_Memory_ObjectStorage()
    assert store.exists("k") is False
    store.put("k", b"data")
    assert store.exists("k") is True


def test_delete_removes_the_object() -> None:
    store = In_Memory_ObjectStorage()
    store.put("k", b"data")
    store.delete("k")
    assert store.exists("k") is False


def test_delete_is_idempotent_for_a_missing_key() -> None:
    store = In_Memory_ObjectStorage()
    store.delete("absent")  # no error
    assert store.exists("absent") is False


def test_stored_bytes_are_isolated_from_the_caller() -> None:
    store = In_Memory_ObjectStorage()
    data = bytearray(b"abc")
    store.put("k", bytes(data))
    data[0] = ord("z")
    assert store.get("k").data == b"abc"


def test_keys_lists_stored_objects() -> None:
    store = In_Memory_ObjectStorage()
    store.put("a", b"1")
    store.put("b", b"2")
    assert set(store.keys()) == {"a", "b"}
