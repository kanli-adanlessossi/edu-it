"""Property-based test for the privileged-user CLI (task 18.2).

Feature: foundations, Property 18: The privileged-user CLI assigns exactly the requested privileged role

Property 18 (design.md): *For any* syntactically valid target email with a role
of reviewer or admin (and a track scope when the role is reviewer), running the
CLI results in a User for that email carrying exactly the specified role
assignment (with the track scope for a reviewer) and a zero exit; and *for any*
invalid input (unknown role, missing or syntactically invalid email, or a
reviewer role without a track scope) the CLI makes no change to any User, exits
non-zero, and reports the invalid input.

This drives the real CLI entry point
(:func:`edu_it_worker.privileged_user_cli.main`) through the full
``argv -> use case -> exit status`` shell, with the production
:class:`~edu_it_application.assign_role.AssignRoleUseCase` wired to the real
infrastructure identity fakes (:mod:`edu_it_infrastructure.fakes`). Only the
use-case *factory* is injected so no database is needed; the parser, the
use case, and the exit-status mapping under test are the production ones.

Each example draws one of two disjoint cases:

- a **valid grant**: a syntactically valid email (<= 254 chars) and a role of
  reviewer or admin (Req 13.2), carrying a non-blank track scope exactly when
  the role is reviewer; and
- an **invalid request** in exactly one of the three rejected shapes (Req 13.4):
  an unknown role, a missing or syntactically invalid email, or a reviewer role
  with no track scope.

The property asserts:

1. **Valid grant** (Req 13.2): the CLI exits ``0``; a User exists for the email;
   that User carries exactly the requested role; and the track scope equals the
   supplied scope for a reviewer and is ``None`` for admin.
2. **Invalid request** (Req 13.4): the CLI exits non-zero (either by returning a
   non-zero status or by argparse raising ``SystemExit`` with a non-zero code);
   no User and no role assignment were created; and an error indication is
   emitted on stderr.

Validates: Requirements 13.2, 13.4

Uses Hypothesis, the pinned Python property-based testing library
(docs/adr/0001-versions.md).
"""

from __future__ import annotations

from collections.abc import Iterator
from enum import Enum

import pytest
from edu_it_application.assign_role import AssignRoleUseCase
from edu_it_domain.ports.role_assignment import (
    ROLE_ADMIN,
    ROLE_LEARNER,
    ROLE_REVIEWER,
)
from edu_it_infrastructure.fakes import (
    In_Memory_RoleAssignmentRepository,
    In_Memory_UserRepository,
)
from edu_it_worker.privileged_user_cli import main
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

# Roles the property treats as a valid privileged grant: reviewer or admin, per
# the property text (design.md Property 18). Reviewer carries a track scope;
# admin carries none.
_GRANTABLE_ROLES = (ROLE_REVIEWER, ROLE_ADMIN)

# Roles that are not grantable through the CLI (Req 13.2, 13.3): the learner role
# (self-service only) and arbitrary unknown strings.
_UNGRANTABLE_ROLES = (ROLE_LEARNER, "wizard", "superuser", "", "Admin", "REVIEWER")

# Syntactically valid emails, bounded to the CLI's 254-char maximum.
_valid_emails = st.emails().filter(lambda e: len(e) <= 254)

# Strings that are not syntactically valid emails (no single "@" separating a
# non-empty local part from a dotted, non-empty domain) or are empty/whitespace.
_invalid_emails = st.one_of(
    st.just(""),
    st.just("   "),
    st.text(alphabet=st.characters(blacklist_characters="@"), min_size=1, max_size=30),
    st.just("no-domain@"),
    st.just("@no-local.test"),
    st.just("two@@ats.test"),
    st.just("spaces in@email.test"),
    st.just("plaintext"),
)

# Non-blank track scopes for the reviewer role.
_track_scopes = st.text(
    alphabet=st.characters(min_codepoint=0x21, max_codepoint=0x7E),
    min_size=1,
    max_size=40,
)


def _flag(name: str, value: str) -> str:
    """Render ``--name=value`` so a value that begins with ``-`` is not misread.

    A syntactically valid email may begin with ``-`` (e.g. ``-@a.co``); passed as
    the separate token ``--email -@a.co`` argparse would treat the value as a new
    option. The ``--flag=value`` form is the standard way to pass a dash-leading
    value on the command line, so the test drives the real CLI the way an
    operator would for such a value — it does not work around a production defect.
    """
    return f"{name}={value}"


def _ids() -> Iterator[str]:
    index = 0
    while True:
        yield f"user-{index}"
        index += 1


class _Harness:
    """Wires the production use case to the in-memory identity fakes.

    Exposes a ``factory`` matching the CLI's ``use_case_factory`` seam so
    :func:`main` runs its real parser and exit-status mapping against an
    in-memory store, with no database.
    """

    def __init__(self) -> None:
        self.users = In_Memory_UserRepository()
        self.roles = In_Memory_RoleAssignmentRepository()
        ids = _ids()
        self.use_case = AssignRoleUseCase(
            users=self.users,
            role_assignments=self.roles,
            stable_id_factory=lambda: next(ids),
        )

    def factory(self, _database_url: str) -> AssignRoleUseCase:
        return self.use_case


class _InvalidShape(Enum):
    """The three rejected request shapes the CLI must refuse (Req 13.4)."""

    UNKNOWN_ROLE = "unknown_role"
    INVALID_EMAIL = "invalid_email"
    REVIEWER_WITHOUT_TRACK_SCOPE = "reviewer_without_track_scope"


@given(
    email=_valid_emails,
    role=st.sampled_from(_GRANTABLE_ROLES),
    track_scope=_track_scopes,
)
@settings(max_examples=200)
def test_property_18_valid_grant_assigns_exactly_the_requested_role(
    email: str,
    role: str,
    track_scope: str,
) -> None:
    """Feature: foundations, Property 18: The privileged-user CLI assigns exactly the requested privileged role.

    A valid grant (syntactically valid email, role of reviewer or admin, track
    scope exactly when reviewer) exits ``0`` and leaves a User for the email
    carrying exactly the requested role — with the supplied track scope for a
    reviewer and none for admin (Req 13.2).
    """
    harness = _Harness()

    argv = [_flag("--email", email), _flag("--role", role)]
    if role == ROLE_REVIEWER:
        argv.append(_flag("--track-scope", track_scope))

    code = main(argv, use_case_factory=harness.factory)

    assert code == 0
    assert len(harness.users) == 1
    assert len(harness.roles) == 1

    user = harness.users.get_by_email(email)
    assert user is not None
    assignment = harness.roles.get_for_user(user.stable_id)
    assert assignment is not None
    assert assignment.role == role
    if role == ROLE_REVIEWER:
        assert assignment.track_scope == track_scope
    else:
        assert assignment.track_scope is None


@given(
    data=st.data(),
    shape=st.sampled_from(_InvalidShape),
    valid_email=_valid_emails,
)
@settings(max_examples=200, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_property_18_invalid_input_makes_no_change_and_exits_non_zero(
    data: st.DataObject,
    shape: _InvalidShape,
    valid_email: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Feature: foundations, Property 18: The privileged-user CLI assigns exactly the requested privileged role.

    An invalid request — an unknown role, a missing/invalid email, or a reviewer
    role without a track scope — makes no change to any User, exits non-zero, and
    reports the invalid input (Req 13.4).
    """
    harness = _Harness()

    if shape is _InvalidShape.UNKNOWN_ROLE:
        role = data.draw(st.sampled_from(_UNGRANTABLE_ROLES))
        argv = [_flag("--email", valid_email), _flag("--role", role)]
    elif shape is _InvalidShape.INVALID_EMAIL:
        bad_email = data.draw(_invalid_emails)
        role = data.draw(st.sampled_from(_GRANTABLE_ROLES))
        argv = [_flag("--email", bad_email), _flag("--role", role)]
        if role == ROLE_REVIEWER:
            argv.append(_flag("--track-scope", data.draw(_track_scopes)))
    else:  # REVIEWER_WITHOUT_TRACK_SCOPE
        argv = [_flag("--email", valid_email), _flag("--role", ROLE_REVIEWER)]

    # The CLI refuses either by returning a non-zero status (a use-case
    # rejection) or by argparse raising SystemExit with a non-zero code (an
    # unknown role rejected at parse time). Both are "exit non-zero".
    try:
        code = main(argv, use_case_factory=harness.factory)
    except SystemExit as exited:
        assert exited.code is not None
        assert exited.code != 0
    else:
        assert code != 0

    # No change to any User and no role assignment recorded (Req 13.4).
    assert len(harness.users) == 0
    assert len(harness.roles) == 0

    # An error indication identifying the invalid input is emitted on stderr
    # (either the use case's operator message or argparse's usage error).
    captured = capsys.readouterr()
    assert captured.err.strip() != ""
