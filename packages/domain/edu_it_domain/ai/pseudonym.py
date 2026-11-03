"""Per-run pseudonymous learner handles (ai-content Req 3.3, 17.1).

``HMAC-SHA256(key, run_id + ":" + user_id)``, truncated and prefixed. The handle
differs between runs and cannot be derived from the account identifier without
the key.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Final

__all__ = ["HANDLE_PREFIX", "handle"]

HANDLE_PREFIX: Final = "h_"
_HEX_LENGTH: Final = 24


def handle(key: str | bytes, run_id: str, user_id: str) -> str:
    """Return the Pseudonymous_Handle of ``user_id`` for one advisor run."""
    key_bytes = key.encode("utf-8") if isinstance(key, str) else key
    if not key_bytes:
        raise ValueError("handle key must not be empty")
    if not run_id or not user_id:
        raise ValueError("run_id and user_id must not be empty")
    message = f"{run_id}:{user_id}".encode()
    digest = hmac.new(key_bytes, message, hashlib.sha256).hexdigest()
    return HANDLE_PREFIX + digest[:_HEX_LENGTH]
