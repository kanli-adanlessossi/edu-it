"""Trivial smoke test proving the test command runs green for application."""

from edu_it_application import __version__


def test_application_package_imports_and_has_version() -> None:
    assert __version__ == "0.0.0"
