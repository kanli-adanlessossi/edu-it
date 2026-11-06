#!/usr/bin/env python3
"""Production Values_File consistency check (kubernetes-deployment, Req 2.5, 9.4, 9.5, 16.11).

A production Values_File is accepted only if:

* ``labs.sessionTtlSeconds`` equals ``LAB_SESSION_TTL`` (Req 2.5, 9.4),
* ``ingress.websocket.readTimeoutSeconds`` and ``ingress.websocket.sendTimeoutSeconds``
  are each at least that constant (Req 9.4, 9.5),
* no ``kindTest.sessionTtlSeconds`` key exists (Req 16.11): the short-TTL override is
  only for the kind test values file.

Usage::

    python tools/check_values_consistency.py FILE [FILE ...]

Exit codes: 0 consistent, 1 violations, 2 usage or read error.
"""

from __future__ import annotations

import sys
from collections.abc import Mapping
from datetime import timedelta
from pathlib import Path
from typing import Any

__all__ = ["check_values_consistency", "main"]

_MISSING = object()


def _get(values: Mapping[str, Any], path: str) -> Any:
    node: Any = values
    for part in path.split("."):
        if not isinstance(node, Mapping) or part not in node:
            return _MISSING
        node = node[part]
    return node


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def check_values_consistency(values: Mapping[str, Any], lab_session_ttl: timedelta) -> list[str]:
    """Return the violations of a production Values_File; an empty list means accepted."""
    ttl = lab_session_ttl.total_seconds()
    errors: list[str] = []

    session_ttl = _get(values, "labs.sessionTtlSeconds")
    if not _is_number(session_ttl):
        errors.append("labs.sessionTtlSeconds is missing or not a number")
    elif session_ttl != ttl:
        errors.append(
            f"labs.sessionTtlSeconds is {session_ttl}, expected LAB_SESSION_TTL ({ttl:g} s)"
        )

    for key in ("readTimeoutSeconds", "sendTimeoutSeconds"):
        path = f"ingress.websocket.{key}"
        timeout = _get(values, path)
        if not _is_number(timeout):
            errors.append(f"{path} is missing or not a number")
        elif timeout < ttl:
            errors.append(f"{path} is {timeout}, less than LAB_SESSION_TTL ({ttl:g} s)")

    if _get(values, "kindTest.sessionTtlSeconds") is not _MISSING:
        errors.append("kindTest.sessionTtlSeconds must not be set in a production Values_File")

    return errors


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if not args:
        print(__doc__, file=sys.stderr)
        return 2
    try:
        import yaml
        from edu_it_domain.config import LAB_SESSION_TTL
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
        for message in check_values_consistency(values, LAB_SESSION_TTL):
            failed = True
            print(f"{name}: {message}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
