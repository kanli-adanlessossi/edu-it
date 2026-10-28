"""Persistence layer: the SQLAlchemy schema and the Alembic Migration_Runner.

This subpackage owns the full EDU-IT data-model schema (Req 4) as a single
SQLAlchemy :class:`~sqlalchemy.MetaData`, plus the Alembic environment and the
:class:`~edu_it_infrastructure.persistence.runner.Migration_Runner` that applies,
rolls back, and idempotently re-applies it.

The schema is the authoritative one in ``docs/DATA-MODEL.md`` (D3). Exactly one
table is created per entity in the D3 entity groups and ER diagram, with no
listed entity omitted and no table beyond those entities (Req 4.1). Every row
carries an internal numeric primary key (``id``) that is never exposed in the
API, and every API-addressable resource additionally carries a unique
``stable_id`` string (Req 4.4).

This phase migrates the full model but implements no learning/lab/AI table
behavior (Req 20.1-20.3); only the schema is created.
"""

from __future__ import annotations

from edu_it_infrastructure.persistence.metadata import (
    DATA_MODEL_TABLE_NAMES,
    STABLE_ID_TABLE_NAMES,
    metadata,
)
from edu_it_infrastructure.persistence.personal_data import Postgres_PersonalDataGateway
from edu_it_infrastructure.persistence.repositories import (
    Postgres_RoleAssignmentRepository,
    Postgres_SessionRepository,
    Postgres_UserRepository,
)

__all__ = [
    "DATA_MODEL_TABLE_NAMES",
    "STABLE_ID_TABLE_NAMES",
    "Postgres_PersonalDataGateway",
    "Postgres_RoleAssignmentRepository",
    "Postgres_SessionRepository",
    "Postgres_UserRepository",
    "metadata",
]
