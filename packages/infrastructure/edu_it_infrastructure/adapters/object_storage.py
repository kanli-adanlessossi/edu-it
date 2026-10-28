"""The production ``ObjectStorage`` adapter (task 7.2, Req 5.1, 5.2).

The design's port table names S3 / Azure Blob as the cloud target for the
:class:`~edu_it_domain.ports.object_storage.ObjectStorage` port, used for the
GDPR data-export artifact (Req 14) and, in later phases, lab artifacts. No cloud
SDK is yet a dependency of this repository and neither the design nor an ADR
mandates one for this phase, so — per task 7.2's guidance — the production
adapter here is a durable **filesystem** implementation that satisfies the port
contract against a mounted directory (a local path in self-hosted deployments or
a mounted object-store volume).

Because the domain depends only on the ``ObjectStorage`` protocol, swapping this
filesystem adapter for an S3 / Azure Blob adapter later is an infrastructure-only
change behind the same port; the key/value + content-type + metadata surface
implemented here is exactly what an S3-style ``put_object`` / ``get_object``
exposes, so the contract carries over unchanged.

Semantics match the port (and the in-memory fake) exactly: :meth:`put`
overwrites, :meth:`get` raises :class:`KeyError` for a missing key, :meth:`exists`
reports presence, and :meth:`delete` is an idempotent no-op on a missing key.
Each object's bytes are stored in one file and its content type / metadata in a
JSON sidecar, so a round-trip returns the same :class:`StoredObject`.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

from edu_it_domain.ports.object_storage import ObjectStorage, StoredObject

__all__ = ["FileSystem_ObjectStorage"]

_DATA_SUFFIX = ".obj"
"""Suffix of the file holding an object's raw bytes."""

_META_SUFFIX = ".meta.json"
"""Suffix of the JSON sidecar holding an object's content type and metadata."""


class FileSystem_ObjectStorage(ObjectStorage):
    """An :class:`ObjectStorage` backed by a directory on a filesystem.

    Satisfies the :class:`~edu_it_domain.ports.object_storage.ObjectStorage`
    protocol, so domain code stores and retrieves objects exactly as it does
    against the cloud (S3 / Azure Blob) adapter.

    Keys are opaque strings that may contain ``/``; each key is mapped to a flat,
    collision-free on-disk name (a hash of the key) so an arbitrary key never
    escapes the storage root or collides with another. The original key is
    recorded in the sidecar and returned on :meth:`get`.
    """

    def __init__(self, root: str | os.PathLike[str]) -> None:
        """Create a store rooted at ``root``, creating the directory if absent."""
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    def _paths_for(self, key: str) -> tuple[Path, Path]:
        """Return the (data, metadata) file paths backing ``key``.

        The key is hashed to a fixed-length hex name so any key — including one
        with path separators or characters illegal on the filesystem — maps to a
        single file inside the root and cannot traverse outside it.
        """
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return (
            self._root / f"{digest}{_DATA_SUFFIX}",
            self._root / f"{digest}{_META_SUFFIX}",
        )

    def _write_atomic(self, path: Path, data: bytes) -> None:
        """Write ``data`` to ``path`` atomically (write-temp-then-replace).

        A temporary file in the same directory is written and flushed, then
        atomically renamed over ``path`` so a reader never observes a partially
        written object and an overwrite is all-or-nothing.
        """
        fd, tmp_name = tempfile.mkstemp(dir=self._root, suffix=".tmp")
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, path)
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise

    def put(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str = "application/octet-stream",
        metadata: dict[str, str] | None = None,
    ) -> None:
        """Store ``data`` under ``key``, overwriting any existing object.

        The content type and metadata are recorded in a JSON sidecar so a later
        :meth:`get` reconstructs the same :class:`StoredObject`.
        """
        data_path, meta_path = self._paths_for(key)
        sidecar = {
            "key": key,
            "content_type": content_type,
            "metadata": dict(metadata) if metadata is not None else {},
        }
        # Write the data first, then the sidecar: a present data file without a
        # sidecar is still readable (defaults applied), while the reverse would
        # claim an object that has no bytes.
        self._write_atomic(data_path, bytes(data))
        self._write_atomic(meta_path, json.dumps(sidecar).encode("utf-8"))

    def get(self, key: str) -> StoredObject:
        """Return the object stored under ``key``.

        Raises :class:`KeyError` when no object exists for ``key``, matching the
        port contract.
        """
        data_path, meta_path = self._paths_for(key)
        try:
            data = data_path.read_bytes()
        except FileNotFoundError as exc:
            raise KeyError(key) from exc
        content_type = "application/octet-stream"
        metadata: dict[str, str] = {}
        try:
            sidecar = json.loads(meta_path.read_bytes())
        except FileNotFoundError:
            sidecar = {}
        if isinstance(sidecar, dict):
            raw_type = sidecar.get("content_type")
            if isinstance(raw_type, str):
                content_type = raw_type
            raw_meta = sidecar.get("metadata")
            if isinstance(raw_meta, dict):
                metadata = {str(k): str(v) for k, v in raw_meta.items()}
        return StoredObject(
            key=key,
            data=data,
            content_type=content_type,
            metadata=metadata,
        )

    def exists(self, key: str) -> bool:
        """Return whether an object is stored under ``key``."""
        data_path, _ = self._paths_for(key)
        return data_path.exists()

    def delete(self, key: str) -> None:
        """Remove the object stored under ``key``.

        Deleting a key that does not exist is a no-op, so deletion is idempotent.
        """
        data_path, meta_path = self._paths_for(key)
        data_path.unlink(missing_ok=True)
        meta_path.unlink(missing_ok=True)
