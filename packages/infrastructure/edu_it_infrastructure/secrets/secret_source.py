"""Secret handling over mounted files (task 8.1, Req 23.2, 23.6).

Secrets are never injected as environment variables (design D6). The chart mounts them as
optional volumes at ``/var/run/secrets/edu-it/<name>/`` and the Api and Worker read them
through a :class:`SecretSource`. Mounted files are refreshed by Kubernetes without a restart,
so a re-read returns the current value.

- :class:`FileSecretSource` reads one key (a path relative to the mount root, such as
  ``database/url``) and returns its current content.
- :func:`check_required_secrets` reports the required keys that are missing or unreadable and
  logs one record per missing key, naming only the key (Req 23.2).
- :class:`SecretHolder` caches the last good value of one key. :meth:`SecretHolder.refresh`
  replaces it only on success and never raises (Req 23.6).

No log record produced here ever contains a secret value.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from pathlib import Path, PurePosixPath
from typing import Final, Protocol

__all__ = [
    "DEFAULT_SECRETS_ROOT",
    "FileSecretSource",
    "MissingKeys",
    "SecretHolder",
    "SecretSource",
    "check_required_secrets",
]

_LOG = logging.getLogger(__name__)

DEFAULT_SECRETS_ROOT: Final[str] = "/var/run/secrets/edu-it"
"""Mount root of the chart's optional secret volumes."""

MissingKeys = tuple[str, ...]
"""The required keys that could not be read, in the order they were required."""


class SecretSource(Protocol):
    """Reads the current value of a secret key."""

    def read(self, key: str) -> str:
        """Return the current value of ``key``; raise ``OSError`` or ``KeyError`` if unavailable."""
        ...


class FileSecretSource:
    """A :class:`SecretSource` over files mounted under a root directory."""

    def __init__(self, root: str | Path = DEFAULT_SECRETS_ROOT) -> None:
        self._root = Path(root)

    def read(self, key: str) -> str:
        """Return the file content for ``key`` with trailing newlines removed.

        Raises ``KeyError`` for a key that is empty, absolute or escapes the root, and
        ``OSError`` when the file is absent or unreadable.
        """
        parts = PurePosixPath(key).parts
        if not parts or PurePosixPath(key).is_absolute() or ".." in parts:
            raise KeyError("invalid secret key")
        return (self._root.joinpath(*parts)).read_text(encoding="utf-8").rstrip("\r\n")


def check_required_secrets(source: SecretSource, required_keys: Iterable[str]) -> MissingKeys:
    """Return the required keys that are missing, logging one record per missing key.

    A key is missing when reading it raises or yields an empty value. Each log record
    names the key only, never a value.
    """
    missing: list[str] = []
    for key in required_keys:
        try:
            present = bool(source.read(key))
        except (OSError, KeyError, ValueError):
            present = False
        if not present:
            missing.append(key)
            _LOG.error("required secret is missing: key=%s", key)
    return tuple(missing)


class SecretHolder:
    """Caches the last good value of one secret key read through a :class:`SecretSource`."""

    def __init__(self, source: SecretSource, key: str) -> None:
        self._source = source
        self._key = key
        self._value: str | None = None

    @property
    def value(self) -> str | None:
        """The last good value, or ``None`` if no read has succeeded yet."""
        return self._value

    def refresh(self) -> bool:
        """Re-read the secret; return whether it succeeded. Never raises.

        On failure the cached value is kept and a warning naming only the key is logged.
        """
        try:
            self._value = self._source.read(self._key)
        except Exception as exc:  # refresh must never raise (Req 23.6)
            _LOG.warning(
                "secret refresh failed, keeping last value: key=%s error=%s",
                self._key,
                type(exc).__name__,
            )
            return False
        return True
