"""The ``ObjectStorage`` port (Req 5.1).

Binary artifacts — primarily the GDPR data-export file (Req 14) and, in later
phases, lab artifacts — are stored through this port. Production uses S3 or
Azure Blob; development/test uses an in-memory fake. The domain references
objects by key and never depends on a concrete storage SDK.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class StoredObject:
    """An object retrieved from storage: its bytes and content type."""

    key: str
    """The key the object is stored under."""

    data: bytes
    """The object's raw bytes."""

    content_type: str = "application/octet-stream"
    """The MIME type recorded for the object."""

    metadata: dict[str, str] = field(default_factory=dict)
    """Optional opaque key/value metadata stored alongside the object."""


@runtime_checkable
class ObjectStorage(Protocol):
    """Port for storing and retrieving binary objects by key."""

    def put(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str = "application/octet-stream",
        metadata: dict[str, str] | None = None,
    ) -> None:
        """Store ``data`` under ``key``, overwriting any existing object."""
        ...

    def get(self, key: str) -> StoredObject:
        """Return the object stored under ``key``.

        Raises :class:`KeyError` when no object exists for ``key``.
        """
        ...

    def exists(self, key: str) -> bool:
        """Return whether an object is stored under ``key``."""
        ...

    def delete(self, key: str) -> None:
        """Remove the object stored under ``key``.

        Deleting a key that does not exist is a no-op, so deletion is
        idempotent.
        """
        ...
