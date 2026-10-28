"""Trivial smoke test proving the test command runs green for infrastructure."""

from edu_it_infrastructure import __version__


def test_infrastructure_package_imports_and_has_version() -> None:
    assert __version__ == "0.0.0"
