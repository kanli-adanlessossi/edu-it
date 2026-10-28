"""The full EDU-IT data-model schema as a single SQLAlchemy ``MetaData`` (Req 4).

This module defines, with SQLAlchemy Core :class:`~sqlalchemy.Table` objects,
**exactly one table per entity** in the ``docs/DATA-MODEL.md`` (D3) entity
groups and ER diagram — identity, catalog, provenance, progress, mastery &
review, AI/review ops, and assessments — with no listed entity omitted and no
table beyond those entities (Req 4.1). The authoritative schema lives in D3;
this module encodes it.

Conventions (D3 Section 1, Req 4.4):

- Every table has an internal numeric primary key named ``id``. It is a
  surrogate key used only for relational joins and is **never exposed in the
  API**; the :data:`metadata` objects therefore carry no behavior that would
  surface it.
- Every API-addressable resource additionally carries an immutable, opaque
  ``stable_id`` string with a **unique constraint**. Tables that are not
  API-addressable (join/child rows, the session backing, ephemeral ops rows)
  carry no ``stable_id``, matching D3's attribute lists.

The tables are schema only. This phase migrates the full model but implements no
learning/lab/AI table behavior (Req 20.1-20.3); there are no ORM mappings,
defaults, or triggers encoding business rules here — only columns, keys,
foreign keys, and the ``stable_id`` uniqueness constraints.

The declaration order below is a safe **creation order**: a table is declared
only after the tables its foreign keys reference. Alembic creates tables in this
order on upgrade and drops them in the exact reverse order on downgrade
(Req 4.3).
"""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
)

__all__ = [
    "DATA_MODEL_TABLE_NAMES",
    "STABLE_ID_TABLE_NAMES",
    "metadata",
]

# A single MetaData collecting every Data_Model table. A stable naming
# convention keeps constraint/index names deterministic across apply and
# rollback so downgrades drop exactly what upgrades created.
metadata = MetaData(
    naming_convention={
        "ix": "ix_%(table_name)s_%(column_0_name)s",
        "uq": "uq_%(table_name)s_%(column_0_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    }
)


def _internal_pk() -> Column[int]:
    """The internal numeric primary key (``id``) carried by every row.

    Never exposed in the API; used only for relational joins (D3 Section 1.1).
    """
    return Column("id", Integer, primary_key=True, autoincrement=True)


def _stable_id() -> Column[str]:
    """The immutable, unique ``stable_id`` on every API-addressable resource.

    Carries a unique constraint and is not nullable (D3 Section 1.1, Req 4.4).
    """
    return Column("stable_id", String(64), nullable=False, unique=True)


# ---------------------------------------------------------------------------
# Identity group (D3 Section 4): user, session, oauth_link, role_assignment
# ---------------------------------------------------------------------------

user = Table(
    "user",
    metadata,
    _internal_pk(),
    _stable_id(),
    Column("email", String(254), nullable=False),
    Column("display_name", String(255)),
    Column("locale", String(2), nullable=False),
    Column("status", String(32), nullable=False),
    Column("deleted_at", DateTime(timezone=True)),
)

session = Table(
    # The session is NOT API-addressable this phase: the client holds only an
    # opaque server_side_ref, so this table carries no stable_id (D3 Section 4,
    # design "Domain entities active this phase").
    "session",
    metadata,
    _internal_pk(),
    Column("user_id", Integer, ForeignKey("user.id"), nullable=False),
    Column("server_side_ref", String(255), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("revoked_at", DateTime(timezone=True)),
)

oauth_link = Table(
    "oauth_link",
    metadata,
    _internal_pk(),
    Column("user_id", Integer, ForeignKey("user.id"), nullable=False),
    Column("provider", String(32), nullable=False),
    Column("subject", String(255), nullable=False),
)

role_assignment = Table(
    "role_assignment",
    metadata,
    _internal_pk(),
    Column("user_id", Integer, ForeignKey("user.id"), nullable=False),
    Column("role", String(32), nullable=False),
    Column("track_scope", String(64)),
)

# ---------------------------------------------------------------------------
# Provenance group (D3 Section 5): content_provenance
# Declared before the catalog tables that reference it one-to-one.
# ---------------------------------------------------------------------------

content_provenance = Table(
    "content_provenance",
    metadata,
    _internal_pk(),
    _stable_id(),
    Column("content_version", String(128), nullable=False),
    Column("origin", String(32), nullable=False),
    Column("model", String(128)),
    Column("prompt_id", String(128)),
    Column("prompt_version", String(128)),
    Column("generation_date", DateTime(timezone=True)),
    # reviewed_by references a reviewer account (D3 Section 13 note).
    Column("reviewed_by", Integer, ForeignKey("user.id")),
    Column("reviewed_at", DateTime(timezone=True)),
)

# ---------------------------------------------------------------------------
# Catalog group (D3 Section 6): track, module, lesson, lab, lab_task,
# lab_check, project, rubric, rubric_criterion, quiz, question,
# question_option, concept, prerequisite
# ---------------------------------------------------------------------------

track = Table(
    "track",
    metadata,
    _internal_pk(),
    _stable_id(),
    Column("provenance_id", Integer, ForeignKey("content_provenance.id"), nullable=False),
    Column("title_fr", String(255), nullable=False),
    Column("title_en", String(255), nullable=False),
    Column("status", String(32), nullable=False),
)

module = Table(
    "module",
    metadata,
    _internal_pk(),
    _stable_id(),
    Column("track_id", Integer, ForeignKey("track.id"), nullable=False),
    Column("provenance_id", Integer, ForeignKey("content_provenance.id"), nullable=False),
    Column("ordinal", Integer, nullable=False),
)

lesson = Table(
    "lesson",
    metadata,
    _internal_pk(),
    _stable_id(),
    Column("module_id", Integer, ForeignKey("module.id"), nullable=False),
    Column("provenance_id", Integer, ForeignKey("content_provenance.id"), nullable=False),
    Column("ordinal", Integer, nullable=False),
)

lab = Table(
    "lab",
    metadata,
    _internal_pk(),
    _stable_id(),
    Column("lesson_id", Integer, ForeignKey("lesson.id"), nullable=False),
    Column("provenance_id", Integer, ForeignKey("content_provenance.id"), nullable=False),
)

lab_task = Table(
    "lab_task",
    metadata,
    _internal_pk(),
    _stable_id(),
    Column("lab_id", Integer, ForeignKey("lab.id"), nullable=False),
    Column("ordinal", Integer, nullable=False),
)

lab_check = Table(
    "lab_check",
    metadata,
    _internal_pk(),
    _stable_id(),
    Column("lab_task_id", Integer, ForeignKey("lab_task.id"), nullable=False),
    Column("check_kind", String(32), nullable=False),
)

project = Table(
    "project",
    metadata,
    _internal_pk(),
    _stable_id(),
    Column("provenance_id", Integer, ForeignKey("content_provenance.id"), nullable=False),
)

rubric = Table(
    "rubric",
    metadata,
    _internal_pk(),
    _stable_id(),
    Column("project_id", Integer, ForeignKey("project.id"), nullable=False),
)

rubric_criterion = Table(
    # Child of rubric; not API-addressable, so no stable_id (D3 Section 3).
    "rubric_criterion",
    metadata,
    _internal_pk(),
    Column("rubric_id", Integer, ForeignKey("rubric.id"), nullable=False),
    Column("max_points", Integer, nullable=False),
)

quiz = Table(
    "quiz",
    metadata,
    _internal_pk(),
    _stable_id(),
    Column("lesson_id", Integer, ForeignKey("lesson.id"), nullable=False),
    Column("provenance_id", Integer, ForeignKey("content_provenance.id"), nullable=False),
)

question = Table(
    "question",
    metadata,
    _internal_pk(),
    _stable_id(),
    Column("quiz_id", Integer, ForeignKey("quiz.id"), nullable=False),
    Column("provenance_id", Integer, ForeignKey("content_provenance.id"), nullable=False),
    Column("prompt_text", String, nullable=False),
)

question_option = Table(
    # Child of question; not API-addressable, so no stable_id (D3 Section 3).
    "question_option",
    metadata,
    _internal_pk(),
    Column("question_id", Integer, ForeignKey("question.id"), nullable=False),
    Column("is_correct", Boolean, nullable=False),
)

concept = Table(
    "concept",
    metadata,
    _internal_pk(),
    _stable_id(),
    Column("provenance_id", Integer, ForeignKey("content_provenance.id"), nullable=False),
    Column("title_fr", String(255), nullable=False),
    Column("title_en", String(255), nullable=False),
)

prerequisite = Table(
    # Directed concept -> requires_concept edge; not API-addressable.
    "prerequisite",
    metadata,
    _internal_pk(),
    Column("concept_id", Integer, ForeignKey("concept.id"), nullable=False),
    Column("requires_concept_id", Integer, ForeignKey("concept.id"), nullable=False),
)

# ---------------------------------------------------------------------------
# Progress group (D3 Section 8): enrollment, module_progress,
# lesson_completion, lab_session, lab_run, lab_task_completion, quiz_attempt,
# answer, project_submission
# ---------------------------------------------------------------------------

enrollment = Table(
    "enrollment",
    metadata,
    _internal_pk(),
    Column("user_id", Integer, ForeignKey("user.id"), nullable=False),
    Column("track_id", Integer, ForeignKey("track.id"), nullable=False),
    Column("enrolled_at", DateTime(timezone=True), nullable=False),
)

module_progress = Table(
    "module_progress",
    metadata,
    _internal_pk(),
    Column("enrollment_id", Integer, ForeignKey("enrollment.id"), nullable=False),
    Column("module_id", Integer, ForeignKey("module.id"), nullable=False),
    Column("tested_out", Boolean, nullable=False),
    Column("state", String(32), nullable=False),
)

lesson_completion = Table(
    "lesson_completion",
    metadata,
    _internal_pk(),
    Column("module_progress_id", Integer, ForeignKey("module_progress.id"), nullable=False),
    Column("lesson_id", Integer, ForeignKey("lesson.id"), nullable=False),
    Column("completed_at", DateTime(timezone=True)),
)

lab_session = Table(
    "lab_session",
    metadata,
    _internal_pk(),
    _stable_id(),
    Column("user_id", Integer, ForeignKey("user.id"), nullable=False),
    Column("lab_id", Integer, ForeignKey("lab.id"), nullable=False),
    Column("started_at", DateTime(timezone=True)),
    Column("ttl_at", DateTime(timezone=True)),
    Column("status", String(32), nullable=False),
)

lab_run = Table(
    "lab_run",
    metadata,
    _internal_pk(),
    Column("lab_session_id", Integer, ForeignKey("lab_session.id"), nullable=False),
    Column("status", String(32), nullable=False),
    Column("graded_at", DateTime(timezone=True)),
)

lab_task_completion = Table(
    "lab_task_completion",
    metadata,
    _internal_pk(),
    Column("lab_run_id", Integer, ForeignKey("lab_run.id"), nullable=False),
    Column("lab_task_id", Integer, ForeignKey("lab_task.id"), nullable=False),
    Column("passed", Boolean, nullable=False),
)

quiz_attempt = Table(
    "quiz_attempt",
    metadata,
    _internal_pk(),
    _stable_id(),
    Column("user_id", Integer, ForeignKey("user.id"), nullable=False),
    Column("quiz_id", Integer, ForeignKey("quiz.id"), nullable=False),
    # Idempotency key dedupes duplicate submissions (D3 Section 9, Req 4.6 note).
    Column("idempotency_key", String(128), nullable=False, unique=True),
    Column("score", Integer),
    Column("is_best", Boolean, nullable=False),
)

answer = Table(
    "answer",
    metadata,
    _internal_pk(),
    Column("quiz_attempt_id", Integer, ForeignKey("quiz_attempt.id"), nullable=False),
    Column("question_id", Integer, ForeignKey("question.id"), nullable=False),
    Column("first_answered_at", DateTime(timezone=True)),
    Column("counted_for_mastery", Boolean, nullable=False),
)

project_submission = Table(
    "project_submission",
    metadata,
    _internal_pk(),
    _stable_id(),
    Column("user_id", Integer, ForeignKey("user.id"), nullable=False),
    Column("project_id", Integer, ForeignKey("project.id"), nullable=False),
    Column("review_outcome", String(32)),
)

# ---------------------------------------------------------------------------
# Mastery & review group (D3 Section 7): mastery, mastery_event,
# review_schedule, review_attempt, hint_reveal
# ---------------------------------------------------------------------------

mastery = Table(
    "mastery",
    metadata,
    _internal_pk(),
    Column("user_id", Integer, ForeignKey("user.id"), nullable=False),
    Column("concept_id", Integer, ForeignKey("concept.id"), nullable=False),
    Column("score", Integer, nullable=False),
    Column("state", String(32), nullable=False),
)

mastery_event = Table(
    "mastery_event",
    metadata,
    _internal_pk(),
    Column("mastery_id", Integer, ForeignKey("mastery.id"), nullable=False),
    Column("delta", Integer, nullable=False),
    Column("source", String(32), nullable=False),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
)

review_schedule = Table(
    "review_schedule",
    metadata,
    _internal_pk(),
    Column("user_id", Integer, ForeignKey("user.id"), nullable=False),
    Column("concept_id", Integer, ForeignKey("concept.id"), nullable=False),
    Column("due_at", DateTime(timezone=True)),
    Column("days_overdue", Integer, nullable=False),
)

review_attempt = Table(
    "review_attempt",
    metadata,
    _internal_pk(),
    Column("review_schedule_id", Integer, ForeignKey("review_schedule.id"), nullable=False),
    Column("passed", Boolean, nullable=False),
    Column("attempted_at", DateTime(timezone=True)),
)

hint_reveal = Table(
    "hint_reveal",
    metadata,
    _internal_pk(),
    Column("user_id", Integer, ForeignKey("user.id"), nullable=False),
    Column("question_id", Integer, ForeignKey("question.id"), nullable=False),
    Column("revealed_at", DateTime(timezone=True)),
)

# ---------------------------------------------------------------------------
# AI / review ops group (D3 Section 11): generation_job, variant,
# review_queue_item, item_statistic, path_recommendation, budget_ledger
# generation_job is declared before variant/budget_ledger which reference it.
# ---------------------------------------------------------------------------

generation_job = Table(
    "generation_job",
    metadata,
    _internal_pk(),
    Column("status", String(32), nullable=False),
    Column("model", String(128)),
    Column("tokens", Integer),
    Column("cost_cents", Integer),
)

variant = Table(
    "variant",
    metadata,
    _internal_pk(),
    _stable_id(),
    Column("question_id", Integer, ForeignKey("question.id"), nullable=False),
    Column("generation_job_id", Integer, ForeignKey("generation_job.id")),
    Column("lifecycle_state", String(32), nullable=False),
)

review_queue_item = Table(
    "review_queue_item",
    metadata,
    _internal_pk(),
    Column("variant_id", Integer, ForeignKey("variant.id"), nullable=False),
    Column("state", String(32), nullable=False),
    Column("reviewer_id", Integer, ForeignKey("user.id")),
)

item_statistic = Table(
    "item_statistic",
    metadata,
    _internal_pk(),
    Column("concept_id", Integer, ForeignKey("concept.id")),
    Column("variant_id", Integer, ForeignKey("variant.id")),
    Column("correct_rate", Float),
    Column("avg_time_ms", Float),
)

path_recommendation = Table(
    "path_recommendation",
    metadata,
    _internal_pk(),
    Column("user_id", Integer, ForeignKey("user.id"), nullable=False),
    Column("source", String(32), nullable=False),
    Column("ordering", String, nullable=False),
)

budget_ledger = Table(
    "budget_ledger",
    metadata,
    _internal_pk(),
    Column("generation_job_id", Integer, ForeignKey("generation_job.id"), nullable=False),
    Column("tokens", Integer),
    Column("cost_cents", Integer),
)

# ---------------------------------------------------------------------------
# Assessments group (D3 Section 12): placement, final_assessment
# ---------------------------------------------------------------------------

placement = Table(
    "placement",
    metadata,
    _internal_pk(),
    Column("user_id", Integer, ForeignKey("user.id"), nullable=False),
    Column("track_id", Integer, ForeignKey("track.id"), nullable=False),
    Column("status", String(32), nullable=False),
)

final_assessment = Table(
    "final_assessment",
    metadata,
    _internal_pk(),
    Column("user_id", Integer, ForeignKey("user.id"), nullable=False),
    Column("track_id", Integer, ForeignKey("track.id"), nullable=False),
    Column("status", String(32), nullable=False),
)


# The authoritative set of Data_Model table names, derived directly from the
# MetaData so it can never drift from the declared tables above. Used by the
# Migration_Runner and the set-equality tests (Req 4.1).
DATA_MODEL_TABLE_NAMES: frozenset[str] = frozenset(metadata.tables.keys())

# The subset that is API-addressable and therefore carries a unique stable_id
# (Req 4.4). Derived from the schema so it stays in sync with the columns above.
STABLE_ID_TABLE_NAMES: frozenset[str] = frozenset(
    name for name, table in metadata.tables.items() if "stable_id" in table.c
)
