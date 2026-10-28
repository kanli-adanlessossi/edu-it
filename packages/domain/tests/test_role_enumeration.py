"""Unit test for the identity role enumeration (task 18.3, Req 13.1).

Requirement 13.1 states the identity model SHALL support the learner, reviewer,
author, and admin Roles. This test pins that enumeration down to a set equality:
the model supports *exactly* those four roles — no more and no fewer — so any
future addition or removal of a role must update this test deliberately rather
than slip through silently.

The role vocabulary lives in ``edu_it_domain.ports.role_assignment`` (task 18.1)
and is re-exported from the ``edu_it_domain.ports`` package; this test checks
both the authoritative ``ROLES`` set and that the individual role constants are
distinct and line up with it.
"""

from __future__ import annotations

from edu_it_domain.ports import (
    ROLE_ADMIN,
    ROLE_AUTHOR,
    ROLE_LEARNER,
    ROLE_REVIEWER,
    ROLES,
)

#: The four roles Requirement 13.1 names, by their string values.
EXPECTED_ROLES = {"learner", "reviewer", "author", "admin"}


def test_identity_model_supports_exactly_the_four_roles() -> None:
    # Set equality: exactly learner, reviewer, author, and admin — no more and
    # no fewer (Req 13.1).
    assert set(ROLES) == EXPECTED_ROLES


def test_role_constants_match_the_expected_values() -> None:
    assert ROLE_LEARNER == "learner"
    assert ROLE_REVIEWER == "reviewer"
    assert ROLE_AUTHOR == "author"
    assert ROLE_ADMIN == "admin"


def test_role_constants_are_the_members_of_the_roles_set() -> None:
    # The named constants and the closed ROLES set describe the same vocabulary.
    assert set(ROLES) == {ROLE_LEARNER, ROLE_REVIEWER, ROLE_AUTHOR, ROLE_ADMIN}


def test_the_four_roles_are_distinct() -> None:
    # Exactly four roles means four distinct values, not duplicates.
    assert len({ROLE_LEARNER, ROLE_REVIEWER, ROLE_AUTHOR, ROLE_ADMIN}) == 4
