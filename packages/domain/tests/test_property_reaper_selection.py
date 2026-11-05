"""Property 12: Reaper selection.

Feature: kubernetes-deployment, Property 12: Reaper selection

# Claims: 6.6, 6.7, 19.5, 20.3, 20.7

**Validates: Requirements 6.6, 6.7, 19.5, 20.3, 20.7**
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from edu_it_domain.config import LAB_IDLE_TIMEOUT
from edu_it_domain.labs.k8s_rules import select_reaper_actions
from edu_it_domain.labs.models import LabSession, SessionStatus
from edu_it_domain.labs.session_rules import ReapAction
from edu_it_domain.ports.sandbox import SandboxHandle
from hypothesis import given, settings
from hypothesis import strategies as st

NOW = datetime(2025, 1, 1, 12, 0, tzinfo=UTC)
_IDLE = LAB_IDLE_TIMEOUT

_offsets = st.integers(min_value=-7200, max_value=7200).map(lambda s: timedelta(seconds=s))
_ids = st.text(alphabet="abcdefghij", min_size=1, max_size=3)


@st.composite
def _scenarios(
    draw: st.DrawFn,
) -> tuple[list[LabSession], list[SandboxHandle], frozenset[str]]:
    session_ids = draw(st.lists(_ids, unique=True, max_size=8))
    sessions = [
        LabSession(
            stable_id=f"s-{sid}",
            user_id="u",
            lab_id="l",
            status=draw(st.sampled_from(list(SessionStatus))),
            created_at=NOW - timedelta(hours=3),
            ttl_at=NOW + draw(_offsets),
            sandbox_ref="p",
            last_activity_at=NOW - abs(draw(_offsets)) % (2 * _IDLE),
        )
        for sid in session_ids
    ]
    orphan_ids = draw(st.lists(_ids, unique=True, max_size=4))
    pod_session_ids = [s.stable_id for s in sessions if draw(st.booleans())] + [
        f"o-{oid}" for oid in orphan_ids
    ]
    pods = [
        SandboxHandle(sandbox_id=f"pod-{sid}", session_stable_id=sid, provider="kubernetes")
        for sid in pod_session_ids
    ]
    failed = frozenset(p.sandbox_id for p in pods if draw(st.booleans()))
    return sessions, pods, failed


def _live(session: LabSession) -> bool:
    return (
        session.status.is_active
        and session.ttl_at >= NOW
        and ((session.last_activity_at or session.created_at) + _IDLE > NOW)
    )


@settings(max_examples=100, deadline=None)
@given(scenario=_scenarios())
def test_live_session_pods_are_never_selected_and_all_others_are(
    scenario: tuple[list[LabSession], list[SandboxHandle], frozenset[str]],
) -> None:
    sessions, pods, failed = scenario
    actions = select_reaper_actions(sessions, pods, NOW, failed_pod_ids=failed)
    live_ids = {s.stable_id for s in sessions if _live(s)}
    selected = {p.sandbox_id for p in actions.delete_pods}

    assert selected == {p.sandbox_id for p in pods if p.session_stable_id not in live_ids}
    assert not any(p.session_stable_id in live_ids for p in actions.delete_pods)


@settings(max_examples=100, deadline=None)
@given(scenario=_scenarios())
def test_expired_sessions_are_expired_and_lost_sessions_are_live(
    scenario: tuple[list[LabSession], list[SandboxHandle], frozenset[str]],
) -> None:
    sessions, pods, failed = scenario
    actions = select_reaper_actions(sessions, pods, NOW, failed_pod_ids=failed)
    by_id = {s.stable_id: s for s in sessions}

    expired = {sid for sid, _ in actions.expire_sessions}
    assert expired == {s.stable_id for s in sessions if s.status.is_active and not _live(s)}
    assert all(a in (ReapAction.EXPIRE, ReapAction.IDLE_EXPIRE) for _, a in actions.expire_sessions)
    assert all(by_id[sid].status.is_active and _live(by_id[sid]) for sid in actions.lost_sessions)


@settings(max_examples=100, deadline=None)
@given(scenario=_scenarios(), seed=st.randoms(use_true_random=False))
def test_selection_is_deterministic_stateless_and_order_independent(
    scenario: tuple[list[LabSession], list[SandboxHandle], frozenset[str]],
    seed: object,
) -> None:
    sessions, pods, failed = scenario
    first = select_reaper_actions(sessions, pods, NOW, failed_pod_ids=failed)
    # An unrelated earlier call with other inputs leaves no trace.
    select_reaper_actions([], pods, NOW + timedelta(days=1))
    again = select_reaper_actions(sessions, pods, NOW, failed_pod_ids=failed)
    assert again == first

    shuffled_sessions, shuffled_pods = list(sessions), list(pods)
    seed.shuffle(shuffled_sessions)  # type: ignore[attr-defined]
    seed.shuffle(shuffled_pods)  # type: ignore[attr-defined]
    assert (
        select_reaper_actions(shuffled_sessions, shuffled_pods, NOW, failed_pod_ids=failed) == first
    )
