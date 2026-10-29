"""Property test: GDPR export and deletion cover all learning personal data.

Feature: learning-core, Property 29: Export and deletion cover all learning personal data

**Validates: Requirements 22.1, 22.2, 22.3**
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from edu_it_application.gdpr import (
    DeleteAccountRequest,
    DeleteAccountUseCase,
    ExportUserDataRequest,
    ExportUserDataUseCase,
)
from edu_it_domain.ports.personal_data import PERSONAL_DATA_TABLES
from edu_it_infrastructure.fakes import (
    In_Memory_ObjectStorage,
    In_Memory_SessionRepository,
    In_Memory_SessionStore,
    Test_Clock,
)
from edu_it_infrastructure.persistence import Postgres_PersonalDataGateway
from hypothesis import given, settings
from hypothesis import strategies as st

sys.path.insert(0, str(Path(__file__).parent))
from test_gdpr_learning_data_coverage import (  # type: ignore[import-not-found]
    LEARNING_TABLES,
    _engine,
    _seed,
)

_names = st.lists(st.from_regex(r"[a-z]{3,8}", fullmatch=True), min_size=2, max_size=3, unique=True)


@settings(max_examples=100, deadline=None)
@given(names=_names, data=st.data())
def test_export_and_deletion_cover_all_learning_data(names: list[str], data: st.DataObject) -> None:
    """Feature: learning-core, Property 29: Export and deletion cover all learning personal data."""
    target = data.draw(st.sampled_from(names))
    others = [n for n in names if n != target]

    # Req 22.3: the personal-data scope includes every learning table.
    assert set(LEARNING_TABLES) <= set(PERSONAL_DATA_TABLES)

    engine = _engine()
    gateway = Postgres_PersonalDataGateway(engine)
    for name in names:
        _seed(engine, name)

    # Req 22.1: export includes the learner's rows in every learning table.
    storage = In_Memory_ObjectStorage()
    export = ExportUserDataUseCase(personal_data=gateway, object_storage=storage)
    result = export.execute(ExportUserDataRequest(user_stable_id=target))
    document = json.loads(storage.get(result.storage_key).data)
    for table in LEARNING_TABLES:
        assert len(document[table]) == 1, table

    # Req 22.2: deletion removes all of the learner's rows, and only theirs.
    clock = Test_Clock(start=datetime(2024, 1, 2, tzinfo=UTC))
    delete = DeleteAccountUseCase(
        personal_data=gateway,
        sessions=In_Memory_SessionRepository(),
        session_store=In_Memory_SessionStore(clock),
        clock=clock,
    )
    assert delete.execute(DeleteAccountRequest(user_stable_id=target, confirmed=True)).deleted
    gone = gateway.export_user_data(target)
    for table in LEARNING_TABLES:
        assert gone[table] == [], table
    for other in others:
        kept = gateway.export_user_data(other)
        for table in LEARNING_TABLES:
            assert len(kept[table]) == 1, table
