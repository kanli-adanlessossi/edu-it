"""Shared ContentStore contract suite (api-route-wiring task 7.3, Property 18).

``apps/api`` is the only package that depends on both ``edu-it-content`` and
``edu-it-infrastructure``, so the schema is created here from the infrastructure
metadata and the same contract runs against ``In_Memory_ContentStore`` and
``Postgres_ContentStore`` (SQLite always, PostgreSQL when ``DATABASE_URL`` is set).

Known limit of ``Postgres_ContentStore`` (design decision 10, no new table): the
children without a ``stable_id`` column (question_option, rubric_criterion,
prerequisite) are matched by position and only recognised by the process that wrote
them. A second sync from a *fresh* store therefore reports them as inserted, but writes
no row; the contract asserts the true invariant (row counts and contents stable).
"""

from __future__ import annotations

import copy
import json
import os
import uuid
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest
from edu_it_content.content_store import (
    ContentStatus,
    ContentStore,
    In_Memory_ContentStore,
)
from edu_it_content.postgres_store import Postgres_ContentStore
from edu_it_content.sync import sync_content
from edu_it_content.validation import LoadedContent, load_content
from edu_it_infrastructure.persistence.metadata import metadata
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy import Engine, create_engine, func, select, text

_REPO = Path(__file__).resolve().parents[3]
_CATALOG_TABLES = (
    "track",
    "module",
    "lesson",
    "concept",
    "prerequisite",
    "quiz",
    "question",
    "question_option",
    "project",
    "rubric",
    "rubric_criterion",
    "lab",
    "lab_task",
    "lab_check",
    "content_provenance",
)

StoreFactory = Callable[[], ContentStore]


@pytest.fixture(scope="module")
def seed() -> LoadedContent:
    return load_content(_REPO / "content")


@pytest.fixture(params=["memory", "sqlite", "postgres"])
def backend(
    request: pytest.FixtureRequest, tmp_path: Path
) -> Iterator[tuple[StoreFactory, Engine | None]]:
    """Yield ``(factory, engine)``; the factory builds a store over the same database."""
    kind = request.param
    if kind == "memory":
        shared = In_Memory_ContentStore()
        yield (lambda: shared), None
        return
    if kind == "sqlite":
        engine = create_engine(f"sqlite:///{tmp_path / 'catalog.db'}")
        metadata.create_all(engine)
        yield (lambda: Postgres_ContentStore(engine)), engine
        engine.dispose()
        return
    url = os.environ.get("DATABASE_URL", "")
    if not url.startswith("postgresql"):
        pytest.skip("DATABASE_URL does not point at PostgreSQL")
    schema = f"contract_{uuid.uuid4().hex[:12]}"
    admin = create_engine(url)
    with admin.begin() as cx:
        cx.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    try:
        metadata.create_all(engine)
        yield (lambda: Postgres_ContentStore(engine)), engine
    finally:
        engine.dispose()
        with admin.begin() as cx:
            cx.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def _counts(engine: Engine) -> dict[str, int]:
    with engine.connect() as cx:
        return {
            name: int(
                cx.execute(select(func.count()).select_from(metadata.tables[name])).scalar_one()
            )
            for name in _CATALOG_TABLES
        }


def _snapshot(engine: Engine) -> dict[str, list[tuple[object, ...]]]:
    with engine.connect() as cx:
        return {
            name: sorted(
                (tuple(map(str, row)) for row in cx.execute(select(metadata.tables[name]))),
            )
            for name in _CATALOG_TABLES
        }


def _without(content: LoadedContent, type_key: str, stable_id: str) -> LoadedContent:
    raw = {
        key: [item for item in items if not (key == type_key and _sid(item) == stable_id)]
        for key, items in content.raw_items.items()
    }
    return LoadedContent(raw_items=raw, root=content.root)


def test_empty_store_has_nothing(backend: tuple[StoreFactory, Engine | None]) -> None:
    store = backend[0]()
    assert store.get("missing") is None
    assert store.list_active_ids() == frozenset()
    assert store.list_items("track") == ()
    assert store.has_active_sessions("missing") is False


def test_sync_writes_every_authored_item_and_reads_it_back(
    backend: tuple[StoreFactory, Engine | None], seed: LoadedContent
) -> None:
    store = backend[0]()
    report = sync_content(store, seed)
    authored = {i for i, _ in _authored(seed)}
    assert set(report.inserted) == authored
    assert not report.updated and not report.archived
    for stable_id in authored:
        item = store.get(stable_id)
        assert item is not None and item.status is ContentStatus.ACTIVE
    assert store.list_active_ids() >= {
        i for i, t in _authored(seed) if t in {"track", "module", "lesson", "lab"}
    }


def test_second_sync_changes_nothing(
    backend: tuple[StoreFactory, Engine | None], seed: LoadedContent
) -> None:
    factory, engine = backend
    store = factory()
    first = sync_content(store, seed)
    before = _snapshot(engine) if engine is not None else None
    second = sync_content(store, seed)
    assert not second.changed
    assert set(second.unchanged) == set(first.inserted)
    if engine is not None:
        assert _snapshot(engine) == before


def test_second_sync_from_a_fresh_store_writes_no_row(
    backend: tuple[StoreFactory, Engine | None], seed: LoadedContent
) -> None:
    factory, engine = backend
    sync_content(factory(), seed)
    if engine is None:
        return
    before = _snapshot(engine)
    second = sync_content(factory(), seed)
    assert not second.updated and not second.archived
    # Items with a stable_id column are recognised; positional children are not (see docstring).
    positional = {"question_option", "rubric_criterion", "prerequisite"}
    assert {i for i, t in _authored(seed) if t not in positional} <= set(second.unchanged)
    assert set(second.inserted) <= {i for i, t in _authored(seed) if t in positional}
    assert _snapshot(engine) == before


def test_removed_item_is_archived_not_deleted_and_reactivated(
    backend: tuple[StoreFactory, Engine | None], seed: LoadedContent
) -> None:
    factory, engine = backend
    store = factory()
    sync_content(store, seed)
    lab_id = next(i for i, t in _authored(seed) if t == "lab")
    # Archive the lab together with its tasks and checks so the content stays consistent.
    reduced = seed
    for type_key in ("lab_check", "lab_task", "lab"):
        for stable_id in [
            i for i, t in _authored(reduced) if t == type_key and _belongs(reduced, i, lab_id)
        ]:
            reduced = _without(reduced, type_key, stable_id)
    report = sync_content(store, reduced)
    assert lab_id in report.archived
    archived = store.get(lab_id)
    assert archived is not None and archived.status is ContentStatus.ARCHIVED
    assert lab_id not in store.list_active_ids()
    if engine is not None:
        # The row is kept (archived_at set), never deleted.
        assert _counts(engine)["lab"] == sum(1 for _, t in _authored(seed) if t == "lab")
    back = sync_content(store, seed)
    assert lab_id in back.updated or lab_id in back.inserted
    restored = store.get(lab_id)
    assert restored is not None and restored.status is ContentStatus.ACTIVE
    if engine is not None:
        assert _counts(engine)["lab"] == sum(1 for _, t in _authored(seed) if t == "lab")


def test_edit_updates_in_place_without_a_duplicate(
    backend: tuple[StoreFactory, Engine | None], seed: LoadedContent
) -> None:
    factory, engine = backend
    store = factory()
    sync_content(store, seed)
    counts = _counts(engine) if engine is not None else None
    edited: dict[str, list[Any]] = copy.deepcopy({k: list(v) for k, v in seed.raw_items.items()})
    target = dict(edited["track"][0])
    title = dict(target["title"])
    title["en"] = title["en"] + " (edited)"
    target["title"] = title
    prov = dict(target["provenance"])
    prov["content_version"] = "edited-" + str(prov["content_version"])
    target["provenance"] = prov
    edited["track"][0] = target
    report = sync_content(store, LoadedContent(raw_items=edited, root=seed.root))
    assert report.updated == (prov["stable_id"],)
    if engine is not None:
        assert _counts(engine) == counts


@settings(
    max_examples=5, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(runs=st.integers(min_value=2, max_value=3))
def test_property_18_repeated_sync_is_idempotent(
    backend: tuple[StoreFactory, Engine | None], seed: LoadedContent, runs: int
) -> None:
    """**Validates: Requirements 12.1, 12.3, 12.4**

    Property 18: any number of syncs of the same content leaves one active item per
    ``stable_id`` and never changes a persisted row after the first.
    """
    factory, engine = backend
    store = factory()
    sync_content(store, seed)
    snapshot = _snapshot(engine) if engine is not None else None
    ids = {i for i, _ in _authored(seed)}
    for _ in range(runs - 1):
        assert not sync_content(store, seed).changed
    if engine is not None:
        assert _snapshot(engine) == snapshot
    kept = [i for i in store.list_active_ids() if i in ids]
    assert len(kept) == len(set(kept))


def _sid(item: Mapping[str, Any]) -> str:
    prov = item.get("provenance")
    return str(prov["stable_id"] if prov else item["stable_id"])


def _authored(content: LoadedContent) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for key, items in content.raw_items.items():
        for item in items:
            out.append((_sid(item), key))
    return out


def _belongs(content: LoadedContent, stable_id: str, lab_id: str) -> bool:
    """Whether the lab, task or check ``stable_id`` is the lab ``lab_id`` or below it."""
    tasks = {
        str(t["stable_id"])
        for t in content.raw_items.get("lab_task", ())
        if t["lab_stable_id"] == lab_id
    }
    checks = {
        str(c["stable_id"])
        for c in content.raw_items.get("lab_check", ())
        if c["lab_task_stable_id"] in tasks
    }
    return stable_id == lab_id or stable_id in tasks or stable_id in checks


def test_sync_writes_the_module_gate_from_the_concept_prerequisites(
    backend: tuple[StoreFactory, Engine | None], seed: LoadedContent
) -> None:
    """Task 13.6: a module is gated by the prerequisites of the concept named like it.

    Without the gate every module is vacuously unlocked right after enrolling, so the
    track page never shows "Processes" locked (Req 2.1, 17.2).
    """
    factory, engine = backend
    if engine is None:
        pytest.skip("the in-memory store has no module rows")
    sync_content(factory(), seed)
    module = metadata.tables["module"]
    with engine.connect() as cx:
        gates = {
            str(sid): json.loads(str(gate)) if gate else []
            for sid, gate in cx.execute(
                select(module.c.stable_id, module.c.prerequisite_concept_ids)
            )
        }
    required: dict[str, set[str]] = {}
    for edge in seed.raw_items.get("prerequisite", []):
        required.setdefault(str(edge["concept_stable_id"]), set()).add(
            str(edge["requires_concept_stable_id"])
        )
    assert required, "the seed content authors prerequisite edges"
    for stable_id, gate in gates.items():
        expected = required.get("concept." + stable_id.split(".", 1)[1], set())
        assert set(gate) == expected, stable_id
    assert gates["module.processes"], "Processes starts locked behind its prerequisites"


def test_sync_writes_the_question_to_concept_links_by_the_quiz_name(
    backend: tuple[StoreFactory, Engine | None], seed: LoadedContent
) -> None:
    """Task 13.11: a question covers the concept named like its quiz (``quiz.x`` -> ``concept.x``).

    Without the links a perfect quiz credits no concept, so Processes never unlocks
    (Req 2.1, 17.2). A quiz with no such concept keeps NULL.
    """
    factory, engine = backend
    if engine is None:
        pytest.skip("the in-memory store has no question rows")
    sync_content(factory(), seed)
    quiz, question = metadata.tables["quiz"], metadata.tables["question"]
    concepts = {_sid(item) for item in seed.raw_items.get("concept", [])}
    with engine.connect() as cx:
        rows = cx.execute(
            select(quiz.c.stable_id, question.c.concept_stable_ids).select_from(
                question.join(quiz, question.c.quiz_id == quiz.c.id)
            )
        ).all()
    assert rows, "the seed content authors questions"
    for quiz_id, links in rows:
        concept = "concept." + str(quiz_id).split(".", 1)[1]
        expected = [concept] if concept in concepts else None
        assert (json.loads(str(links)) if links else None) == expected, quiz_id
    reading = [json.loads(str(links)) for qid, links in rows if qid == "quiz.reading-permissions"]
    assert reading and all(links == ["concept.reading-permissions"] for links in reading)
