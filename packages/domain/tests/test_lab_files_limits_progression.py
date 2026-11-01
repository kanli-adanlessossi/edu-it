"""Tests for labs files, limits and progression (hands-on-labs task 1.7)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from edu_it_domain import config
from edu_it_domain.labs.files import FileLimits, FileViolation, validate_learner_files
from edu_it_domain.labs.limits import (
    HeartbeatTracker,
    MessageLimiter,
    MessageVerdict,
    TokenBucket,
)
from edu_it_domain.labs.models import CheckOutcome, CheckResult
from edu_it_domain.labs.progression import LabProgression, apply_lab_results
from edu_it_domain.learning.models import Mastery, MasteryState
from edu_it_domain.ports.lab_catalog import (
    LabCheckDefinition,
    LabDefinition,
    LabTaskDefinition,
)
from hypothesis import given
from hypothesis import strategies as st


class _Clock:
    def __init__(self) -> None:
        self.t = datetime(2025, 1, 1, tzinfo=UTC)

    def now(self) -> datetime:
        return self.t

    def advance(self, **kw: float) -> None:
        self.t += timedelta(**kw)


# --- files ---------------------------------------------------------------------


def test_files_ok_and_caps() -> None:
    assert validate_learner_files({"a.py": "x"}, ["a.py"]).ok
    big = "x" * (config.LAB_FILE_MAX_BYTES + 1)
    assert validate_learner_files({"a.py": big}, ["a.py"]).violation is FileViolation.FILE_TOO_LARGE
    ok_size = "x" * config.LAB_FILE_MAX_BYTES
    assert validate_learner_files({"a.py": ok_size}, ["a.py"]).ok
    many = {f"f{i}": "" for i in range(config.LAB_FILE_MAX_COUNT + 1)}
    assert validate_learner_files(many, list(many)).violation is FileViolation.TOO_MANY_FILES


def test_files_size_counts_utf8_bytes() -> None:
    limits = FileLimits(max_count=1, max_bytes=3)
    assert validate_learner_files({"a": "é"}, ["a"], limits).ok
    assert not validate_learner_files({"a": "éé"}, ["a"], limits).ok


@given(st.sampled_from(["../x", "a/b", "a\\b", "..", ".", "", "/etc/passwd", "a\x00"]))
def test_unsafe_names_rejected_even_if_allowed(name: str) -> None:
    result = validate_learner_files({name: "x"}, [name])
    assert result.violation is FileViolation.UNSAFE_NAME


def test_unlisted_name_rejected() -> None:
    result = validate_learner_files({"b.py": "x"}, ["a.py"])
    assert result.violation is FileViolation.NAME_NOT_ALLOWED
    assert result.name == "b.py"


# --- limits --------------------------------------------------------------------


def test_token_bucket_refills_over_clock() -> None:
    clock = _Clock()
    bucket = TokenBucket(clock, capacity=2, rate_per_second=1)
    assert bucket.try_consume()
    assert bucket.try_consume()
    assert not bucket.try_consume()
    clock.advance(seconds=1)
    assert bucket.try_consume()
    assert not bucket.try_consume()
    clock.advance(seconds=100)
    assert bucket.try_consume(2)
    assert not bucket.try_consume()


@given(st.integers(min_value=1, max_value=50), st.integers(min_value=0, max_value=200))
def test_token_bucket_never_exceeds_capacity(capacity: int, attempts: int) -> None:
    bucket = TokenBucket(_Clock(), capacity=capacity, rate_per_second=1)
    allowed = sum(bucket.try_consume() for _ in range(attempts))
    assert allowed == min(capacity, attempts)


def test_message_limiter_codes() -> None:
    clock = _Clock()
    limiter = MessageLimiter(clock, max_bytes=10, messages_per_second=2)
    assert limiter.check(11) is MessageVerdict.TOO_BIG
    assert limiter.check(1) is MessageVerdict.OK
    assert limiter.check(1) is MessageVerdict.OK
    assert limiter.check(1) is MessageVerdict.RATE_EXCEEDED
    assert MessageVerdict.TOO_BIG.value == 1009
    assert MessageVerdict.RATE_EXCEEDED.value == 1008


def test_heartbeat_closes_after_two_missed_pongs() -> None:
    clock = _Clock()
    hb = HeartbeatTracker(clock)
    assert not hb.ping_due()
    clock.advance(seconds=20)
    assert hb.ping_due()
    hb.record_ping()
    hb.record_ping()  # first ping unanswered -> 1 missed
    assert hb.missed == 1
    assert not hb.should_close
    hb.record_ping()  # 2 missed
    assert hb.should_close


def test_heartbeat_pong_resets() -> None:
    hb = HeartbeatTracker(_Clock())
    hb.record_ping()
    hb.record_ping()
    hb.record_pong()
    assert hb.missed == 0
    hb.record_ping()
    assert not hb.should_close


# --- progression ---------------------------------------------------------------


def _lab() -> LabDefinition:
    return LabDefinition(
        lab_id="lab1",
        kind="terminal",
        image="img",
        concept_ids=("c1", "c2"),
        tasks=(
            LabTaskDefinition("t1", required=True),
            LabTaskDefinition("t2", required=True),
            LabTaskDefinition("t3", required=False),
        ),
        checks=(
            LabCheckDefinition("k1", "t1", 5),
            LabCheckDefinition("k2", "t2", 5),
            LabCheckDefinition("k3", "t3", 5),
        ),
    )


def _res(check: str, task: str, outcome: CheckOutcome) -> CheckResult:
    return CheckResult(check, task, outcome, "code", "fb")


P, F, X = CheckOutcome.PASSED, CheckOutcome.FAILED, CheckOutcome.COULD_NOT_COMPLETE


def _run(
    results: list[CheckResult],
    done: tuple[str, ...] = (),
    hinted: tuple[str, ...] = (),
    mastery: dict[str, Mastery] | None = None,
) -> LabProgression:
    return apply_lab_results(
        lab=_lab(),
        user_id="u",
        run_id="r",
        results=results,
        completed_task_ids=done,
        hinted_task_ids=hinted,
        mastery=mastery or {},
        clock=_Clock(),
    )


def test_first_pass_writes_completion_and_event_per_concept() -> None:
    out = _run([_res("k1", "t1", P), _res("k2", "t2", F)])
    assert [c.task_id for c in out.completions] == ["t1"]
    assert out.completions[0].counted_for_mastery
    assert [m.concept_id for m in out.mastery] == ["c1", "c2"]
    for m in out.mastery:
        assert m.score == config.LAB_TASK_MASTERY_GAIN
        assert [(e.source, e.delta) for e in m.events] == [("lab", config.LAB_TASK_MASTERY_GAIN)]
    assert not out.lab_passed


def test_hint_multiplier_applies() -> None:
    out = _run([_res("k1", "t1", P)], hinted=("t1",))
    assert out.mastery[0].score == int(config.LAB_TASK_MASTERY_GAIN * config.HINT_MULTIPLIER)


def test_repeat_pass_is_uncounted_and_changes_no_mastery() -> None:
    out = _run([_res("k1", "t1", P)], done=("t1",))
    assert len(out.completions) == 1
    assert not out.completions[0].counted_for_mastery
    assert out.mastery == ()


def test_failures_change_nothing() -> None:
    out = _run([_res("k1", "t1", F), _res("k2", "t2", X)])
    assert out.completions == ()
    assert out.mastery == ()


def test_lab_passed_marker_on_last_required_only() -> None:
    out = _run([_res("k2", "t2", P)], done=("t1",))
    assert out.lab_passed
    for m in out.mastery:
        assert [e.source for e in m.events] == ["lab", "lab_passed"]
        assert m.events[-1].delta == 0
    # Optional task completion never triggers the marker; repeat doesn't either.
    assert not _run([_res("k3", "t3", P)], done=("t1", "t2")).lab_passed
    assert not _run([_res("k2", "t2", P)], done=("t1", "t2")).lab_passed


def test_mastered_state_not_downgraded_and_existing_score_kept() -> None:
    existing = Mastery("u", "c1", 90, MasteryState.MASTERED)
    out = _run([_res("k1", "t1", P)], mastery={"c1": existing})
    c1 = next(m for m in out.mastery if m.concept_id == "c1")
    assert c1.score == 100
    assert c1.state is MasteryState.MASTERED


@given(
    st.lists(st.sampled_from([P, F, X]), min_size=3, max_size=3),
    st.sets(st.sampled_from(["t1", "t2", "t3"])),
    st.integers(min_value=0, max_value=100),
)
def test_never_negative_deltas_and_score_never_drops(
    outcomes: list[CheckOutcome], done: set[str], score: int
) -> None:
    results = [_res(f"k{i + 1}", f"t{i + 1}", o) for i, o in enumerate(outcomes)]
    start = Mastery("u", "c1", score, MasteryState.PRACTICED)
    out = _run(results, done=tuple(done), mastery={"c1": start})
    for m in out.mastery:
        assert all(e.delta >= 0 for e in m.events)
        if m.concept_id == "c1":
            assert m.score >= score
