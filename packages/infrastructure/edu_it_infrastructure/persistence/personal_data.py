"""The PostgreSQL personal-data gateway (task 19.1, Req 14.1-14.4).

:class:`Postgres_PersonalDataGateway` implements the domain
:class:`~edu_it_domain.ports.personal_data.PersonalDataGateway` port over the
full data-model schema in
:mod:`~edu_it_infrastructure.persistence.metadata` using SQLAlchemy Core. It
backs GDPR data export (Req 14.1) and the atomic account deletion (Req 14.2,
14.4) the :mod:`edu_it_application.gdpr` use cases orchestrate.

Every personal-data table in ``docs/DATA-MODEL.md`` (D3) Section 13 is reached
from a User's ``stable_id`` through the foreign-key chain the schema records:
some tables carry ``user_id`` directly, others reach the User only through a
parent row (``module_progress`` through ``enrollment``, ``answer`` through
``quiz_attempt``, and so on). This module encodes, for each personal-data table,
the predicate selecting exactly that User's rows, as a ``user_id IN (...)``
subquery resolving back to the User.

- **Export** reads each table's rows for the User and returns a mapping keyed by
  every personal-data table name, with an empty list for any table the User has
  no rows in, so the export covers exactly the personal-data tables regardless of
  the data present (Req 14.1). The internal numeric primary key (``id``) and the
  internal foreign-key columns are never surfaced — only the API-safe columns.

- **Deletion** removes the User's rows from every personal-data table **in a
  single transaction**, child tables before the parents they reference, so a
  failure on any table rolls the whole transaction back and leaves every
  personal-data table unchanged while the error propagates (Req 14.2, 14.4). The
  caller (the delete-account use case) revokes the User's Sessions (Req 14.3).

Like the identity repositories this adapter is database-agnostic SQLAlchemy
Core, so the same code runs against PostgreSQL in production and SQLite in unit
tests, and it never redefines the schema — it reads and writes the shared Core
``Table`` objects.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from edu_it_domain.ports.personal_data import PERSONAL_DATA_TABLES, PersonalDataGateway
from sqlalchemy import Table, delete, select
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.sql.elements import ColumnElement

from edu_it_infrastructure.persistence.metadata import metadata as data_model_metadata
from edu_it_infrastructure.persistence.metadata import user as user_table

__all__ = ["Postgres_PersonalDataGateway"]

#: The internal numeric primary key column name, never surfaced in an export
#: (D3 Section 1, Req 4.4).
_INTERNAL_PK: str = "id"


class Postgres_PersonalDataGateway(PersonalDataGateway):
    """A :class:`PersonalDataGateway` backed by the PostgreSQL data-model schema.

    Satisfies the
    :class:`~edu_it_domain.ports.personal_data.PersonalDataGateway` protocol via
    SQLAlchemy Core, so the GDPR use cases export and delete a User's personal
    data exactly as they do against the in-memory fake. The export covers every
    personal-data table (Req 14.1) and the delete runs in a single transaction so
    it is atomic across all personal-data tables (Req 14.2, 14.4).
    """

    def __init__(self, engine: Engine) -> None:
        """Create the gateway over an existing ``engine``.

        Unlike the identity repositories this takes a ready engine: GDPR flows
        always run against a live database, so there is no lazy-URL path.
        """
        self._engine = engine

    def export_user_data(self, user_stable_id: str) -> Mapping[str, Sequence[Mapping[str, Any]]]:
        """Return the User's rows across every personal-data table (Req 14.1).

        One key per table in
        :data:`~edu_it_domain.ports.personal_data.PERSONAL_DATA_TABLES`, each
        value the list of the User's rows in that table (empty when none), with
        the internal ``id`` column stripped from every row so no internal key is
        surfaced.
        """
        export: dict[str, list[dict[str, Any]]] = {}
        with self._engine.connect() as connection:
            internal_id = _resolve_internal_user_id(connection, user_stable_id)
            for name in PERSONAL_DATA_TABLES:
                table = data_model_metadata.tables[name]
                if internal_id is None:
                    export[name] = []
                    continue
                predicate = _user_row_predicate(table, internal_id)
                rows = connection.execute(select(table).where(predicate)).mappings().all()
                export[name] = [_api_safe_row(dict(row)) for row in rows]
        return export

    def delete_user_data(self, user_stable_id: str) -> int:
        """Remove the User's rows from every personal-data table atomically (Req 14.2, 14.4).

        Opens a single transaction and deletes the User's rows table by table in
        :data:`~edu_it_domain.ports.personal_data.PERSONAL_DATA_TABLES` order
        (children before parents), so any failure rolls the whole transaction
        back and leaves every personal-data table unchanged while the error
        propagates. Returns the total number of rows removed.
        """
        with self._engine.begin() as connection:
            internal_id = _resolve_internal_user_id(connection, user_stable_id)
            if internal_id is None:
                return 0
            removed = 0
            for name in PERSONAL_DATA_TABLES:
                table = data_model_metadata.tables[name]
                predicate = _user_row_predicate(table, internal_id)
                result = connection.execute(delete(table).where(predicate))
                removed += int(result.rowcount)
            return removed


def _resolve_internal_user_id(connection: Connection, user_stable_id: str) -> int | None:
    """Resolve a User's ``stable_id`` to the internal numeric id, or ``None``.

    The internal id is used only to build the foreign-key predicates below; it is
    never surfaced to the domain (D3 Section 1, Req 4.4).
    """
    return connection.execute(
        select(user_table.c.id).where(user_table.c.stable_id == user_stable_id)
    ).scalar_one_or_none()


def _user_row_predicate(table: Table, internal_user_id: int) -> ColumnElement[bool]:
    """Return the predicate selecting ``table``'s rows owned by the User.

    A table that carries ``user_id`` directly is filtered on it; the ``user``
    table is filtered on its own ``id``; every other personal-data table reaches
    the User only through a parent row, so it is filtered by a ``<fk> IN (SELECT
    id FROM parent WHERE <parent owned by user>)`` subquery built from the
    table's single owning foreign key.
    """
    name = table.name
    if name == "user":
        return table.c.id == internal_user_id
    if "user_id" in table.c:
        return table.c.user_id == internal_user_id

    foreign_column, parent_table = _owning_foreign_key(table)
    parent_predicate = _user_row_predicate(parent_table, internal_user_id)
    return foreign_column.in_(select(parent_table.c.id).where(parent_predicate))


#: For each personal-data table that reaches the User only through a parent, the
#: (foreign-key column name, parent table name) that owns it. Derived from the D3
#: schema foreign keys; kept explicit so the ownership chain is auditable rather
#: than inferred at runtime from ambiguous multi-FK tables.
_OWNING_FOREIGN_KEY: dict[str, tuple[str, str]] = {
    "answer": ("quiz_attempt_id", "quiz_attempt"),
    "lesson_completion": ("module_progress_id", "module_progress"),
    "module_progress": ("enrollment_id", "enrollment"),
    "lab_task_completion": ("lab_run_id", "lab_run"),
    "lab_run": ("lab_session_id", "lab_session"),
    "mastery_event": ("mastery_id", "mastery"),
    "review_attempt": ("review_schedule_id", "review_schedule"),
}


def _owning_foreign_key(table: Table) -> tuple[ColumnElement[Any], Table]:
    """Return the owning foreign-key column and its parent table for ``table``."""
    column_name, parent_name = _OWNING_FOREIGN_KEY[table.name]
    return table.c[column_name], data_model_metadata.tables[parent_name]


def _api_safe_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """Return ``row`` without the internal numeric primary key (Req 4.4)."""
    return {column: value for column, value in row.items() if column != _INTERNAL_PK}
