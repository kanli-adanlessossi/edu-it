#!/usr/bin/env python3
"""Digest-only image reference check for the Chart values (Req 7.11, 7.12, 12.11).

Every ``images.<name>`` entry must have a non-empty ``repository`` without a tag or digest
and a ``digest`` of the form ``sha256:<64 lowercase hex>``, so that the composed reference
``repository@digest`` is pinned. A mutable tag is never accepted as the sole identifier.

Usage::

    python tools/check_image_refs.py FILE [FILE ...]

Exit codes: 0 all pinned, 1 violations, 2 usage or read error.
"""

from __future__ import annotations

import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

try:  # run as a script (tools/ on sys.path) or imported as a module
    from image_refs import compose_image_ref, is_pinned_digest, is_pinned_image_ref
except ImportError:  # pragma: no cover
    from tools.image_refs import compose_image_ref, is_pinned_digest, is_pinned_image_ref

__all__ = ["check_image_refs", "main"]


def _repository_problem(repository: Any) -> str | None:
    if not isinstance(repository, str) or not repository.strip():
        return "repository is missing or empty"
    if "@" in repository:
        return "repository must not contain a digest; set the digest key"
    last = repository.rsplit("/", 1)[-1]
    if ":" in last:
        return f"repository {repository!r} carries a tag; use the digest key only"
    return None


def check_image_refs(values: Mapping[str, Any]) -> list[str]:
    """Return the violations in ``values['images']``; an empty list means every image is pinned."""
    images = values.get("images")
    if not isinstance(images, Mapping) or not images:
        return ["images is missing or empty"]

    errors: list[str] = []
    for name, entry in images.items():
        prefix = f"images.{name}"
        if not isinstance(entry, Mapping):
            errors.append(f"{prefix} is not a mapping with repository and digest")
            continue
        for key in entry:
            if key not in ("repository", "digest"):
                errors.append(f"{prefix}.{key} is not allowed; use repository and digest only")
        problem = _repository_problem(entry.get("repository"))
        if problem:
            errors.append(f"{prefix}.repository: {problem}")
        digest = entry.get("digest")
        if not is_pinned_digest(digest):
            errors.append(
                f"{prefix}.digest {digest!r} is not a sha256 digest (sha256: + 64 lowercase hex)"
            )
        elif problem is None and not is_pinned_image_ref(
            compose_image_ref(entry["repository"], digest)
        ):
            errors.append(f"{prefix} does not compose to a pinned image reference")
    return errors


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if not args:
        print(__doc__, file=sys.stderr)
        return 2
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - environment problem
        print(f"error: {exc}", file=sys.stderr)
        return 2

    failed = False
    for name in args:
        try:
            values = yaml.safe_load(Path(name).read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            print(f"error: {name}: {exc}", file=sys.stderr)
            return 2
        for message in check_image_refs(values):
            failed = True
            print(f"{name}: {message}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
