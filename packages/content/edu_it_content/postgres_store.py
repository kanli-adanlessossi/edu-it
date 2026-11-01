"""The PostgreSQL implementation of the :class:`ContentStore` port (api-route-wiring Req 12).

``edu-it-content sync --database-url <url>`` writes the authored content into the
relational catalog through this adapter, so ``sync_content`` runs unchanged against a
real database. It lives in ``packages/content`` and imports no other EDU-IT package
(design "Package dependency direction"): the table definitions are obtained by
**reflection** from the migrated database (``MetaData.reflect(only=...)``), the adapter
declares no table of its own, and Alembic in ``packages/infrastructure`` stays the only
owner of the schema. When the migrations have not been applied, construction raises
:class:`CatalogSchemaMissingError` naming every missing table or column (Req 12.8).

Coverage (Req 12.2). Every Relational_Catalog table is written: ``track``, ``module``,
``lesson``, ``concept``, ``prerequisite``, ``quiz``, ``question``, ``question_option``,
``project``, ``rubric``, ``rubric_criterion``, ``lab``, ``lab_task`` and ``lab_check``,
plus the ``content_provenance`` row each provenanced item points at.

What the relational schema cannot hold (design decision 10, no new table)
------------------------------------------------------------------------
The catalog tables store the structure and the identifying columns, not the whole
authored payload, so:

- *Change detection.* :meth:`get` returns a payload that compares equal to any incoming
  payload whose relational projection matches the stored rows. A second sync over
  unchanged content therefore reports no change, and an edit to a persisted column
  updates the row in place. Text that has no column (lesson bodies, option text, hints)
  is not persisted and is not part of the comparison.
- *Children without a ``stable_id`` column* (``question_option``, ``rubric_criterion``,
  ``prerequisite``) are matched to rows by their position among the children of the same
  parent, in authored order; rows are never deleted. :meth:`get` only knows such an item
  within the process that wrote it.
- *Archive state* is persisted for ``track`` (``status = 'archived'``) and ``lab``
  (``archived_at``). For the other types it is kept in process for the run.
- *Review* is persisted in ``content_provenance.reviewed_at`` and, when the reviewer
  resolves to a ``user.stable_id``, ``reviewed_by``. The Golden_Set and the orphaned-progress
  flag are kept in process for the sync run and logged.

Items whose parent row has not been written yet (the loader reads files alphabetically, so a
lab precedes its lesson) are held back and written once the parent exists; the sync's
:meth:`list_active_ids` call flushes them, and :meth:`flush` does so on demand.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Engine, MetaData, Table, insert, inspect, select, update
from sqlalchemy.engine import Connection

from edu_it_content.content_store import ContentStatus, StoredItem

__all__ = [
    "CatalogSchemaMissingError",
    "CatalogWriteError",
    "Postgres_ContentStore",
]

_LOG = logging.getLogger(__name__)

#: ``track.status`` value that marks an archived track (the only status the store writes itself).
_ARCHIVED_TRACK_STATUS = "archived"

#: ``lab_session.status`` values for a session that is still in flight.
_ACTIVE_LAB_SESSION_STATUSES = ("provisioning", "ready", "grading")


class CatalogSchemaMissingError(RuntimeError):
    """The database lacks catalog tables or columns: the migrations were not applied (Req 12.8)."""


class CatalogWriteError(ValueError):
    """A catalog item could not be written, for example because its parent row is absent."""


# -- relational mapping ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Parent:
    """A foreign key to the owning row, resolved from a ``stable_id`` held in the payload."""

    column: str
    table: str
    key: str


@dataclass(frozen=True, slots=True)
class _Spec:
    """How one content type maps onto its catalog table."""

    table: str
    scalars: Callable[[dict[str, Any]], dict[str, Any]]
    columns: tuple[str, ...] = ()
    parents: tuple[_Parent, ...] = ()
    provenance: bool = False
    has_stable_id: bool = True
    insert_defaults: dict[str, Any] = field(default_factory=dict)


def _titles(payload: dict[str, Any]) -> dict[str, Any]:
    title = payload["title"]
    return {"title_fr": title["fr"], "title_en": title["en"]}


_SPECS: dict[str, _Spec] = {
    "track": _Spec(
        "track",
        lambda p: {**_titles(p), "status": str(p["status"])},
        columns=("title_fr", "title_en", "status"),
        provenance=True,
    ),
    "module": _Spec(
        "module",
        lambda p: {"ordinal": int(p["ordinal"])},
        columns=("ordinal",),
        parents=(_Parent("track_id", "track", "track_stable_id"),),
        provenance=True,
    ),
    "lesson": _Spec(
        "lesson",
        lambda p: {"ordinal": int(p["ordinal"])},
        columns=("ordinal",),
        parents=(_Parent("module_id", "module", "module_stable_id"),),
        provenance=True,
    ),
    "concept": _Spec(
        "concept",
        _titles,
        columns=("title_fr", "title_en"),
        provenance=True,
    ),
    "prerequisite": _Spec(
        "prerequisite",
        lambda p: {},
        parents=(
            _Parent("concept_id", "concept", "concept_stable_id"),
            _Parent("requires_concept_id", "concept", "requires_concept_stable_id"),
        ),
        has_stable_id=False,
    ),
    "quiz": _Spec(
        "quiz",
        lambda p: {},
        parents=(_Parent("lesson_id", "lesson", "lesson_stable_id"),),
        provenance=True,
    ),
    "question": _Spec(
        "question",
        # The column holds one string; the French prompt is stored, as locales list fr first.
        lambda p: {"prompt_text": p["prompt_text"]["fr"]},
        columns=("prompt_text",),
        parents=(_Parent("quiz_id", "quiz", "quiz_stable_id"),),
        provenance=True,
    ),
    "question_option": _Spec(
        "question_option",
        lambda p: {"is_correct": bool(p["is_correct"])},
        columns=("is_correct",),
        parents=(_Parent("question_id", "question", "question_stable_id"),),
        has_stable_id=False,
    ),
    "project": _Spec("project", lambda p: {}, provenance=True),
    "rubric": _Spec(
        "rubric",
        lambda p: {},
        parents=(_Parent("project_id", "project", "project_stable_id"),),
    ),
    "rubric_criterion": _Spec(
        "rubric_criterion",
        lambda p: {"max_points": int(p["max_points"])},
        columns=("max_points",),
        parents=(_Parent("rubric_id", "rubric", "rubric_stable_id"),),
        has_stable_id=False,
    ),
    "lab": _Spec(
        "lab",
        lambda p: {
            "kind": str(p["kind"]),
            "image": p.get("image"),
            "concept_stable_ids": json.dumps(list(p.get("concept_stable_ids") or [])),
            "archived_at": None,
        },
        columns=("kind", "image", "concept_stable_ids", "archived_at"),
        parents=(_Parent("lesson_id", "lesson", "lesson_stable_id"),),
        provenance=True,
    ),
    "lab_task": _Spec(
        "lab_task",
        lambda p: {"ordinal": int(p["ordinal"]), "required": bool(p.get("required", True))},
        columns=("ordinal", "required"),
        parents=(_Parent("lab_id", "lab", "lab_stable_id"),),
    ),
    "lab_check": _Spec(
        "lab_check",
        lambda p: {
            "check_kind": "shell",
            "timeout_seconds": int(p["timeout_seconds"]),
            "script": p.get("script"),
        },
        columns=("check_kind", "timeout_seconds", "script"),
        parents=(_Parent("lab_task_id", "lab_task", "lab_task_stable_id"),),
    ),
}

#: Tables reflected from the migrated database, besides the ones the specs name.
_EXTRA_TABLES: tuple[str, ...] = ("content_provenance", "user", "lab_session")

_PROVENANCE_COLUMNS = (
    "content_version",
    "origin",
    "model",
    "prompt_id",
    "prompt_version",
    "generation_date",
    "reviewed_by",
    "reviewed_at",
)

#: Columns the adapter relies on besides each spec's own, by table.
_REQUIRED_COLUMNS: dict[str, tuple[str, ...]] = {
    "content_provenance": ("id", "stable_id", *_PROVENANCE_COLUMNS),
    "module": ("prerequisite_concept_ids",),
    "user": ("id", "stable_id"),
    "lab_session": ("lab_id", "status"),
}


def _norm_dt(value: object) -> str | None:
    """Normalize a datetime (or ISO string) to a UTC ISO string; naive values are UTC."""
    if value is None:
        return None
    parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
    if not isinstance(parsed, datetime):
        raise TypeError(f"not a datetime: {value!r}")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat()


def _provenance_values(provenance: dict[str, Any]) -> dict[str, Any]:
    """The comparable provenance columns of a payload's provenance record."""
    return {
        "origin": str(provenance["origin"]),
        "model": provenance.get("model"),
        "prompt_id": provenance.get("prompt_id"),
        "prompt_version": provenance.get("prompt_version"),
        "generation_date": _norm_dt(provenance.get("generation_date")),
    }


def _project(content_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Project an authored payload onto the relational columns the store persists."""
    spec = _SPECS[content_type]
    return {
        "scalars": spec.scalars(payload),
        "parents": {p.column: str(payload[p.key]) for p in spec.parents},
        "provenance": (_provenance_values(payload["provenance"]) if spec.provenance else None),
    }


class _StoredPayload(str):
    """A stored payload that equals any incoming payload with the same relational projection.

    The catalog has no column for the whole authored payload, so equality is decided on
    what is persisted: parsing the other side and projecting it must give the stored
    projection (see the module docstring). The string value is the canonical JSON of that
    projection, which keeps ``repr`` and logs meaningful.
    """

    _content_type: str
    _projection: dict[str, Any]

    def __new__(cls, content_type: str, projection: dict[str, Any]) -> _StoredPayload:
        text = json.dumps(projection, sort_keys=True, ensure_ascii=False, default=str)
        obj = super().__new__(cls, text)
        obj._content_type = content_type
        obj._projection = projection
        return obj

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, str):
            return NotImplemented
        if str.__eq__(self, other):
            return True
        try:
            data = json.loads(other)
            return isinstance(data, dict) and _project(self._content_type, data) == self._projection
        except (KeyError, TypeError, ValueError, AttributeError):
            return False

    def __ne__(self, other: object) -> bool:
        result = self.__eq__(other)
        return result if result is NotImplemented else not result

    __hash__ = str.__hash__


# -- the store ---------------------------------------------------------------------------


class Postgres_ContentStore:
    """A :class:`~edu_it_content.content_store.ContentStore` over a SQLAlchemy engine (Req 12.1).

    Constructed from an injected engine (PostgreSQL in production, SQLite in unit tests).
    Raises :class:`CatalogSchemaMissingError` when the migrated tables are absent.
    """

    def __init__(self, engine: Engine) -> None:
        """Reflect the catalog tables from ``engine`` and check the migrations were applied."""
        self._engine = engine
        self._tables = self._reflect(engine)
        # Everything this instance has written, by stable_id (authoritative within the run).
        self._shadow: dict[str, StoredItem] = {}
        self._pending: list[StoredItem] = []
        self._child_rows: dict[str, int] = {}
        self._child_slots: dict[tuple[str, int], int] = {}
        self._archived: set[str] = set()
        self._reviews: dict[str, tuple[str, datetime]] = {}
        self._golden: set[str] = set()
        self._orphaned: set[str] = set()

    # -- reflection --------------------------------------------------------------------

    @staticmethod
    def _reflect(engine: Engine) -> dict[str, Table]:
        wanted = sorted({s.table for s in _SPECS.values()} | set(_EXTRA_TABLES))
        present = set(inspect(engine).get_table_names())
        missing = [name for name in wanted if name not in present]
        if missing:
            raise CatalogSchemaMissingError(
                f"catalog table(s) missing from the database: {', '.join(missing)}; "
                "apply the migrations (edu-it-migrate) before syncing"
            )
        metadata = MetaData()
        metadata.reflect(bind=engine, only=wanted)
        required: dict[str, set[str]] = {name: set() for name in wanted}
        for spec in _SPECS.values():
            needed = {"id", *spec.columns, *(p.column for p in spec.parents)}
            if spec.has_stable_id:
                needed.add("stable_id")
            if spec.provenance:
                needed.add("provenance_id")
            required[spec.table] |= needed
        for name, columns in _REQUIRED_COLUMNS.items():
            required[name] |= set(columns)
        absent = [
            f"{name}.{column}"
            for name, columns in required.items()
            for column in sorted(columns)
            if column not in metadata.tables[name].c
        ]
        if absent:
            raise CatalogSchemaMissingError(
                f"catalog column(s) missing from the database: {', '.join(absent)}; "
                "apply the migrations (edu-it-migrate) before syncing"
            )
        return {name: metadata.tables[name] for name in wanted}

    # -- ContentStore port -------------------------------------------------------------

    def get(self, stable_id: str) -> StoredItem | None:
        """Return the stored item with ``stable_id`` (any status), or ``None``."""
        if stable_id in self._shadow:
            return self._shadow[stable_id]
        for content_type, spec in _SPECS.items():
            if spec.has_stable_id:
                item = self._read(content_type, stable_id)
                if item is not None:
                    return item
        return None

    def list_active_ids(self) -> frozenset[str]:
        """Return the ``stable_id`` of every ``ACTIVE`` item (flushes held-back writes first)."""
        self.flush()
        ids: set[str] = set()
        with self._engine.connect() as cx:
            for content_type, spec in _SPECS.items():
                if not spec.has_stable_id:
                    continue
                table = self._tables[spec.table]
                query = select(table.c.stable_id)
                if content_type == "track":
                    query = query.where(table.c.status != _ARCHIVED_TRACK_STATUS)
                elif content_type == "lab":
                    query = query.where(table.c.archived_at.is_(None))
                ids.update(str(row[0]) for row in cx.execute(query))
        ids.update(i for i, item in self._shadow.items() if item.status is ContentStatus.ACTIVE)
        return frozenset(ids - self._archived)

    def list_items(self, content_type: str) -> tuple[StoredItem, ...]:
        """Return every stored item of ``content_type`` (any status), ordered by ``stable_id``."""
        self.flush()
        spec = _SPECS.get(content_type)
        if spec is None:
            return ()
        found: dict[str, StoredItem] = {
            i: item for i, item in self._shadow.items() if item.content_type == content_type
        }
        if spec.has_stable_id:
            with self._engine.connect() as cx:
                id_column = self._tables[spec.table].c.stable_id
                ids = [str(row[0]) for row in cx.execute(select(id_column))]
            for stable_id in ids:
                if stable_id not in found:
                    item = self._read(content_type, str(stable_id))
                    if item is not None:
                        found[str(stable_id)] = item
        return tuple(found[i] for i in sorted(found))

    def upsert(self, item: StoredItem) -> None:
        """Insert or update ``item`` by ``stable_id``; rows already equal are not written."""
        spec = _SPECS.get(item.content_type)
        if spec is None:
            raise CatalogWriteError(f"unknown content type {item.content_type!r}")
        self._shadow[item.stable_id] = item
        self._archived.discard(item.stable_id)
        # Children without a stable_id column are matched by position, so they are written
        # in authored order at flush time; others are written now when their parents exist.
        if spec.has_stable_id and self._write(item):
            return
        self._pending.append(item)

    def archive(self, stable_id: str) -> None:
        """Transition ``stable_id`` to ``ARCHIVED``; a no-op when absent or already archived."""
        self.flush()
        existing = self.get(stable_id)
        if existing is None or existing.status is ContentStatus.ARCHIVED:
            return
        with self._engine.begin() as cx:
            if existing.content_type == "track":
                table = self._tables["track"]
                cx.execute(
                    update(table)
                    .where(table.c.stable_id == stable_id)
                    .values(status=_ARCHIVED_TRACK_STATUS)
                )
            elif existing.content_type == "lab":
                table = self._tables["lab"]
                cx.execute(
                    update(table)
                    .where(table.c.stable_id == stable_id)
                    .values(archived_at=datetime.now(UTC))
                )
            else:
                self._archived.add(stable_id)
        if stable_id in self._shadow:
            self._shadow[stable_id] = _with_status(self._shadow[stable_id], ContentStatus.ARCHIVED)

    def has_active_sessions(self, stable_id: str) -> bool:
        """Return whether a lab session in flight references the lab ``stable_id`` (Req 26.2)."""
        lab = self._tables["lab"]
        session = self._tables["lab_session"]
        query = (
            select(session.c.status)
            .select_from(session.join(lab, session.c.lab_id == lab.c.id))
            .where(lab.c.stable_id == stable_id, session.c.status.in_(_ACTIVE_LAB_SESSION_STATUSES))
            .limit(1)
        )
        with self._engine.connect() as cx:
            return cx.execute(query).first() is not None

    def record_review(self, stable_id: str, reviewed_by: str, reviewed_at: datetime) -> None:
        """Record the reviewer: ``reviewed_at`` always, ``reviewed_by`` when it is a known user."""
        self._reviews[stable_id] = (reviewed_by, reviewed_at)
        self.flush()
        provenance = self._tables["content_provenance"]
        users = self._tables["user"]
        with self._engine.begin() as cx:
            if (
                cx.execute(
                    select(provenance.c.id).where(provenance.c.stable_id == stable_id)
                ).first()
                is None
            ):
                return
            reviewer_id = cx.execute(
                select(users.c.id).where(users.c.stable_id == reviewed_by)
            ).scalar()
            values: dict[str, Any] = {"reviewed_at": reviewed_at}
            if reviewer_id is not None:
                values["reviewed_by"] = reviewer_id
            else:
                _LOG.warning(
                    "reviewer %r is not a known user; only reviewed_at is stored", reviewed_by
                )
            cx.execute(
                update(provenance).where(provenance.c.stable_id == stable_id).values(**values)
            )

    def review_of(self, stable_id: str) -> tuple[str, datetime] | None:
        """Read back the recorded ``(reviewed_by, reviewed_at)`` of ``stable_id``, if any."""
        if stable_id in self._reviews:
            return self._reviews[stable_id]
        provenance = self._tables["content_provenance"]
        users = self._tables["user"]
        query = (
            select(users.c.stable_id, provenance.c.reviewed_at)
            .select_from(provenance.join(users, provenance.c.reviewed_by == users.c.id))
            .where(provenance.c.stable_id == stable_id, provenance.c.reviewed_at.is_not(None))
        )
        with self._engine.connect() as cx:
            row = cx.execute(query).first()
        if row is None:
            return None
        reviewed_at = row[1]
        if reviewed_at.tzinfo is None:
            reviewed_at = reviewed_at.replace(tzinfo=UTC)
        return str(row[0]), reviewed_at

    def mark_golden(self, stable_ids: frozenset[str]) -> None:
        """Keep the Golden_Set in process for the run and log it (design decision 10)."""
        self._golden |= stable_ids
        _LOG.info("golden set marked in process: %d item(s) (not persisted)", len(stable_ids))

    def orphan_progress(self, stable_id: str) -> None:
        """Keep the orphaned flag in process and log it; progress rows are never purged."""
        self._orphaned.add(stable_id)
        _LOG.info("progress for %s kept as orphaned (in process; not persisted)", stable_id)

    # -- extras (not part of the port) -------------------------------------------------

    def golden_ids(self) -> frozenset[str]:
        """Return the Golden_Set marked during this run."""
        return frozenset(self._golden)

    def orphaned_ids(self) -> frozenset[str]:
        """Return the stable ids whose progress was flagged orphaned during this run."""
        return frozenset(self._orphaned)

    def flush(self) -> None:
        """Write the held-back items; raise when a parent row never appears."""
        wrote = bool(self._pending)
        try:
            self._flush_pending()
        finally:
            if wrote:
                self._write_module_gates()
        # The links are derived from rows already stored, so repair them on every flush:
        # an existing database whose content did not change still receives them.
        self._write_question_concepts()

    def _write_question_concepts(self) -> None:
        """Write each question's concept links (``question.concept_stable_ids``).

        D3 has a question-to-concept relation but the content schema carries no field for
        it, so a question covers the concept named like its quiz (``quiz.x`` ->
        ``concept.x``), the convention the performance seed uses. A quiz with no such
        concept leaves its questions NULL.
        """
        quiz, question, concept = (self._tables[n] for n in ("quiz", "question", "concept"))
        with self._engine.begin() as cx:
            known = {str(sid) for (sid,) in cx.execute(select(concept.c.stable_id))}
            for quiz_id, quiz_sid in cx.execute(select(quiz.c.id, quiz.c.stable_id)).all():
                target = "concept." + str(quiz_sid).split(".", 1)[-1]
                links = json.dumps([target]) if target in known else None
                cx.execute(
                    update(question)
                    .where(question.c.quiz_id == quiz_id)
                    .where(
                        question.c.concept_stable_ids.is_not(None)
                        if links is None
                        else (
                            question.c.concept_stable_ids.is_(None)
                            | (question.c.concept_stable_ids != links)
                        )
                    )
                    .values(concept_stable_ids=links)
                )

    def _flush_pending(self) -> None:
        while self._pending:
            queue, self._pending = self._pending, []
            remaining = [item for item in queue if not self._write(item)]
            if len(remaining) == len(queue):
                self._pending = []
                missing = "; ".join(self._describe_missing(item) for item in remaining)
                raise CatalogWriteError(
                    f"cannot write catalog item(s), parent row absent: {missing}"
                )
            self._pending = remaining

    def _write_module_gates(self) -> None:
        """Write each module's gating set (``module.prerequisite_concept_ids``, Req 2.1).

        D3 has no module-to-concept join, so a module is gated by the prerequisites of the
        concept named like it (``module.x`` -> ``concept.x``), the convention the performance
        seed uses. A module with no such concept or no edges keeps an empty gate.
        """
        module, concept, edge = (self._tables[n] for n in ("module", "concept", "prerequisite"))
        required = concept.alias("required")
        with self._engine.begin() as cx:
            gates: dict[str, list[str]] = {}
            for dependent, needed in cx.execute(
                select(concept.c.stable_id, required.c.stable_id)
                .select_from(
                    edge.join(concept, edge.c.concept_id == concept.c.id).join(
                        required, edge.c.requires_concept_id == required.c.id
                    )
                )
                .order_by(edge.c.id)
            ):
                gates.setdefault(str(dependent), []).append(str(needed))
            for row in cx.execute(
                select(module.c.id, module.c.stable_id, module.c.prerequisite_concept_ids)
            ).all():
                gate = gates.get("concept." + str(row[1]).split(".", 1)[-1], [])
                stored = json.loads(row[2]) if row[2] else []
                if stored != gate:
                    cx.execute(
                        update(module)
                        .where(module.c.id == row[0])
                        .values(prerequisite_concept_ids=json.dumps(gate) if gate else None)
                    )

    # -- internals ---------------------------------------------------------------------

    def _describe_missing(self, item: StoredItem) -> str:
        spec = _SPECS[item.content_type]
        payload = json.loads(item.payload)
        with self._engine.connect() as cx:
            for parent in spec.parents:
                if self._id_of(cx, parent.table, str(payload[parent.key])) is None:
                    return (
                        f"{item.content_type} {item.stable_id!r} needs {parent.table} "
                        f"{payload[parent.key]!r}"
                    )
        return f"{item.content_type} {item.stable_id!r}"

    def _id_of(self, cx: Connection, table_name: str, stable_id: str) -> int | None:
        table = self._tables[table_name]
        value = cx.execute(select(table.c.id).where(table.c.stable_id == stable_id)).scalar()
        return None if value is None else int(value)

    def _write(self, item: StoredItem) -> bool:
        """Write ``item``; return ``False`` (writing nothing) when a parent row is missing."""
        spec = _SPECS[item.content_type]
        payload: dict[str, Any] = json.loads(item.payload)
        table = self._tables[spec.table]
        with self._engine.begin() as cx:
            parent_ids: dict[str, int] = {}
            for parent in spec.parents:
                parent_id = self._id_of(cx, parent.table, str(payload[parent.key]))
                if parent_id is None:
                    return False
                parent_ids[parent.column] = parent_id
            values: dict[str, Any] = {**spec.scalars(payload), **parent_ids}
            if spec.provenance:
                values["provenance_id"] = self._write_provenance(
                    cx, payload["provenance"], item.content_version
                )
            if spec.has_stable_id:
                row = (
                    cx.execute(select(table).where(table.c.stable_id == item.stable_id))
                    .mappings()
                    .first()
                )
                if row is None:
                    cx.execute(
                        insert(table).values(
                            stable_id=item.stable_id, **spec.insert_defaults, **values
                        )
                    )
                else:
                    changed = {k: v for k, v in values.items() if row[k] != v}
                    if changed:
                        cx.execute(
                            update(table)
                            .where(table.c.stable_id == item.stable_id)
                            .values(**changed)
                        )
            else:
                self._write_child(cx, table, item.stable_id, values, parent_ids)
        return True

    def _write_child(
        self,
        cx: Connection,
        table: Table,
        stable_id: str,
        values: dict[str, Any],
        parents: dict[str, int],
    ) -> None:
        """Write a child row that has no ``stable_id`` column, matched by position."""
        row_id = self._child_rows.get(stable_id)
        if row_id is None:
            parent_column, parent_id = next(iter(parents.items()))
            slot_key = (table.name, parent_id)
            slot = self._child_slots.get(slot_key, 0)
            self._child_slots[slot_key] = slot + 1
            existing = [
                int(r[0])
                for r in cx.execute(
                    select(table.c.id)
                    .where(table.c[parent_column] == parent_id)
                    .order_by(table.c.id)
                )
            ]
            if slot < len(existing):
                row_id = int(existing[slot])
            else:
                inserted = cx.execute(insert(table).values(**values)).inserted_primary_key
                assert inserted is not None
                row_id = int(inserted[0])
            self._child_rows[stable_id] = row_id
        current = cx.execute(select(table).where(table.c.id == row_id)).mappings().first()
        if current is None:  # pragma: no cover - the row was removed behind our back
            raise CatalogWriteError(f"{table.name} row {row_id} disappeared")
        changed = {k: v for k, v in values.items() if current[k] != v}
        if changed:
            cx.execute(update(table).where(table.c.id == row_id).values(**changed))

    def _write_provenance(self, cx: Connection, provenance: dict[str, Any], version: str) -> int:
        """Insert or update the ``content_provenance`` row; the review trail is left untouched."""
        table = self._tables["content_provenance"]
        generated = provenance.get("generation_date")
        values: dict[str, Any] = {
            "content_version": version,
            "origin": str(provenance["origin"]),
            "model": provenance.get("model"),
            "prompt_id": provenance.get("prompt_id"),
            "prompt_version": provenance.get("prompt_version"),
            "generation_date": None if generated is None else datetime.fromisoformat(generated),
        }
        stable_id = str(provenance["stable_id"])
        row = cx.execute(select(table).where(table.c.stable_id == stable_id)).mappings().first()
        if row is None:
            result = cx.execute(insert(table).values(stable_id=stable_id, **values))
            inserted = result.inserted_primary_key
            assert inserted is not None
            return int(inserted[0])
        changed = {
            k: v
            for k, v in values.items()
            if (_norm_dt(row[k]) != _norm_dt(v) if k == "generation_date" else row[k] != v)
        }
        if changed:
            cx.execute(update(table).where(table.c.id == row["id"]).values(**changed))
        return int(row["id"])

    def _read(self, content_type: str, stable_id: str) -> StoredItem | None:
        """Rebuild a :class:`StoredItem` from the persisted rows of a ``stable_id`` table."""
        spec = _SPECS[content_type]
        table = self._tables[spec.table]
        with self._engine.connect() as cx:
            row = cx.execute(select(table).where(table.c.stable_id == stable_id)).mappings().first()
            if row is None:
                return None
            scalars = {
                column: (_norm_dt(row[column]) if column == "archived_at" else row[column])
                for column in spec.columns
            }
            parents: dict[str, str] = {}
            for parent in spec.parents:
                parent_table = self._tables[parent.table]
                parents[parent.column] = str(
                    cx.execute(
                        select(parent_table.c.stable_id).where(
                            parent_table.c.id == row[parent.column]
                        )
                    ).scalar_one()
                )
            provenance: dict[str, Any] | None = None
            version: str | None = None
            if spec.provenance:
                prov_table = self._tables["content_provenance"]
                prow = (
                    cx.execute(select(prov_table).where(prov_table.c.id == row["provenance_id"]))
                    .mappings()
                    .one()
                )
                version = str(prow["content_version"])
                provenance = {
                    "origin": prow["origin"],
                    "model": prow["model"],
                    "prompt_id": prow["prompt_id"],
                    "prompt_version": prow["prompt_version"],
                    "generation_date": _norm_dt(prow["generation_date"]),
                }
        projection = {"scalars": scalars, "parents": parents, "provenance": provenance}
        payload = _StoredPayload(content_type, projection)
        if content_type == "track":
            archived = row["status"] == _ARCHIVED_TRACK_STATUS
        elif content_type == "lab":
            archived = row["archived_at"] is not None
        else:
            archived = stable_id in self._archived
        return StoredItem(
            stable_id=stable_id,
            content_type=content_type,
            # Sub-items without a provenance record use their payload as their version.
            content_version=version if version is not None else payload,
            status=ContentStatus.ARCHIVED if archived else ContentStatus.ACTIVE,
            payload=payload,
        )


def _with_status(item: StoredItem, status: ContentStatus) -> StoredItem:
    return StoredItem(
        stable_id=item.stable_id,
        content_type=item.content_type,
        content_version=item.content_version,
        status=status,
        payload=item.payload,
    )
