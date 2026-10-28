"""Trivial smoke test proving the test command runs green for apps/worker."""

from edu_it_worker import __version__


def test_worker_package_imports_and_has_version() -> None:
    assert __version__ == "0.0.0"
