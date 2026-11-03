"""Shared contract suite for ``VariantRepository`` and ``ReviewTaskRepository`` (task 12.2).

One parametrized suite runs against the in-memory fakes and the SQL adapters: SQLite
always, PostgreSQL additionally when ``DATABASE_URL`` is set.

Validates: Requirements 13.3, 15.4, 15.5, 15.10, 23.4
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

import pytest
from edu_it_domain.ai.lifecycle import SERVABLE_STATES, InvalidTransitionError, VariantState
from edu_it_domain.ports.review_tasks import (
    Decision,
    DecisionOutcome,
    NewReviewTask,
    ReviewTaskAlreadyDecidedError,
    ReviewTaskRepository,
    TargetKind,
    TaskKind,
    TaskState,
)
from edu_it_domain.ports.variants import (
    NewRevision,
    NewVariant,
    RevisionState,
    VariantRepository,
)
from edu_it_infrastructure.fakes import (
    In_Memory_ReviewTaskRepository,
    In_Memory_VariantRepository,
    Test_Clock,
)
from edu_it_infrastructure.persistence import (
    Postgres_ReviewTaskRepository,
    Postgres_VariantRepository,
    metadata,
)
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import create_engine, event, insert
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool

_T0 = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
_DATABASE_URL = os.environ.get("DATABASE_URL", "")
_POSTGRES_URL = _DATABASE_URL if _DATABASE_URL.startswith("postgresql") else ""

_BACKENDS = [
    "fake",
    "sqlite",
    pytest.param(
        "postgres",
        marks=pytest.mark.skipif(not _POSTGRES_URL, reason="DATABASE_URL (PostgreSQL) not set"),
    ),
]

S = VariantState


@dataclass
class _Backend:
    variants: VariantRepository
    tasks: ReviewTaskRepository
    clock: Test_Clock
    ns: str
    engine: Engine | None = None
    _parents: set[str] | None = None

    def name(self, label: str) -> str:
        """An id unique to this test, so a shared PostgreSQL database never collides."""
        return f"{self.ns}-{label}"

    def parent(self, label: str = "q") -> str:
        """A parent question id; seeded in the catalog for the SQL backends."""
        stable_id = self.name(label)
        if self.engine is not None:
            _seed_question(self.engine, stable_id)
        return stable_id

    def reviewer(self, label: str = "rev") -> str:
        stable_id = self.name(label)
        if self.engine is not None:
            with self.engine.begin() as c:
                c.execute(
                    insert(metadata.tables["user"]).values(
                        stable_id=stable_id,
                        email=f"{stable_id}@example.org",
                        locale="en",
                        status="active",
                    )
                )
        return stable_id

    def variant(self, label: str, parent: str, state: VariantState = S.PENDING_REVIEW) -> str:
        stable_id = self.name(label)
        self.variants.add(NewVariant(stable_id, parent, self.name("trk"), state))
        return stable_id


def _seed_question(engine: Engine, question_id: str) -> None:
    t = metadata.tables
    with engine.begin() as c:

        def one(table: str, **values: object) -> int:
            result = c.execute(insert(t[table]).values(**values))
            assert result.inserted_primary_key is not None
            return int(result.inserted_primary_key[0])

        prov = one(
            "content_provenance",
            stable_id=f"{question_id}-prov",
            content_version="v1",
            origin="authored",
        )
        track = one(
            "track",
            stable_id=f"{question_id}-t",
            provenance_id=prov,
            title_fr="p",
            title_en="t",
            status="published",
        )
        module = one(
            "module", stable_id=f"{question_id}-m", track_id=track, provenance_id=prov, ordinal=1
        )
        lesson = one(
            "lesson", stable_id=f"{question_id}-l", module_id=module, provenance_id=prov, ordinal=1
        )
        quiz = one("quiz", stable_id=f"{question_id}-z", lesson_id=lesson, provenance_id=prov)
        one("question", stable_id=question_id, quiz_id=quiz, provenance_id=prov, prompt_text="?")


def _sqlite_engine() -> Engine:
    eng = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(eng, "connect")
    def _fk(dbapi_connection: object, _record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    metadata.create_all(eng)
    return eng


def _fake_backend() -> _Backend:
    clock = Test_Clock(_T0)
    variants = In_Memory_VariantRepository(clock)
    return _Backend(
        variants, In_Memory_ReviewTaskRepository(clock, variants), clock, uuid.uuid4().hex[:8]
    )


def _sql_backend(engine: Engine) -> _Backend:
    clock = Test_Clock(_T0)
    return _Backend(
        Postgres_VariantRepository(engine, clock),
        Postgres_ReviewTaskRepository(engine, clock),
        clock,
        uuid.uuid4().hex[:8],
        engine,
    )


@pytest.fixture(params=_BACKENDS)
def b(request: pytest.FixtureRequest) -> Iterator[_Backend]:
    if request.param == "fake":
        yield _fake_backend()
        return
    engine = _sqlite_engine() if request.param == "sqlite" else create_engine(_POSTGRES_URL)
    if request.param == "postgres":
        metadata.create_all(engine)
    try:
        yield _sql_backend(engine)
    finally:
        engine.dispose()


def _task(b: _Backend, label: str, target: str, **kw: object) -> NewReviewTask:
    fields: dict[str, object] = {
        "kind": TaskKind.NEW_VARIANT,
        "target_kind": TargetKind.VARIANT,
        "track_stable_id": b.name("trk"),
    }
    fields.update(kw)
    return NewReviewTask(stable_id=b.name(label), target_stable_id=target, **fields)  # type: ignore[arg-type]


def _revision(variant: str, version: str, **kw: object) -> NewRevision:
    return NewRevision(variant, version, '{"en": {"prompt": "?"}}', **kw)  # type: ignore[arg-type]


# --- VariantRepository ---------------------------------------------------------------------


def test_add_and_get_round_trip(b: _Backend) -> None:
    parent = b.parent()
    b.variants.add(NewVariant(b.name("v"), parent, b.name("trk")))
    found = b.variants.get(b.name("v"))
    assert found is not None
    assert (found.parent_question_stable_id, found.track_stable_id) == (parent, b.name("trk"))
    assert found.state is S.GENERATED
    assert found.created_at == _T0
    assert found.approved_content_version is None
    assert b.variants.get(b.name("missing")) is None


def test_add_duplicate_raises(b: _Backend) -> None:
    parent = b.parent()
    b.variant("v", parent)
    with pytest.raises(ValueError):
        b.variant("v", parent)


def test_save_applies_legal_transition_and_fields(b: _Backend) -> None:
    v = b.variant("v", b.parent(), S.PENDING_REVIEW)
    stored = b.variants.get(v)
    assert stored is not None
    b.variants.save(replace(stored, state=S.APPROVED, approved_content_version="c1"))
    saved = b.variants.get(v)
    assert saved is not None
    assert (saved.state, saved.approved_content_version) == (S.APPROVED, "c1")


def test_save_rejects_illegal_transition_and_writes_nothing(b: _Backend) -> None:
    v = b.variant("v", b.parent(), S.PENDING_REVIEW)
    stored = b.variants.get(v)
    assert stored is not None
    with pytest.raises(InvalidTransitionError):
        b.variants.save(replace(stored, state=S.SERVED, approved_content_version="c1"))
    assert b.variants.get(v) == stored


def test_save_unknown_variant_raises(b: _Backend) -> None:
    v = b.variant("v", b.parent())
    stored = b.variants.get(v)
    assert stored is not None
    with pytest.raises(ValueError):
        b.variants.save(replace(stored, stable_id=b.name("ghost")))


def test_count_and_parents_below_count_servable_states_only(b: _Backend) -> None:
    low, full = b.parent("low"), b.parent("full")
    b.variant("a", low, S.APPROVED)
    b.variant("b", low, S.PENDING_REVIEW)
    b.variant("c", low, S.WITHDRAWN)
    b.variant("d", full, S.APPROVED)
    b.variant("e", full, S.SERVED)
    assert b.variants.count_approved(low) == 1
    assert b.variants.count_approved(full) == 2
    assert b.variants.count_approved(b.name("none")) == 0
    below = set(b.variants.parents_below(2))
    assert low in below
    assert full not in below
    assert full in set(b.variants.parents_below(3))


def test_list_servable_returns_approved_revision_not_pending_edit(b: _Backend) -> None:
    parent = b.parent()
    v = b.variant("v", parent, S.SERVED)
    other = b.variant("w", parent, S.PENDING_REVIEW)
    b.variants.add_revision(_revision(v, "c1", state=RevisionState.APPROVED))
    b.variants.add_revision(_revision(v, "c2"))  # pending edit of a served variant
    b.variants.add_revision(_revision(other, "c1"))
    stored = b.variants.get(v)
    assert stored is not None
    b.variants.save(replace(stored, approved_content_version="c1"))
    served = b.variants.list_servable(parent)
    assert [(r.variant_stable_id, r.content_version) for r in served] == [(v, "c1")]
    assert served[0].state is RevisionState.APPROVED


def test_listings_use_code_point_order_not_locale_collation(b: _Backend) -> None:
    # PostgreSQL en_US.utf8 would give a < B < _x < y10 < y2; the port's code-point
    # order (Python sorted()) is B < _x < a < y10 < y2.
    labels = ("a", "B", "_x", "y2", "y10")
    parent = b.parent()
    for label in labels:
        v = b.variant(f"v{label}", parent, S.APPROVED)
        b.variants.add_revision(_revision(v, "c1", state=RevisionState.APPROVED))
        stored = b.variants.get(v)
        assert stored is not None
        b.variants.save(replace(stored, approved_content_version="c1"))
    expected = sorted(b.name(f"v{label}") for label in labels)
    assert [r.variant_stable_id for r in b.variants.list_servable(parent)] == expected
    # parents_below is global on a shared database: keep only this test's parents.
    parents = [b.parent(f"p{label}") for label in labels]
    for parent_id in parents:
        b.variant(f"x{parent_id}", parent_id, S.PENDING_REVIEW)  # none servable
    assert [p for p in b.variants.parents_below(1) if p.startswith(b.ns)] == sorted(parents)


def test_add_revision_round_trip_and_duplicate(b: _Backend) -> None:
    v = b.variant("v", b.parent())
    reviewer = b.reviewer()
    when = _T0 - timedelta(hours=1)
    b.variants.add_revision(
        _revision(
            v,
            "c1",
            origin="ai",
            model="m",
            prompt_id="p",
            prompt_version="1",
            generation_date=when,
            reviewed_by=reviewer,
            reviewed_at=when,
            findings_json="[]",
        )
    )
    rev = b.variants.get_revision(v, "c1")
    assert rev is not None
    assert (rev.origin, rev.model, rev.reviewed_by, rev.findings_json) == (
        "ai",
        "m",
        reviewer,
        "[]",
    )
    assert rev.generation_date == rev.reviewed_at == when
    assert rev.state is RevisionState.PENDING_REVIEW
    assert b.variants.get_revision(v, "c9") is None
    with pytest.raises(ValueError):
        b.variants.add_revision(_revision(v, "c1"))
    with pytest.raises(ValueError):
        b.variants.add_revision(_revision(b.name("ghost"), "c1"))


# --- ReviewTaskRepository ------------------------------------------------------------------


def test_add_task_is_idempotent_per_open_target(b: _Backend) -> None:
    v = b.variant("v", b.parent())
    first = b.tasks.add(_task(b, "t1", v))
    again = b.tasks.add(_task(b, "t2", v))
    assert again == first
    assert b.tasks.get(b.name("t2")) is None
    other_version = b.tasks.add(
        _task(b, "t3", v, kind=TaskKind.EDITED_REVISION, revision_content_version="c2")
    )
    assert other_version.stable_id == b.name("t3")
    assert (
        b.tasks.add(_task(b, "t4", v, kind=TaskKind.EDITED_REVISION, revision_content_version="c2"))
        == other_version
    )
    with pytest.raises(ValueError):
        b.tasks.add(_task(b, "t1", b.variant("w", b.parent("q2"))))


def test_new_task_is_open_with_clock_time(b: _Backend) -> None:
    v = b.variant("v", b.parent())
    task = b.tasks.add(_task(b, "t", v, reason="verified"))
    assert task.state is TaskState.OPEN
    assert task.created_at == _T0
    assert (task.reason, task.reviewer_stable_id, task.decided_at) == ("verified", None, None)
    assert b.tasks.get(b.name("t")) == task


def _pending_with_revision(b: _Backend, label: str = "v") -> tuple[str, str]:
    v = b.variant(label, b.parent(f"{label}-q"), S.PENDING_REVIEW)
    b.variants.add_revision(_revision(v, "c1"))
    task = b.tasks.add(_task(b, f"{label}-t", v, revision_content_version="c1"))
    return v, task.stable_id


def test_decide_approve_writes_task_variant_and_revision_together(b: _Backend) -> None:
    v, task_id = _pending_with_revision(b)
    reviewer = b.reviewer()
    task, variant = b.tasks.get(task_id), b.variants.get(v)
    assert task is not None
    assert variant is not None
    when = _T0 + timedelta(minutes=5)
    b.tasks.decide(
        task,
        Decision(
            DecisionOutcome.APPROVE,
            reviewer,
            when,
            comment="ok",
            variant=replace(variant, state=S.APPROVED, approved_content_version="c1"),
            revision_state=RevisionState.APPROVED,
        ),
    )
    decided = b.tasks.get(task_id)
    assert decided is not None
    assert (decided.state, decided.reviewer_stable_id, decided.decided_at, decided.comment) == (
        TaskState.APPROVED,
        reviewer,
        when,
        "ok",
    )
    stored = b.variants.get(v)
    assert stored is not None
    assert (stored.state, stored.approved_content_version) == (S.APPROVED, "c1")
    rev = b.variants.get_revision(v, "c1")
    assert rev is not None
    assert (rev.state, rev.reviewed_by, rev.reviewed_at) == (RevisionState.APPROVED, reviewer, when)


def test_decide_reject_and_request_changes(b: _Backend) -> None:
    v, task_id = _pending_with_revision(b, "r")
    task, variant = b.tasks.get(task_id), b.variants.get(v)
    assert task is not None
    assert variant is not None
    b.tasks.decide(
        task,
        Decision(
            DecisionOutcome.REJECT,
            b.reviewer(),
            _T0,
            variant=replace(variant, state=S.REJECTED),
            revision_state=RevisionState.REJECTED,
        ),
    )
    assert b.variants.get(v).state is S.REJECTED  # type: ignore[union-attr]
    assert b.tasks.get(task_id).state is TaskState.REJECTED  # type: ignore[union-attr]

    v2, task2 = _pending_with_revision(b, "c")
    t2 = b.tasks.get(task2)
    assert t2 is not None
    b.tasks.decide(t2, Decision(DecisionOutcome.REQUEST_CHANGES, b.reviewer("rev2"), _T0, "fix"))
    assert b.variants.get(v2).state is S.PENDING_REVIEW  # type: ignore[union-attr]
    changed = b.tasks.get(task2)
    assert changed is not None
    assert (changed.state, changed.comment) == (TaskState.CHANGES_REQUESTED, "fix")


def test_decide_edit_of_served_variant_keeps_state_until_approved(b: _Backend) -> None:
    parent = b.parent()
    v = b.variant("v", parent, S.SERVED)
    b.variants.add_revision(_revision(v, "c1", state=RevisionState.APPROVED))
    served = b.variants.get(v)
    assert served is not None
    b.variants.save(replace(served, approved_content_version="c1"))
    b.variants.add_revision(_revision(v, "c2"))
    edit = b.tasks.add(
        _task(b, "t", v, kind=TaskKind.EDITED_REVISION, revision_content_version="c2")
    )
    reviewer = b.reviewer()
    # Rejecting the edit leaves the old revision served (Requirement 16.4).
    b.tasks.decide(
        edit, Decision(DecisionOutcome.REJECT, reviewer, _T0, revision_state=RevisionState.REJECTED)
    )
    assert [r.content_version for r in b.variants.list_servable(parent)] == ["c1"]
    # Approving a second edit switches the served revision (Requirement 16.3).
    b.variants.add_revision(_revision(v, "c3"))
    edit2 = b.tasks.add(
        _task(b, "t2", v, kind=TaskKind.EDITED_REVISION, revision_content_version="c3")
    )
    current = b.variants.get(v)
    assert current is not None
    b.tasks.decide(
        edit2,
        Decision(
            DecisionOutcome.APPROVE,
            reviewer,
            _T0,
            variant=replace(current, approved_content_version="c3"),
            revision_state=RevisionState.APPROVED,
        ),
    )
    assert [r.content_version for r in b.variants.list_servable(parent)] == ["c3"]
    final = b.variants.get(v)
    assert final is not None
    assert final.state is S.SERVED


def test_decide_is_atomic_when_the_variant_move_is_illegal(b: _Backend) -> None:
    v, task_id = _pending_with_revision(b)
    task, variant = b.tasks.get(task_id), b.variants.get(v)
    assert task is not None
    assert variant is not None
    bad = replace(variant, state=S.SERVED, approved_content_version="c1")
    with pytest.raises(InvalidTransitionError):
        b.tasks.decide(
            task,
            Decision(
                DecisionOutcome.APPROVE,
                b.reviewer(),
                _T0,
                variant=bad,
                revision_state=RevisionState.APPROVED,
            ),
        )
    assert b.tasks.get(task_id) == task
    assert b.variants.get(v) == variant
    rev = b.variants.get_revision(v, "c1")
    assert rev is not None
    assert (rev.state, rev.reviewed_by) == (RevisionState.PENDING_REVIEW, None)


def test_decide_is_atomic_when_the_revision_is_unknown(b: _Backend) -> None:
    v = b.variant("v", b.parent())
    task = b.tasks.add(_task(b, "t", v, revision_content_version="nope"))
    variant = b.variants.get(v)
    assert variant is not None
    with pytest.raises(ValueError):
        b.tasks.decide(
            task,
            Decision(
                DecisionOutcome.APPROVE,
                b.reviewer(),
                _T0,
                variant=replace(variant, state=S.APPROVED, approved_content_version="nope"),
                revision_state=RevisionState.APPROVED,
            ),
        )
    assert b.variants.get(v) == variant
    assert b.tasks.get(task.stable_id) == task


def test_decide_twice_raises_already_decided(b: _Backend) -> None:
    _, task_id = _pending_with_revision(b)
    task = b.tasks.get(task_id)
    assert task is not None
    decision = Decision(DecisionOutcome.REQUEST_CHANGES, b.reviewer(), _T0, "x")
    b.tasks.decide(task, decision)
    with pytest.raises(ReviewTaskAlreadyDecidedError):
        b.tasks.decide(task, decision)
    with pytest.raises(ValueError):
        b.tasks.decide(replace(task, stable_id=b.name("ghost")), decision)


def test_a_decided_target_can_be_queued_again(b: _Backend) -> None:
    v, task_id = _pending_with_revision(b)
    task = b.tasks.get(task_id)
    assert task is not None
    b.tasks.decide(task, Decision(DecisionOutcome.REQUEST_CHANGES, b.reviewer(), _T0, "x"))
    again = b.tasks.add(_task(b, "again", v, revision_content_version="c1"))
    assert again.stable_id == b.name("again")


def test_page_lists_open_tasks_of_the_tracks_by_keyset(b: _Backend) -> None:
    ids: list[str] = []
    for i in range(5):
        b.clock.advance(timedelta(minutes=1))
        v = b.variant(f"v{i}", b.parent(f"q{i}"))
        ids.append(b.tasks.add(_task(b, f"t{i}", v)).stable_id)
    other = b.variant("vx", b.parent("qx"))
    b.tasks.add(_task(b, "tx", other, track_stable_id=b.name("other-trk")))
    decided = b.tasks.get(ids[1])
    assert decided is not None
    b.tasks.decide(decided, Decision(DecisionOutcome.REQUEST_CHANGES, b.reviewer(), _T0, "x"))

    tracks = [b.name("trk")]
    first = b.tasks.page(tracks, None, 2)
    assert [t.stable_id for t in first.items] == [ids[0], ids[2]]
    assert first.next_after is not None
    second = b.tasks.page(tracks, first.next_after, 2)
    assert [t.stable_id for t in second.items] == [ids[3], ids[4]]
    assert second.next_after is None
    assert b.tasks.page([], None, 5).items == ()
    both = b.tasks.page([*tracks, b.name("other-trk")], None, 10)
    assert len(both.items) == 5
    with pytest.raises(ValueError):
        b.tasks.page(tracks, None, 0)


def test_page_breaks_ties_on_stable_id(b: _Backend) -> None:
    for label in ("c", "a", "b"):  # same created_at for all three
        b.tasks.add(_task(b, label, b.variant(f"v{label}", b.parent(f"q{label}"))))
    page = b.tasks.page([b.name("trk")], None, 2)
    assert [t.stable_id for t in page.items] == [b.name("a"), b.name("b")]
    assert page.next_after is not None
    rest = b.tasks.page([b.name("trk")], page.next_after, 2)
    assert [t.stable_id for t in rest.items] == [b.name("c")]


def test_page_ties_use_code_point_order_not_locale_collation(b: _Backend) -> None:
    # A locale collation (PostgreSQL en_US.utf8) ignores case and punctuation first,
    # giving a < B < _x < y10 < y2; the port's code-point order (Python sorted())
    # is B < _x < a < y10 < y2.
    labels = ("a", "B", "_x", "y2", "y10")
    for label in labels:  # same created_at for all
        b.tasks.add(_task(b, label, b.variant(f"v{label}", b.parent(f"q{label}"))))
    seen: list[str] = []
    after = None
    while True:
        page = b.tasks.page([b.name("trk")], after, 1)
        seen.extend(t.stable_id for t in page.items)
        if page.next_after is None:
            break
        after = page.next_after
    assert seen == sorted(b.name(label) for label in labels)


# --- SQL-only reference checks -------------------------------------------------------------


def test_sql_rejects_unknown_parent_job_and_reviewer() -> None:
    engine = _sqlite_engine()
    try:
        b = _sql_backend(engine)
        with pytest.raises(ValueError):
            b.variants.add(NewVariant(b.name("v"), b.name("no-parent"), b.name("trk")))
        parent = b.parent()
        with pytest.raises(ValueError):
            b.variants.add(
                NewVariant(b.name("v"), parent, b.name("trk"), generation_job_stable_id="nope")
            )
        assert b.variants.get(b.name("v")) is None
        v = b.variant("v", parent)
        with pytest.raises(ValueError):
            b.variants.add_revision(_revision(v, "c1", reviewed_by=b.name("no-user")))
        assert b.variants.get_revision(v, "c1") is None
        b.variants.add_revision(_revision(v, "c1"))
        task = b.tasks.add(_task(b, "t", v, revision_content_version="c1"))
        with pytest.raises(ValueError):
            b.tasks.decide(
                task,
                Decision(
                    DecisionOutcome.REQUEST_CHANGES,
                    b.name("no-user"),
                    _T0,
                    revision_state=RevisionState.REJECTED,
                ),
            )
        assert b.tasks.get(task.stable_id) == task
    finally:
        engine.dispose()


# --- Property ------------------------------------------------------------------------------


@settings(max_examples=25, deadline=None)
@given(states=st.lists(st.sampled_from(list(VariantState)), max_size=8))
def test_property_servable_listing_is_a_subset_of_approved_and_served(
    states: list[VariantState],
) -> None:
    """Only Approved or Served variants are counted and listed.

    **Validates: Requirements 13.3, 15.4**
    """
    engine = _sqlite_engine()
    try:
        for b in (_fake_backend(), _sql_backend(engine)):
            parent = b.parent()
            for i, state in enumerate(states):
                v = b.variant(f"v{i}", parent, state)
                b.variants.add_revision(_revision(v, "c1", state=RevisionState.APPROVED))
                stored = b.variants.get(v)
                assert stored is not None
                b.variants.save(replace(stored, approved_content_version="c1"))
            expected = sum(1 for s in states if s in SERVABLE_STATES)
            assert b.variants.count_approved(parent) == expected
            listed = b.variants.list_servable(parent)
            assert len(listed) == expected
            for revision in listed:
                variant = b.variants.get(revision.variant_stable_id)
                assert variant is not None
                assert variant.state in SERVABLE_STATES
    finally:
        engine.dispose()
