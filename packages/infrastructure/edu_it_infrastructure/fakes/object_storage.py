"""The in-memory ``ObjectStorage`` fake (Req 5.2).

:class:`In_Memory_ObjectStorage` implements the domain
:class:`~edu_it_domain.ports.object_storage.ObjectStorage` port with a plain
in-process dictionary, so the GDPR data-export artifact (Req 14) and later lab
artifacts can be stored and read back in tests and local development without S3
or Azure Blob.

The fake is substitutable for the production adapter without changing domain
code: it satisfies the same ``ObjectStorage`` protocol and matches the port's
documented semantics — ``put`` overwrites, ``get`` raises :class:`KeyError` for a
missing key, and ``delete`` is idempotent.
"""

from __future__ import annotations

from edu_it_domain.ports.object_storage import ObjectStorage, StoredObject

__all__ = ["In_Memory_ObjectStorage"]


class In_Memory_ObjectStorage(ObjectStorage):
    """An :class:`ObjectStorage` backed by an in-process dictionary.

    Satisfies the :class:`~edu_it_domain.ports.object_storage.ObjectStorage`
    protocol, so domain code stores and retrieves objects exactly as it does in
    production.
    """

    __test__ = False

    def __init__(self) -> None:
        """Create an empty store."""
        self._objects: dict[str, StoredObject] = {}

    def put(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str = "application/octet-stream",
        metadata: dict[str, str] | None = None,
    ) -> None:
        """Store ``data`` under ``key``, overwriting any existing object.

        The bytes and metadata are copied so a later mutation of the caller's
        arguments cannot change what was stored.
        """
        self._objects[key] = StoredObject(
            key=key,
            data=bytes(data),
            content_type=content_type,
            metadata=dict(metadata) if metadata is not None else {},
        )

    def get(self, key: str) -> StoredObject:
        """Return the object stored under ``key``.

        Raises :class:`KeyError` when no object exists for ``key``, matching the
        port contract.
        """
        return self._objects[key]

    def exists(self, key: str) -> bool:
        """Return whether an object is stored under ``key``."""
        return key in self._objects

    def delete(self, key: str) -> None:
        """Remove the object stored under ``key``.

        Deleting a key that does not exist is a no-op, so deletion is idempotent.
        """
        self._objects.pop(key, None)

    def keys(self) -> tuple[str, ...]:
        """Return the keys currently stored, for test inspection."""
        return tuple(self._objects)
