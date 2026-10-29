"""GDPR coverage of the learning personal-data tables (task 19.5, Req 22.1-22.3).

Runs the real GDPR use cases (:mod:`edu_it_application.gdpr`) over the SQL
gateway against in-memory SQLite, seeding every learning-phase personal-data
table: quiz attempts (+answers), mastery (+events), reviews (schedule +
attempts), hint reveals, project submissions, and enrollment / progress /
completions. Export must include them (Req 22.1), deletion must remove them
without touching another learner (Req 22.2), and the personal-data table set
(the export/deletion/retention scope) must include every learning table that
carries a user (Req 22.3).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

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
from edu_it_infrastructure.persistence import Postgres_PersonalDataGateway, metadata
from sqlalchemy import create_engine, insert
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool

T = metadata.tables

#: The learning-phase personal-data tables Req 22 names or implies.
LEARNING_TABLES = (
    "quiz_attempt",
    "answer",
    "mastery",
    "mastery_event",
    "review_schedule",
    "review_attempt",
    "hint_reveal",
    "project_submission",
    "enrollment",
    "module_progress",
    "lesson_completion",
)


def _engine() -> Engine:
    eng = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    metadata.create_all(eng)
    return eng


def _pk(result: object) -> int:
    return int(result.inserted_primary_key[0])  # type: ignore[attr-defined]


def _seed(engine: Engine, name: str) -> None:
    now = datetime(2024, 1, 1, tzinfo=UTC)
    with engine.begin() as c:
        uid = _pk(
            c.execute(
                insert(T["user"]).values(
                    stable_id=name, email=f"{name}@example.test", locale="en", status="verified"
                )
            )
        )
        enr = _pk(
            c.execute(
                insert(T["enrollment"]).values(
                    stable_id=f"en-{name}", user_id=uid, track_id=1, enrolled_at=now
                )
            )
        )
        mp = _pk(
            c.execute(
                insert(T["module_progress"]).values(
                    enrollment_id=enr, module_id=1, tested_out=False, state="active"
                )
            )
        )
        c.execute(insert(T["lesson_completion"]).values(module_progress_id=mp, lesson_id=1))
        qa = _pk(
            c.execute(
                insert(T["quiz_attempt"]).values(
                    user_id=uid,
                    stable_id=f"qa-{name}",
                    quiz_id=1,
                    idempotency_key=f"k-{name}",
                    is_best=True,
                )
            )
        )
        c.execute(
            insert(T["answer"]).values(quiz_attempt_id=qa, question_id=1, counted_for_mastery=True)
        )
        m = _pk(
            c.execute(
                insert(T["mastery"]).values(user_id=uid, concept_id=1, score=10, state="introduced")
            )
        )
        c.execute(
            insert(T["mastery_event"]).values(
                mastery_id=m, delta=10, source="quiz", occurred_at=now
            )
        )
        rs = _pk(
            c.execute(
                insert(T["review_schedule"]).values(
                    user_id=uid, concept_id=1, due_at=now, days_overdue=0
                )
            )
        )
        c.execute(
            insert(T["review_attempt"]).values(review_schedule_id=rs, passed=True, attempted_at=now)
        )
        c.execute(insert(T["hint_reveal"]).values(user_id=uid, question_id=1, revealed_at=now))
        c.execute(
            insert(T["project_submission"]).values(
                user_id=uid, stable_id=f"ps-{name}", project_id=1
            )
        )


def test_every_learning_table_is_in_the_personal_data_scope() -> None:
    assert set(LEARNING_TABLES) <= set(PERSONAL_DATA_TABLES)


def test_export_includes_all_learning_data() -> None:
    engine = _engine()
    storage = In_Memory_ObjectStorage()
    _seed(engine, "u1")
    use_case = ExportUserDataUseCase(
        personal_data=Postgres_PersonalDataGateway(engine), object_storage=storage
    )

    result = use_case.execute(ExportUserDataRequest(user_stable_id="u1"))

    document = json.loads(storage.get(result.storage_key).data)
    for table in LEARNING_TABLES:
        assert len(document[table]) == 1, table
        assert "id" not in document[table][0]


def test_deletion_removes_all_learning_data_and_spares_other_learners() -> None:
    engine = _engine()
    gateway = Postgres_PersonalDataGateway(engine)
    _seed(engine, "gone")
    _seed(engine, "kept")
    clock = Test_Clock(start=datetime(2024, 1, 2, tzinfo=UTC))
    use_case = DeleteAccountUseCase(
        personal_data=gateway,
        sessions=In_Memory_SessionRepository(),
        session_store=In_Memory_SessionStore(clock),
        clock=clock,
    )

    result = use_case.execute(DeleteAccountRequest(user_stable_id="gone", confirmed=True))

    assert result.deleted
    gone = gateway.export_user_data("gone")
    kept = gateway.export_user_data("kept")
    for table in LEARNING_TABLES:
        assert gone[table] == [], table
        assert len(kept[table]) == 1, table
