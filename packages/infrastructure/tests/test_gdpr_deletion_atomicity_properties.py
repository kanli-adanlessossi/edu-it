"""Property-based test for deletion completeness and atomicity (task 19.4).

Feature: foundations, Property 20: Account deletion is complete and atomic

Property 20: *For any* User and any arrangement of rows across the personal-data
tables, a confirmed deletion removes every personal-data row referencing that
User across all personal-data tables and leaves no Session of theirs able to
authenticate (Req 14.2, 14.3); and *for any* injected failure deleting from a
single personal-data table, the deletion leaves every personal-data table
unchanged and returns an error (Req 14.4).

This drives :class:`edu_it_application.gdpr.DeleteAccountUseCase` against the
infrastructure port fakes (:class:`In_Memory_PersonalDataGateway`,
:class:`In_Memory_SessionRepository`, :class:`In_Memory_SessionStore`,
:class:`Test_Clock`), exactly as the test/dev composition root wires them — no
database, Redis, or real clock. Two quantifiers are exercised:

- **Success path**: an arbitrary dataset across the personal-data tables and an
  arbitrary set of prior Sessions are seeded; after a confirmed deletion every
  personal-data table is empty for the User and every prior Session resolves to
  nothing.
- **Failure path**: the same dataset is seeded behind a gateway configured to
  fail deleting from a drawn table; the confirmed deletion raises
  :class:`AccountDeletionError` and every personal-data table is left exactly as
  seeded (the atomic all-or-nothing contract, Req 14.4).

Validates: Requirements 14.2, 14.3, 14.4

Uses Hypothesis, the pinned Python property-based testing library
(docs/adr/0001-versions.md).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from edu_it_application.gdpr import (
    AccountDeletionError,
    DeleteAccountRequest,
    DeleteAccountUseCase,
)
from edu_it_domain.ports.personal_data import PERSONAL_DATA_TABLES
from edu_it_domain.ports.repository import SessionRecord
from edu_it_infrastructure.fakes import (
    In_Memory_PersonalDataGateway,
    In_Memory_SessionRepository,
    In_Memory_SessionStore,
    Test_Clock,
)
from hypothesis import given, settings
from hypothesis import strategies as st

_T0 = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
_EXPIRES = _T0 + timedelta(days=14)
_USER = "subject"

_rows = st.dictionaries(
    keys=st.text(alphabet="abcdefghijklmnopqrstuvwxyz_", min_size=1, max_size=8),
    values=st.one_of(st.integers(), st.text(max_size=12), st.booleans()),
    max_size=3,
)
_per_table = st.fixed_dictionaries(
    {table: st.lists(_rows, max_size=3) for table in PERSONAL_DATA_TABLES}
)
_prior_session_counts = st.integers(min_value=0, max_value=6)


def _seed_gateway(
    dataset: dict[str, list[dict[str, object]]],
    *,
    fail_on_table: str | None = None,
) -> In_Memory_PersonalDataGateway:
    gateway = In_Memory_PersonalDataGateway(fail_on_table=fail_on_table)
    for table in PERSONAL_DATA_TABLES:
        for row in dataset[table]:
            gateway.add_row(table, _USER, row)
    return gateway


def _seed_sessions(
    count: int, clock: Test_Clock
) -> tuple[In_Memory_SessionRepository, In_Memory_SessionStore, list[str]]:
    sessions = In_Memory_SessionRepository()
    session_store = In_Memory_SessionStore(clock)
    refs = [f"ref-{n}" for n in range(count)]
    for ref in refs:
        sessions.add(SessionRecord(server_side_ref=ref, user_stable_id=_USER, expires_at=_EXPIRES))
        session_store.create(ref, _USER, expires_at=_EXPIRES)
    return sessions, session_store, refs


@settings(max_examples=100)
@given(dataset=_per_table, prior_sessions=_prior_session_counts)
def test_confirmed_deletion_is_complete(
    dataset: dict[str, list[dict[str, object]]], prior_sessions: int
) -> None:
    gateway = _seed_gateway(dataset)
    clock = Test_Clock(start=_T0)
    sessions, session_store, refs = _seed_sessions(prior_sessions, clock)
    use_case = DeleteAccountUseCase(
        personal_data=gateway, sessions=sessions, session_store=session_store, clock=clock
    )

    result = use_case.execute(DeleteAccountRequest(user_stable_id=_USER, confirmed=True))

    assert result.deleted is True
    # Every personal-data table is empty for the user after deletion (Req 14.2).
    after = gateway.export_user_data(_USER)
    assert all(after[table] == [] for table in PERSONAL_DATA_TABLES)
    # No prior Session can authenticate (Req 14.3).
    assert all(session_store.resolve(ref) is None for ref in refs)
    assert all(sessions.get(ref).is_revoked for ref in refs)  # type: ignore[union-attr]


@settings(max_examples=100)
@given(
    dataset=_per_table,
    prior_sessions=_prior_session_counts,
    fail_on_table=st.sampled_from(PERSONAL_DATA_TABLES),
)
def test_deletion_failure_is_atomic(
    dataset: dict[str, list[dict[str, object]]],
    prior_sessions: int,
    fail_on_table: str,
) -> None:
    gateway = _seed_gateway(dataset, fail_on_table=fail_on_table)
    clock = Test_Clock(start=_T0)
    sessions, session_store, _refs = _seed_sessions(prior_sessions, clock)
    use_case = DeleteAccountUseCase(
        personal_data=gateway, sessions=sessions, session_store=session_store, clock=clock
    )

    with pytest.raises(AccountDeletionError):
        use_case.execute(DeleteAccountRequest(user_stable_id=_USER, confirmed=True))

    # Atomic: every personal-data table is left exactly as it was seeded (Req 14.4).
    after = gateway.export_user_data(_USER)
    for table in PERSONAL_DATA_TABLES:
        assert after[table] == dataset[table]
