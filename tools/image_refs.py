"""Image reference helpers (kubernetes-deployment, Req 7.11, 7.12, 12.11).

An image reference is pinned when it has the form ``repository@sha256:<64 lowercase hex>``.
A reference with only a tag is rejected.
"""

from __future__ import annotations

import re

__all__ = ["DIGEST_PATTERN", "compose_image_ref", "is_pinned_digest", "is_pinned_image_ref"]

#: Same pattern as the ``values.schema.json`` digest rule (design values contract).
DIGEST_PATTERN = r"sha256:[a-f0-9]{64}"

_DIGEST_RE = re.compile(DIGEST_PATTERN)
_REF_RE = re.compile(r"[^\s@]+@" + DIGEST_PATTERN)


def is_pinned_digest(digest: object) -> bool:
    """True when ``digest`` is exactly ``sha256:`` followed by 64 lowercase hex characters."""
    return isinstance(digest, str) and _DIGEST_RE.fullmatch(digest) is not None


def is_pinned_image_ref(ref: object) -> bool:
    """True if and only if ``ref`` is ``repository@sha256:<64 lowercase hex>``."""
    return isinstance(ref, str) and _REF_RE.fullmatch(ref) is not None


def compose_image_ref(repository: str, digest: str) -> str:
    """Compose ``repository@digest`` as the Chart image helper does (design D8)."""
    return f"{repository}@{digest}"
