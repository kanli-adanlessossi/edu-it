"""Trivial smoke test proving the test command runs green for the domain.

Replaced by real domain tests in later tasks; exists now so the uniform
``test`` command has something to execute and can be verified clean.
"""

from edu_it_domain import __version__


def test_domain_package_imports_and_has_version() -> None:
    assert __version__ == "0.0.0"
