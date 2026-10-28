"""Trivial smoke test proving the test command runs green for apps/api."""

from edu_it_api import __version__


def test_api_package_imports_and_has_version() -> None:
    assert __version__ == "0.0.0"
