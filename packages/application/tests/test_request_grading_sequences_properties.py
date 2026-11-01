"""Property 7: grading request sequences stay throttled and idempotent.

Feature: hands-on-labs, Property 7: Grading request sequences stay throttled and idempotent

**Validates: Requirements 17.4**
"""

from __future__ import annotations

from datetime import timedelta
from itertools import pairwise

from edu_it_application.labs import GradingRejectedError, RequestGradingUseCase
from edu_it_domain import config
from edu_it_domain.labs.models import LabSession, SandboxSpec, SessionStatus
from edu_it_domain.labs.session_rules import RejectionReason
from edu_it_infrastructure.fakes import (
    Fake_SandboxProvider,
    In_Memory_GradingRunRepository,
    In_Memory_IdempotencyStore,
    In_Memory_LabSessionRepository,
    In_Memory_TaskQueue,
    Sequential_IdGenerator,
    Test_Clock,
)
from hypothesis import settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule

_USER = "u1"
_SID = "s1"
_MIN = config.LAB_GRADING_MIN_INTERVAL


class GradingSequenceMachine(RuleBasedStateMachine):
    """Random interleavings of grading requests, replays, worker completions, and time."""

    def __init__(self) -> None:
        super().__init__()
        self.clock = Test_Clock()
        self.runs = In_Memory_GradingRunRepository()
        self.sessions = In_Memory_LabSessionRepository(self.runs)
        self.queue = In_Memory_TaskQueue()
        sandbox = Fake_SandboxProvider(self.clock)
        self.use_case = RequestGradingUseCase(
            sessions=self.sessions,
            runs=self.runs,
            queue=self.queue,
            idempotency=In_Memory_IdempotencyStore(),
            clock=self.clock,
            ids=Sequential_IdGenerator("r"),
        )
        now = self.clock.now()
        handle = sandbox.provision(
            SandboxSpec(_SID, "img", "learner", False, 1, 1, 1, 1, True, True, True, (), ())
        )
        self.sessions.add_if_no_active(
            LabSession(
                stable_id=_SID,
                user_id=_USER,
                lab_id="lab1",
                status=SessionStatus.READY,
                created_at=now,
                ttl_at=now + config.LAB_SESSION_TTL,
                sandbox_ref=handle.sandbox_id,
            )
        )
        self.counter = 0
        self.accepted: dict[str, object] = {}  # key -> run
        self.accepted_times: list = []

    @rule(seconds=st.integers(min_value=0, max_value=int(_MIN.total_seconds()) * 3))
    def advance(self, seconds: int) -> None:
        self.clock.advance(timedelta(seconds=seconds))

    @rule()
    def worker_finishes(self) -> None:
        self.sessions.transition(_SID, expected={SessionStatus.GRADING}, to=SessionStatus.READY)

    @rule()
    def request_new_key(self) -> None:
        self.counter += 1
        key = f"k{self.counter}"
        session = self.sessions.get(_SID)
        assert session is not None
        was_grading = session.status is SessionStatus.GRADING
        last = session.last_grading_accepted_at
        now = self.clock.now()
        try:
            result = self.use_case.execute(_USER, _SID, key)
        except GradingRejectedError as exc:
            if was_grading:
                assert exc.reason is RejectionReason.IN_PROGRESS
            elif last is not None and now - last <= _MIN:
                assert exc.reason is RejectionReason.THROTTLED
            return
        # Accepted: never while grading, never inside the minimum interval.
        assert not was_grading
        assert last is None or now - last > _MIN
        assert not result.replayed
        self.accepted[key] = result.run
        self.accepted_times.append(result.run.accepted_at)

    @rule(data=st.data())
    def repeat_accepted_key(self, data: st.DataObject) -> None:
        if not self.accepted:
            return
        key = data.draw(st.sampled_from(sorted(self.accepted)))
        jobs_before = len(self.queue._pending)
        runs_before = len(self.accepted)
        result = self.use_case.execute(_USER, _SID, key)
        assert result.replayed
        assert result.run == self.accepted[key]
        assert len(self.queue._pending) == jobs_before
        assert len(self.accepted) == runs_before

    @invariant()
    def accepted_runs_are_spaced_and_each_enqueued_once(self) -> None:
        times = self.accepted_times
        for earlier, later in pairwise(times):
            assert later - earlier > _MIN
        grade_jobs = [j for j in self.queue._pending if j.job_name == "lab.grade"]
        assert len(grade_jobs) == len(self.accepted)
        assert len({j.dedupe_key for j in grade_jobs}) == len(grade_jobs)


GradingSequenceMachine.TestCase.settings = settings(
    max_examples=100, stateful_step_count=30, deadline=None
)
TestGradingSequences = GradingSequenceMachine.TestCase
TestGradingSequences.__doc__ = (
    "Feature: hands-on-labs, Property 7: Grading request sequences stay throttled and idempotent"
)
