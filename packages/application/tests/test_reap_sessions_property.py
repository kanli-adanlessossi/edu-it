"""Property test for the reaper (task 6.14).

Feature: hands-on-labs, Property 5: Reaper leaves no resources behind and respects deferral

A Hypothesis rule-based state machine builds arbitrary session / run / sandbox worlds on
the fakes, advances the Test_Clock, runs ``ReapSessions`` and checks the invariants.

**Validates: Requirements 6.5, 7.3, 8.2, 8.3, 8.6, 13.1, 25.5, 27.2, 27.3**
"""

from __future__ import annotations

from datetime import timedelta

from edu_it_application.labs import ReapSessions
from edu_it_domain import config
from edu_it_domain.labs.models import GradingRun, LabSession, RunStatus, SessionStatus
from edu_it_domain.labs.sandbox_spec import build_sandbox_spec
from edu_it_domain.ports.lab_catalog import LabDefinition
from edu_it_infrastructure.fakes import (
    Fake_SandboxProvider,
    In_Memory_GradingRunRepository,
    In_Memory_LabSessionRepository,
    Test_Clock,
)
from hypothesis import HealthCheck, settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule

_LAB = LabDefinition(
    lab_id="lab1", kind="terminal", image="img", concept_ids=(), tasks=(), checks=()
)
_DEADLINE = config.LAB_GRADING_RUN_DEADLINE
_ALL_STATUSES = list(SessionStatus)
_seconds = st.integers(min_value=0, max_value=int(config.LAB_SESSION_TTL.total_seconds()) * 2)


class ReaperMachine(RuleBasedStateMachine):
    """Sessions, runs and sandboxes evolve; every reap must leave a clean world."""

    def __init__(self) -> None:
        super().__init__()
        self.clock = Test_Clock()
        self.runs = In_Memory_GradingRunRepository()
        self.sessions = In_Memory_LabSessionRepository(self.runs)
        self.sandbox = Fake_SandboxProvider(self.clock)
        self.reaper = ReapSessions(
            sessions=self.sessions,
            runs=self.runs,
            sandbox=self.sandbox,
            clock=self.clock,
            provider_name="fake",
        )
        self.n = 0
        self.session_ids: list[str] = []
        self.run_ids: list[str] = []
        self.sandbox_ids: set[str] = set()  # every handle ever provisioned
        self.handles: dict[str, object] = {}
        self.reaped = False

    # -- world building ------------------------------------------------------

    def _provision(self, sid: str):
        handle = self.sandbox.provision(build_sandbox_spec(_LAB, sid))
        self.sandbox_ids.add(handle.sandbox_id)
        return handle

    @rule(
        status=st.sampled_from(_ALL_STATUSES),
        ttl_s=st.integers(min_value=1, max_value=int(config.LAB_SESSION_TTL.total_seconds())),
        has_sandbox=st.booleans(),
        active_ago_s=st.one_of(st.none(), st.integers(min_value=0, max_value=1200)),
    )
    def add_session(
        self, status: SessionStatus, ttl_s: int, has_sandbox: bool, active_ago_s: int | None
    ) -> None:
        self.n += 1
        sid = f"s{self.n}"
        now = self.clock.now()
        ref = self._provision(sid).sandbox_id if has_sandbox else None
        last = None if active_ago_s is None else now - timedelta(seconds=active_ago_s)
        self.sessions.add_if_no_active(
            LabSession(
                stable_id=sid,
                user_id=f"u{self.n}",
                lab_id="lab1",
                status=status,
                created_at=now,
                ttl_at=now + timedelta(seconds=ttl_s),
                sandbox_ref=ref,
                last_activity_at=last,
            )
        )
        self.session_ids.append(sid)

    @rule()
    def add_orphan_sandbox(self) -> None:
        self.n += 1
        self._provision(f"ghost{self.n}")

    @rule(data=st.data())
    def start_run(self, data: st.DataObject) -> None:
        ready = [
            s
            for s in map(self.sessions.get, self.session_ids)
            if s is not None and s.status is SessionStatus.READY and s.sandbox_ref
        ]
        if not ready:
            return
        s = data.draw(st.sampled_from(ready))
        now = self.clock.now()
        self.sessions.transition(
            s.stable_id, expected={SessionStatus.READY}, to=SessionStatus.GRADING
        )
        rid = f"r{len(self.run_ids) + 1}"
        self.runs.add(
            GradingRun(
                stable_id=rid,
                session_id=s.stable_id,
                status=RunStatus.RUNNING,
                accepted_at=now,
                deadline_at=now + _DEADLINE,
            )
        )
        self.run_ids.append(rid)

    @rule(data=st.data())
    def finish_run_in_time(self, data: st.DataObject) -> None:
        """A run that finishes first records normally (Req 27.3)."""
        now = self.clock.now()
        live = [r for r in self._running() if now <= r.deadline_at]
        if not live:
            return
        run = data.draw(st.sampled_from(live))
        assert self.runs.finish(run.stable_id, status=RunStatus.PASSED, graded_at=now)
        self.sessions.transition(
            run.session_id, expected={SessionStatus.GRADING}, to=SessionStatus.READY
        )

    @rule(data=st.data())
    def end_session(self, data: st.DataObject) -> None:
        active = [
            s
            for s in map(self.sessions.get, self.session_ids)
            if s is not None and s.status.is_active
        ]
        if not active:
            return
        s = data.draw(st.sampled_from(active))
        self.sessions.transition(
            s.stable_id,
            expected={s.status},
            to=data.draw(st.sampled_from([SessionStatus.ENDED, SessionStatus.FAILED])),
            ended_at=self.clock.now(),
        )

    @rule(data=st.data())
    def touch(self, data: st.DataObject) -> None:
        active = [s for s in map(self.sessions.get, self.session_ids) if s and s.status.is_active]
        if active:
            self.sessions.touch_activity(
                data.draw(st.sampled_from(active)).stable_id, self.clock.now()
            )

    @rule(seconds=_seconds)
    def advance(self, seconds: int) -> None:
        self.clock.advance(timedelta(seconds=seconds))

    @rule(micro=st.integers(min_value=0, max_value=2_000_000))
    def advance_around_deadline(self, micro: int) -> None:
        self.clock.advance(timedelta(microseconds=micro))

    # -- helpers -------------------------------------------------------------

    def _running(self) -> list[GradingRun]:
        return list(self.runs.list_running())

    def _live_sandboxes(self) -> dict[str, str]:
        """sandbox_id -> session_stable_id for every undestroyed sandbox."""
        return {h.sandbox_id: h.session_stable_id for h in self.sandbox.list_sandboxes()}

    def _snapshot(self) -> tuple:
        return (
            tuple(self.sessions.get(s) for s in self.session_ids),
            tuple(self.runs.get(r) for r in self.run_ids),
            frozenset(self._live_sandboxes()),
        )

    # -- the property --------------------------------------------------------

    @rule()
    def reap(self) -> None:
        now = self.clock.now()
        running_before = {r.session_id: r for r in self._running()}
        deferred_before = {sid for sid, r in running_before.items() if now <= r.deadline_at}
        live_before = self._live_sandboxes()
        sessions_before = {s: self.sessions.get(s) for s in self.session_ids}

        self.reaper.execute()

        live_after = self._live_sandboxes()
        # Reaping never creates sandboxes.
        assert set(live_after) <= set(live_before)

        # No running run is left past its deadline (thaw always succeeds on the fake).
        for r in self._running():
            assert now <= r.deadline_at

        # Deferral: sessions with a run inside its deadline are untouched mid-run.
        for sid in deferred_before:
            assert self.sessions.get(sid) == sessions_before[sid]
            for sb_id, owner in live_before.items():
                if owner == sid:
                    assert sb_id in live_after

        # No resources left behind.
        for sb_id, owner in live_after.items():
            session = self.sessions.get(owner)
            if owner in deferred_before:
                continue
            assert session is not None, "orphan sandbox survived"
            assert session.status.is_active, f"{session.status} session kept a sandbox"
            assert session.sandbox_ref == sb_id
            assert now <= session.ttl_at, "expired session kept a sandbox"
            baseline = session.last_activity_at or session.created_at
            assert now < baseline + config.LAB_IDLE_TIMEOUT, "idle session kept a sandbox"

        # Active sessions are never past their deadlines once reaped (unless deferred).
        for sid in self.session_ids:
            s = self.sessions.get(sid)
            if s is not None and s.status.is_active and sid not in deferred_before:
                assert now <= s.ttl_at
                assert now < (s.last_activity_at or s.created_at) + config.LAB_IDLE_TIMEOUT

        # Idempotent: a second run changes nothing and destroys nothing.
        snap = self._snapshot()
        calls = len(self.sandbox.calls)
        again = self.reaper.execute()
        assert self._snapshot() == snap
        assert "destroy" not in self.sandbox.calls[calls:]
        assert again.expired == again.idle_expired == again.destroyed == 0
        assert again.destroy_failures == again.runs_closed == 0
        self.reaped = True

    @invariant()
    def sandbox_handles_known(self) -> None:
        assert set(self._live_sandboxes()) <= self.sandbox_ids


TestReaperProperty = ReaperMachine.TestCase
TestReaperProperty.settings = settings(
    max_examples=100,
    stateful_step_count=30,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)
