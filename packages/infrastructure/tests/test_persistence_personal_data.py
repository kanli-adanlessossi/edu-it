"""Unit tests for the PostgreSQL personal-data gateway (task 19.1, Req 14.1-14.4).

The gateway is database-agnostic SQLAlchemy Core over the shared data-model
``Table`` objects, so these tests run it against an in-memory SQLite database
(schema created from the same ``metadata``, foreign keys enforced) with no live
PostgreSQL server. They pin the behaviors the GDPR use cases rely on: an export
covers every personal-data table and resolves rows reached only through a parent
foreign key (``module_progress`` through ``enrollment``, ``answer`` through
``quiz_attempt``), the internal numeric primary key is never surfaced, deletion
removes one User's rows without touching another User's, and a deletion that
fails partway rolls the whole transaction back leaving every table unchanged
(the atomicity requirement, Req 14.4).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from edu_it_domain.ports.personal_data import PERSONAL_DATA_TABLES, PersonalDataGateway
from edu_it_infrastructure.persistence import Postgres_PersonalDataGateway, metadata
from edu_it_infrastructure.persistence.metadata import (
    answer as answer_table,
)
from edu_it_infrastructure.persistence.metadata import (
    content_provenance as content_provenance_table,
)
from edu_it_infrastructure.persistence.metadata import (
    enrollment as enrollment_table,
)
from edu_it_infrastructure.persistence.metadata import (
    lesson_completion as lesson_completion_table,
)
from edu_it_infrastructure.persistence.metadata import (
    mastery as mastery_table,
)
from edu_it_infrastructure.persistence.metadata import (
    mastery_event as mastery_event_table,
)
from edu_it_infrastructure.persistence.metadata import (
    module_progress as module_progress_table,
)
from edu_it_infrastructure.persistence.metadata import (
    oauth_link as oauth_link_table,
)
from edu_it_infrastructure.persistence.metadata import (
    quiz_attempt as quiz_attempt_table,
)
from edu_it_infrastructure.persistence.metadata import (
    role_assignment as role_assignment_table,
)
from edu_it_infrastructure.persistence.metadata import (
    session as session_table,
)
from edu_it_infrastructure.persistence.metadata import (
    user as user_table,
)
from sqlalchemy import create_engine, event, insert
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool


def _new_engine(*, enforce_fk: bool) -> Engine:
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    if enforce_fk:

        @event.listens_for(eng, "connect")
        def _enable_fk(dbapi_connection: object, _record: object) -> None:
            cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    metadata.create_all(eng)
    return eng


@pytest.fixture
def engine() -> Iterator[Engine]:
    # Foreign-key enforcement is left OFF here so the full owned chain can be
    # seeded with placeholder catalog ids (track/module/quiz/concept) without
    # materialising the whole catalog — the gateway's user-row resolution is pure
    # SQL and does not depend on enforcement. The atomicity test builds its own
    # FK-enforcing engine where a rolled-back transaction is observable.
    eng = _new_engine(enforce_fk=False)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def gateway(engine: Engine) -> Postgres_PersonalDataGateway:
    return Postgres_PersonalDataGateway(engine)


def _pk(result: object) -> int:
    """Return the single inserted primary key as an int (typed helper for mypy)."""
    key = result.inserted_primary_key  # type: ignore[attr-defined]
    assert key is not None
    return int(key[0])


def _seed_user(engine: Engine, stable_id: str, email: str) -> int:
    """Insert a user and return its internal id (for building the FK chains)."""
    with engine.begin() as connection:
        result = connection.execute(
            insert(user_table).values(
                stable_id=stable_id, email=email, locale="en", status="verified"
            )
        )
        return _pk(result)


def _seed_full_chain(engine: Engine, user_id: int) -> None:
    """Seed one row per directly- and indirectly-owned personal-data table."""
    with engine.begin() as connection:
        # Direct user_id tables.
        instant = datetime(2024, 1, 1, tzinfo=UTC)
        connection.execute(
            insert(session_table).values(
                user_id=user_id, server_side_ref=f"ref-{user_id}", expires_at=instant
            )
        )
        connection.execute(
            insert(oauth_link_table).values(
                user_id=user_id, provider="github", subject=f"s-{user_id}"
            )
        )
        connection.execute(insert(role_assignment_table).values(user_id=user_id, role="learner"))
        # Catalog foreign keys are NOT NULL; with FK enforcement off we seed
        # placeholder catalog ids (the gateway resolves ownership by the chain to
        # the user, not by catalog validity).
        enrollment_id = _pk(
            connection.execute(
                insert(enrollment_table).values(
                    stable_id=f"en-{user_id}", user_id=user_id, track_id=1, enrolled_at=instant
                )
            )
        )
        # Indirect: module_progress -> enrollment, lesson_completion -> module_progress.
        mp_id = _pk(
            connection.execute(
                insert(module_progress_table).values(
                    enrollment_id=enrollment_id, module_id=1, tested_out=False, state="active"
                )
            )
        )
        connection.execute(
            insert(lesson_completion_table).values(module_progress_id=mp_id, lesson_id=1)
        )
        # Indirect: quiz_attempt -> user (direct), answer -> quiz_attempt.
        qa_id = _pk(
            connection.execute(
                insert(quiz_attempt_table).values(
                    user_id=user_id,
                    stable_id=f"qa-{user_id}",
                    quiz_id=1,
                    idempotency_key=f"k-{user_id}",
                    is_best=True,
                )
            )
        )
        connection.execute(
            insert(answer_table).values(
                quiz_attempt_id=qa_id, question_id=1, counted_for_mastery=False
            )
        )
        # mastery -> user (direct), mastery_event -> mastery.
        mastery_id = _pk(
            connection.execute(
                insert(mastery_table).values(
                    user_id=user_id, concept_id=1, score=10, state="introduced"
                )
            )
        )
        connection.execute(
            insert(mastery_event_table).values(
                mastery_id=mastery_id,
                delta=10,
                source="quiz",
                occurred_at=instant,
            )
        )


def test_gateway_satisfies_the_port(gateway: Postgres_PersonalDataGateway) -> None:
    assert isinstance(gateway, PersonalDataGateway)


def test_export_covers_every_personal_data_table(
    engine: Engine, gateway: Postgres_PersonalDataGateway
) -> None:
    _seed_user(engine, "user-1", "a@example.test")

    export = gateway.export_user_data("user-1")

    assert set(export) == set(PERSONAL_DATA_TABLES)
    # The user row is present; a table the user has no rows in is empty.
    assert len(export["user"]) == 1
    assert export["enrollment"] == []


def test_export_never_surfaces_the_internal_primary_key(
    engine: Engine, gateway: Postgres_PersonalDataGateway
) -> None:
    _seed_user(engine, "user-1", "a@example.test")

    export = gateway.export_user_data("user-1")

    assert "id" not in export["user"][0]
    assert export["user"][0]["stable_id"] == "user-1"


def test_export_resolves_rows_reached_through_a_parent_foreign_key(
    engine: Engine, gateway: Postgres_PersonalDataGateway
) -> None:
    user_id = _seed_user(engine, "user-1", "a@example.test")
    _seed_full_chain(engine, user_id)

    export = gateway.export_user_data("user-1")

    # Directly- and indirectly-owned tables both resolve to the user's rows.
    assert len(export["enrollment"]) == 1
    assert len(export["module_progress"]) == 1  # via enrollment
    assert len(export["lesson_completion"]) == 1  # via module_progress
    assert len(export["answer"]) == 1  # via quiz_attempt
    assert len(export["mastery_event"]) == 1  # via mastery


def test_export_for_unknown_user_is_all_empty(gateway: Postgres_PersonalDataGateway) -> None:
    export = gateway.export_user_data("ghost")
    assert set(export) == set(PERSONAL_DATA_TABLES)
    assert all(rows == [] for rows in export.values())


def test_delete_removes_all_of_one_users_rows_across_the_chain(
    engine: Engine, gateway: Postgres_PersonalDataGateway
) -> None:
    user_id = _seed_user(engine, "user-1", "a@example.test")
    _seed_full_chain(engine, user_id)

    removed = gateway.delete_user_data("user-1")

    assert removed > 0
    after = gateway.export_user_data("user-1")
    assert all(rows == [] for rows in after.values())


def test_delete_leaves_another_users_rows_untouched(
    engine: Engine, gateway: Postgres_PersonalDataGateway
) -> None:
    keep_id = _seed_user(engine, "keep", "keep@example.test")
    drop_id = _seed_user(engine, "drop", "drop@example.test")
    _seed_full_chain(engine, keep_id)
    _seed_full_chain(engine, drop_id)

    gateway.delete_user_data("drop")

    assert gateway.export_user_data("drop")["mastery_event"] == []
    # The other user's full chain survives intact.
    kept = gateway.export_user_data("keep")
    assert len(kept["mastery_event"]) == 1
    assert len(kept["answer"]) == 1


def test_delete_for_unknown_user_removes_nothing(gateway: Postgres_PersonalDataGateway) -> None:
    assert gateway.delete_user_data("ghost") == 0


def test_delete_is_atomic_when_a_table_delete_fails() -> None:
    # Build a foreign-key-ENFORCING engine so a rolled-back transaction is
    # observable. A `content_provenance.reviewed_by` row references the user; that
    # row is NOT a personal-data table, so the gateway never deletes it, and the
    # final `DELETE FROM user` therefore violates the foreign key and raises. The
    # single transaction must roll back, leaving every personal-data table
    # unchanged (Req 14.4).
    eng = _new_engine(enforce_fk=True)
    try:
        gateway = Postgres_PersonalDataGateway(eng)
        user_id = _seed_user(eng, "user-1", "a@example.test")
        # A couple of directly-owned rows with no catalog FK dependency so the
        # seed is valid under enforcement: an oauth_link and a role_assignment.
        with eng.begin() as connection:
            connection.execute(
                insert(oauth_link_table).values(user_id=user_id, provider="github", subject="s")
            )
            connection.execute(
                insert(role_assignment_table).values(user_id=user_id, role="learner")
            )
            connection.execute(
                insert(content_provenance_table).values(
                    stable_id="prov-1", content_version="v1", origin="human", reviewed_by=user_id
                )
            )

        with pytest.raises(Exception):  # noqa: B017 - any DB integrity error satisfies the contract
            gateway.delete_user_data("user-1")

        # Nothing was removed: the user and their owned rows all still exist.
        after = gateway.export_user_data("user-1")
        assert len(after["user"]) == 1
        assert len(after["oauth_link"]) == 1
        assert len(after["role_assignment"]) == 1
    finally:
        eng.dispose()
