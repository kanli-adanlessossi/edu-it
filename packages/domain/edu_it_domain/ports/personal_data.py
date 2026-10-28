"""The ``PersonalDataGateway`` port (task 19.1, Req 14.1-14.4).

GDPR data export and account deletion operate over **every personal-data
table** listed in ``docs/DATA-MODEL.md`` (D3) Section 13 — the identity records
and all per-user progress, mastery, review, and assessment rows. The application
layer must orchestrate export and deletion without importing a database or ORM
(the inward ``api -> application -> domain <- infrastructure`` layering,
Req 1.8), so that whole-of-model read and the atomic whole-of-model delete sit
behind this framework-free :class:`typing.Protocol` port. The infrastructure
layer supplies a PostgreSQL (SQLAlchemy Core) implementation whose delete runs in
a single transaction, and an in-memory fake for tests.

The port names exactly the personal-data tables in :data:`PERSONAL_DATA_TABLES`,
derived from D3 Section 13 (catalog, provenance, and AI/review-ops tables that
hold no personal data are deliberately excluded). An export is required to cover
*every* personal-data table — including tables for which the User has no rows,
represented as empty — so the exported shape is independent of the data present
(Req 14.1). A deletion is required to be **atomic across all personal-data
tables**: either every one of the User's personal-data rows is removed, or — if
removal from any table fails — none is, and an error is raised (Req 14.2, 14.4).
Revoking the User's Sessions (Req 14.3) is performed by the application use case
through the session ports, not here.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Final, Protocol, runtime_checkable

__all__ = [
    "PERSONAL_DATA_TABLES",
    "PersonalDataExport",
    "PersonalDataGateway",
]

#: The canonical, ordered tuple of personal-data table names from
#: ``docs/DATA-MODEL.md`` (D3) Section 13 — every table marked "Yes" for personal
#: data. Catalog, provenance, and AI/review-ops tables (marked "not personal
#: data") are excluded. The order is a safe **deletion order**: a child table is
#: listed before the parent its foreign key references, so a gateway deleting in
#: this order never violates a foreign-key constraint; an export may use any
#: order since every table is always present. The property test (task 19.4)
#: checks this set against D3 Section 13.
PERSONAL_DATA_TABLES: Final[tuple[str, ...]] = (
    # Progress leaves first, then their parents, then the identity records last.
    "answer",
    "quiz_attempt",
    "lesson_completion",
    "module_progress",
    "enrollment",
    "lab_task_completion",
    "lab_run",
    "lab_session",
    "project_submission",
    "mastery_event",
    "mastery",
    "review_attempt",
    "review_schedule",
    "hint_reveal",
    "path_recommendation",
    "placement",
    "final_assessment",
    # Identity records (session/oauth_link/role_assignment reference user) then
    # the user row itself, deleted last.
    "session",
    "oauth_link",
    "role_assignment",
    "user",
)

#: A User's exported personal data: a mapping from each personal-data table name
#: to the list of that User's rows in that table (each row a column→value
#: mapping). Every table in :data:`PERSONAL_DATA_TABLES` is a key, with an empty
#: list for a table the User has no rows in, so the export covers exactly the
#: personal-data tables regardless of the data present (Req 14.1).
PersonalDataExport = Mapping[str, Sequence[Mapping[str, Any]]]


@runtime_checkable
class PersonalDataGateway(Protocol):
    """Port for exporting and atomically deleting a User's personal data (Req 14)."""

    def export_user_data(self, user_stable_id: str) -> PersonalDataExport:
        """Return the User's rows across **every** personal-data table.

        The returned mapping has one key per table in
        :data:`PERSONAL_DATA_TABLES`; the value is the list of that User's rows
        in that table (a table with no rows for the User is present with an empty
        list), so the export covers exactly the personal-data tables listed in D3
        Section 13 regardless of the data present (Req 14.1). The internal numeric
        primary key is never surfaced; rows carry the API-safe columns only
        (D3 Section 1).
        """
        ...

    def delete_user_data(self, user_stable_id: str) -> int:
        """Remove the User's personal data across **every** personal-data table.

        Runs as a single atomic operation: every one of the User's personal-data
        rows across all tables in :data:`PERSONAL_DATA_TABLES` is removed, or — if
        removal from any table fails — none is removed and the operation raises,
        leaving every personal-data table unchanged (Req 14.2, 14.4). Returns the
        total number of rows removed. Revoking the User's Sessions (Req 14.3) is
        the caller's responsibility, performed through the session ports.
        """
        ...
