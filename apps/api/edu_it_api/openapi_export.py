"""Export the published OpenAPI document for a major version (task 22, Req 16.1).

The ``apps/web`` build generates its typed client from the OpenAPI_Document of
the *targeted major version* (Req 16.2). That document is the one FastAPI
publishes for the matching versioned sub-application (see
:mod:`edu_it_api.versioning`): for major version ``m`` it is served at
``/v{m}/openapi.json``.

This module renders that same per-version document to JSON without running a
live server, and exposes a tiny CLI so the web build can materialise the
targeted major version's document to a file before it compiles application code.

Usage::

    python -m edu_it_api.openapi_export --major 1 --out apps/web/openapi/v1.json

On an unknown major version it exits non-zero and reports the unknown version,
so a build targeting a version this service does not publish fails loudly rather
than silently producing an empty or stale client.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from edu_it_api.app import create_app
from edu_it_api.versioning import (
    SUPPORTED_MAJOR_VERSIONS,
    version_path_prefix,
)

__all__ = ["main", "openapi_document_for_major"]


def openapi_document_for_major(major: int) -> dict[str, Any]:
    """Return the OpenAPI_Document the service publishes for ``major``.

    Raises :class:`ValueError` when ``major`` is not a supported major version,
    so a caller targeting an unpublished version is told exactly what went wrong
    instead of receiving an empty document.
    """
    if major not in SUPPORTED_MAJOR_VERSIONS:
        supported = ", ".join(f"v{m}" for m in SUPPORTED_MAJOR_VERSIONS)
        raise ValueError(
            f"No OpenAPI document is published for major version v{major}; "
            f"supported major versions are: {supported}."
        )

    app = create_app()
    prefix = version_path_prefix(major)
    # The versioned sub-app is mounted at /v{major}; find it and ask it for its
    # single OpenAPI schema (the document published for this major version).
    for route in app.routes:
        mount_path = getattr(route, "path", None)
        mounted_app = getattr(route, "app", None)
        if mount_path == prefix and mounted_app is not None:
            document = mounted_app.openapi()
            assert isinstance(document, dict)
            return document

    raise ValueError(
        f"No OpenAPI document is published for major version v{major}; "
        f"the versioned application is not mounted at {prefix}."
    )


def main(argv: list[str] | None = None) -> int:
    """CLI: write the targeted major version's OpenAPI document to a file.

    Returns a process exit status: ``0`` on success, non-zero on an unknown major
    version or a write failure (with the reason on stderr).
    """
    parser = argparse.ArgumentParser(
        prog="edu-it-openapi-export",
        description="Export the OpenAPI document published for a major version.",
    )
    parser.add_argument(
        "--major",
        type=int,
        required=True,
        help="Targeted API major version (e.g. 1 for /v1).",
    )
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="Destination file for the OpenAPI document (JSON).",
    )
    args = parser.parse_args(argv)

    try:
        document = openapi_document_for_major(args.major)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    try:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps(document, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        print(f"Failed to write OpenAPI document to {args.out}: {exc}", file=sys.stderr)
        return 1

    print(f"Wrote OpenAPI document for v{args.major} to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
