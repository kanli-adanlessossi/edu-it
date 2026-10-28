"""Property-based test for GDPR export coverage (task 19.3).

Feature: foundations, Property 19: GDPR export covers exactly the personal-data tables

Property 19: *For any* User and any arrangement of rows across the personal-data
tables, the produced export contains exactly the personal-data tables listed in
``docs/DATA-MODEL.md`` Section 13 — including tables for which the User has no
rows, represented as empty — and the rows it contains for a table are exactly
that User's rows in that table (Req 14.1).

This drives :class:`edu_it_application.gdpr.ExportUserDataUseCase` against the
infrastructure port fakes (:class:`In_Memory_PersonalDataGateway`,
:class:`In_Memory_ObjectStorage`), exactly as the test/dev composition root wires
them — no database or cloud storage. Each drawn example seeds an arbitrary number
of rows for the exported User across a random subset of personal-data tables
(and some rows for a *second* User, to check the export isolates the subject's
rows), runs the export, reads the written document back from object storage, and
asserts the document's key set is exactly the personal-data tables and each
table's rows are exactly the subject User's rows.

Validates: Requirements 14.1

Uses Hypothesis, the pinned Python property-based testing library
(docs/adr/0001-versions.md).
"""

from __future__ import annotations

import json

from edu_it_application.gdpr import ExportUserDataRequest, ExportUserDataUseCase
from edu_it_domain.ports.personal_data import PERSONAL_DATA_TABLES
from edu_it_infrastructure.fakes import (
    In_Memory_ObjectStorage,
    In_Memory_PersonalDataGateway,
)
from hypothesis import given, settings
from hypothesis import strategies as st

_SUBJECT = "subject"
_OTHER = "other"

# A row is an arbitrary JSON-serialisable column→value mapping; a small shape
# keeps examples cheap while still being distinguishable.
_rows = st.dictionaries(
    keys=st.text(alphabet="abcdefghijklmnopqrstuvwxyz_", min_size=1, max_size=8),
    values=st.one_of(st.integers(), st.text(max_size=12), st.booleans(), st.none()),
    max_size=4,
)

# For each personal-data table, how many rows the subject and the other user hold
# (0 included so the "empty table present" quantifier is exercised).
_table_rows = st.lists(_rows, max_size=3)
_per_table = st.fixed_dictionaries({table: _table_rows for table in PERSONAL_DATA_TABLES})


@settings(max_examples=100)
@given(subject_rows=_per_table, other_rows=_per_table)
def test_export_covers_exactly_the_personal_data_tables(
    subject_rows: dict[str, list[dict[str, object]]],
    other_rows: dict[str, list[dict[str, object]]],
) -> None:
    gateway = In_Memory_PersonalDataGateway()
    for table in PERSONAL_DATA_TABLES:
        for row in subject_rows[table]:
            gateway.add_row(table, _SUBJECT, row)
        for row in other_rows[table]:
            gateway.add_row(table, _OTHER, row)
    storage = In_Memory_ObjectStorage()
    use_case = ExportUserDataUseCase(personal_data=gateway, object_storage=storage)

    result = use_case.execute(ExportUserDataRequest(user_stable_id=_SUBJECT))

    document = json.loads(storage.get(result.storage_key).data)
    # Exactly the personal-data tables — no more, no fewer — are present as keys.
    assert set(document) == set(PERSONAL_DATA_TABLES)
    for table in PERSONAL_DATA_TABLES:
        # The rows for a table are exactly the subject's rows in that table,
        # unaffected by the other user's rows, and an empty table is present.
        assert document[table] == subject_rows[table]
