"""Unit tests for per-major-version OpenAPI publication (task 22, Req 16.1).

The API publishes exactly one OpenAPI_Document per major version, path-prefixed
``/v{major}`` (D2 Section 3). These tests assert that contract against the built
application and the exporter the web build uses to obtain the targeted major
version's document.

No HTTP client is used (the project pins none): the mounted versioned sub-apps
are resolved from the application's routes and asked for their single OpenAPI
schema directly — the same technique the exporter uses and the task-9 health
tests use for route callables.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from edu_it_api.app import create_app
from edu_it_api.openapi_export import main, openapi_document_for_major
from edu_it_api.versioning import SUPPORTED_MAJOR_VERSIONS, version_path_prefix
from fastapi import FastAPI


def _mounted_version_apps(app: FastAPI) -> dict[str, FastAPI]:
    """Map each ``/v{major}`` mount path to its sub-application."""
    mounts: dict[str, FastAPI] = {}
    for route in app.routes:
        mount_path = getattr(route, "path", None)
        sub_app = getattr(route, "app", None)
        if isinstance(mount_path, str) and isinstance(sub_app, FastAPI):
            mounts[mount_path] = sub_app
    return mounts


def test_exactly_one_openapi_document_per_major_version() -> None:
    """Each supported major version serves exactly one OpenAPI document (Req 16.1)."""
    app = create_app()
    mounts = _mounted_version_apps(app)

    for major in SUPPORTED_MAJOR_VERSIONS:
        prefix = version_path_prefix(major)
        assert prefix in mounts, f"no versioned sub-app mounted at {prefix}"
        sub_app = mounts[prefix]
        document: dict[str, Any] = sub_app.openapi()
        # A well-formed OpenAPI document with the required top-level members.
        assert isinstance(document, dict)
        assert isinstance(document.get("openapi"), str)
        assert isinstance(document.get("info"), dict)
        assert isinstance(document.get("paths"), dict)
        # Exactly one document per version: the sub-app exposes a single
        # openapi_url and the document is served under the version prefix.
        assert sub_app.openapi_url == "/openapi.json"


def test_one_mounted_version_app_per_supported_major_version() -> None:
    """There is exactly one versioned mount per supported major version (Req 16.1)."""
    app = create_app()
    mounts = _mounted_version_apps(app)
    expected = {version_path_prefix(m) for m in SUPPORTED_MAJOR_VERSIONS}
    assert set(mounts) == expected
    assert len(mounts) == len(SUPPORTED_MAJOR_VERSIONS)


def test_openapi_document_for_major_returns_the_published_document() -> None:
    document = openapi_document_for_major(1)
    assert document["openapi"]
    assert document["info"]["title"] == "EDU-IT API v1"


def test_openapi_document_for_major_rejects_unknown_version() -> None:
    unknown = max(SUPPORTED_MAJOR_VERSIONS) + 1
    with pytest.raises(ValueError, match=f"v{unknown}"):
        openapi_document_for_major(unknown)


def test_export_cli_writes_the_document(tmp_path: Path) -> None:
    out = tmp_path / "v1.json"
    exit_code = main(["--major", "1", "--out", str(out)])
    assert exit_code == 0
    assert out.exists()
    document = json.loads(out.read_text(encoding="utf-8"))
    assert document["openapi"]
    assert isinstance(document["paths"], dict)


def test_export_cli_fails_non_zero_for_unknown_version(tmp_path: Path) -> None:
    unknown = max(SUPPORTED_MAJOR_VERSIONS) + 1
    out = tmp_path / "unknown.json"
    exit_code = main(["--major", str(unknown), "--out", str(out)])
    assert exit_code != 0
    # No document is written for a version the service does not publish.
    assert not out.exists()
