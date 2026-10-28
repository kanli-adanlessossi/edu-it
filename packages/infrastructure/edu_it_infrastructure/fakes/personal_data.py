"""The in-memory personal-data gateway fake (task 19.1, Req 5.2, 14.1-14.4).

:class:`In_Memory_PersonalDataGateway` implements the domain
:class:`~edu_it_domain.ports.personal_data.PersonalDataGateway` port with
in-process storage, so the GDPR use cases (:mod:`edu_it_application.gdpr`) can be
exercised in tests and local development without a database.

The fake is substitutable for the PostgreSQL gateway without changing domain or
application code (Req 5.2): it satisfies the same protocol and matches the port's
documented semantics — an export covers **every** personal-data table (empty
lists for tables the User has no rows in, Req 14.1), and a delete is **atomic
across all personal-data tables** (Req 14.2, 14.4). To exercise the atomicity
requirement the fake accepts an optional ``fail_on_table`` so a test can inject a
failure deleting from one table and assert the other tables are left unchanged.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

from edu_it_domain.ports.personal_data import PERSONAL_DATA_TABLES, PersonalDataGateway

__all__ = ["In_Memory_PersonalDataGateway", "PersonalDataDeletionFailure"]


class PersonalDataDeletionFailure(Exception):
    """Raised by the fake to simulate a per-table delete failure (Req 14.4).

    Lets a test drive the atomic-deletion path: the gateway raises this while
    deleting from the configured table and leaves every personal-data table
    unchanged, so the delete-account use case surfaces the failure without any
    partial deletion persisting.
    """

    def __init__(self, table: str) -> None:
        super().__init__(f"injected deletion failure on table {table!r}")
        self.table = table


class In_Memory_PersonalDataGateway(PersonalDataGateway):
    """A :class:`PersonalDataGateway` backed by in-process per-table storage.

    Satisfies the
    :class:`~edu_it_domain.ports.personal_data.PersonalDataGateway` protocol, so
    the GDPR use cases export and delete exactly as they do against the
    PostgreSQL gateway. Rows are stored per ``(table, user_stable_id)`` so the
    fake can answer "this User's rows in this table" directly.
    """

    __test__ = False

    def __init__(self, *, fail_on_table: str | None = None) -> None:
        """Create an empty gateway.

        ``fail_on_table``, when set to a personal-data table name, makes
        :meth:`delete_user_data` raise :class:`PersonalDataDeletionFailure` while
        deleting from that table **without removing anything**, so a test can
        assert the atomic-failure behavior (Req 14.4).
        """
        if fail_on_table is not None and fail_on_table not in PERSONAL_DATA_TABLES:
            raise ValueError(f"{fail_on_table!r} is not a personal-data table")
        self._rows: dict[str, dict[str, list[dict[str, Any]]]] = {
            table: {} for table in PERSONAL_DATA_TABLES
        }
        self._fail_on_table = fail_on_table

    def add_row(self, table: str, user_stable_id: str, row: Mapping[str, Any]) -> None:
        """Record one ``row`` of ``table`` owned by ``user_stable_id`` (test helper)."""
        if table not in self._rows:
            raise ValueError(f"{table!r} is not a personal-data table")
        self._rows[table].setdefault(user_stable_id, []).append(dict(row))

    def export_user_data(self, user_stable_id: str) -> Mapping[str, Sequence[Mapping[str, Any]]]:
        """Return the User's rows across every personal-data table (Req 14.1).

        One key per table in
        :data:`~edu_it_domain.ports.personal_data.PERSONAL_DATA_TABLES`, each
        value a deep copy of the User's rows in that table (empty when none), so
        the export covers exactly the personal-data tables and a caller cannot
        mutate stored state through the returned rows.
        """
        return {
            table: [deepcopy(row) for row in self._rows[table].get(user_stable_id, [])]
            for table in PERSONAL_DATA_TABLES
        }

    def delete_user_data(self, user_stable_id: str) -> int:
        """Remove the User's rows from every personal-data table atomically (Req 14.2, 14.4).

        When ``fail_on_table`` is configured the delete raises
        :class:`PersonalDataDeletionFailure` and removes **nothing**, so every
        personal-data table is left unchanged (Req 14.4). Otherwise it removes the
        User's rows from every table and returns the total removed.
        """
        if self._fail_on_table is not None:
            raise PersonalDataDeletionFailure(self._fail_on_table)
        removed = 0
        for table in PERSONAL_DATA_TABLES:
            rows = self._rows[table].pop(user_stable_id, None)
            if rows is not None:
                removed += len(rows)
        return removed

    def row_count(self, table: str, user_stable_id: str) -> int:
        """Return how many rows of ``table`` the User has (test inspection)."""
        return len(self._rows[table].get(user_stable_id, []))
