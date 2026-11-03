"""Refreshable credential source for the Live_Provider (ai-content Req 1.7, 1.8, 1.10, 1.11).

A :class:`CredentialSource` hands the live transport the API key it should use
and can re-read it once after the provider refuses the current key (HTTP 401 or
403), which is how a rotated short-term key is picked up (ADR 0007, addendum 1).

Two implementations:

* :class:`EnvironmentCredentialSource` holds the long-term key from
  ``LLM_PROVIDER_API_KEY`` (local development and record mode).
* :class:`SecretsManagerCredentialSource` reads the secret named by
  ``LLM_PROVIDER_API_KEY_SECRET_ID`` from AWS Secrets Manager (production). The
  Secrets Manager client (``boto3``) is imported here, in the infrastructure
  layer, and only on first use.

Secrecy. The key is returned to the transport and nowhere else. Logs, errors and
records carry only :func:`credential_fingerprint`, which is non-reversible. The
fingerprint always describes the key most recently handed out, so it follows a
rotation.
"""

from __future__ import annotations

import hashlib
import importlib
import logging
from collections.abc import Callable
from typing import Any, Protocol, runtime_checkable

__all__ = [
    "CredentialSource",
    "CredentialUnavailableError",
    "EnvironmentCredentialSource",
    "SecretsManagerCredentialSource",
    "credential_fingerprint",
]

_log = logging.getLogger(__name__)


class CredentialUnavailableError(Exception):
    """The key could not be read (missing, empty or unreadable). The message never holds a key."""


def credential_fingerprint(api_key: str) -> str:
    """Stable, non-reversible identifier of ``api_key`` (never the key itself)."""
    return "key:" + hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:16]


@runtime_checkable
class CredentialSource(Protocol):
    """Where the live transport gets its API key."""

    @property
    def fingerprint(self) -> str | None:
        """Fingerprint of the key most recently handed out; ``None`` before the first read."""
        ...

    def current(self) -> str:
        """The key to use now. Reads on first use, then returns the cached key."""
        ...

    def refresh(self) -> str:
        """Re-read the key from its origin and return it."""
        ...


class EnvironmentCredentialSource:
    """A fixed long-term key taken from ``LLM_PROVIDER_API_KEY``."""

    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise CredentialUnavailableError("no API key configured for the live provider")
        self._key = api_key
        self._fingerprint: str | None = None

    @property
    def fingerprint(self) -> str | None:
        return self._fingerprint

    def current(self) -> str:
        self._fingerprint = credential_fingerprint(self._key)
        return self._key

    def refresh(self) -> str:
        return self.current()


class SecretsManagerCredentialSource:
    """The key stored as the secret string of an AWS Secrets Manager secret."""

    def __init__(self, secret_id: str, *, client: Any | None = None) -> None:
        """``client`` is for tests; by default a ``boto3`` client is created on first use."""
        if not secret_id:
            raise CredentialUnavailableError("no Secrets Manager secret id configured")
        self._secret_id = secret_id
        self._client = client
        self._key: str | None = None
        self._fingerprint: str | None = None

    @property
    def fingerprint(self) -> str | None:
        return self._fingerprint

    def current(self) -> str:
        if self._key is None:
            return self.refresh()
        return self._key

    def refresh(self) -> str:
        previous = self._fingerprint
        key = self._read()
        self._key = key
        self._fingerprint = credential_fingerprint(key)
        if previous is not None and previous != self._fingerprint:
            _log.info("live provider credential changed: %s -> %s", previous, self._fingerprint)
        return key

    def _read(self) -> str:
        try:
            response = self._get_client().get_secret_value(SecretId=self._secret_id)
        except CredentialUnavailableError:
            raise
        except Exception as err:  # boto3 raises its own hierarchy; never echo err args
            raise CredentialUnavailableError(
                f"cannot read secret {self._secret_id!r}: {type(err).__name__}"
            ) from None
        secret = response.get("SecretString") if isinstance(response, dict) else None
        if not isinstance(secret, str) or not secret.strip():
            raise CredentialUnavailableError(
                f"secret {self._secret_id!r} has no usable SecretString"
            )
        return secret.strip()

    def _get_client(self) -> Any:
        if self._client is None:
            factory: Callable[..., Any]
            try:
                factory = importlib.import_module("boto3").client
            except ImportError:
                raise CredentialUnavailableError(
                    "boto3 is required to read LLM_PROVIDER_API_KEY_SECRET_ID"
                ) from None
            self._client = factory("secretsmanager")
        return self._client
