"""Property test for VerifyLab sandbox cleanup and bounds (ai-content task 7.7).

Feature: ai-content, Property 19: Verification sandboxes are destroyed together and within bounds

For any sequence of exec outcomes (clean exits, failures, timeouts, provider errors) and any
provisioning outcome, after ``VerifyLab`` returns or raises the fake provider lists no
sandbox, no sandbox is destroyed before every exec finished or failed, and every exec
timeout is at most ``min(check timeout, USABLE_RUN_BUDGET)``.

**Validates: Requirements 8.5, 8.6, 8.7**
"""

from __future__ import annotations

import contextlib
from collections.abc import Sequence
from datetime import timedelta
from typing import Any

from edu_it_application.ai import VerifyLab
from edu_it_domain import config
from edu_it_domain.ai.verification import LabCheckUnderVerification, LabUnderVerification
from edu_it_domain.ports.sandbox import ExecResult, SandboxGone
from edu_it_infrastructure.fakes import Fake_SandboxProvider, Test_Clock
from edu_it_infrastructure.fakes.sandbox_provider import FakeSandbox, ProvisionMode
from hypothesis import given, settings
from hypothesis import strategies as st

_USABLE_S = config.USABLE_RUN_BUDGET.total_seconds()
_PROVISION_MODES = st.sampled_from(list(ProvisionMode))

# An exec outcome: a result, or an exception raised by the provider.
_OUTCOME = st.one_of(
    st.builds(
        lambda code, timed_out: ExecResult(code, b"", b"", timed_out),
        st.sampled_from([0, 1, 2, 126, 127, 137, 143, -1]),
        st.booleans(),
    ),
    st.just(SandboxGone("gone")),
    st.just(RuntimeError("provider error")),
)

_CHECK_TIMEOUT = st.floats(min_value=0.1, max_value=3600.0, allow_nan=False)


@settings(max_examples=200, deadline=None)
@given(
    modes=st.lists(_PROVISION_MODES, min_size=2, max_size=2),
    outcomes=st.lists(_OUTCOME, min_size=0, max_size=12),
    timeouts=st.lists(_CHECK_TIMEOUT, min_size=1, max_size=4),
    with_setup=st.booleans(),
    call_seconds=st.sampled_from([0, 1, 30, 300, 1200]),
)
def test_sandboxes_destroyed_together_and_exec_within_bounds(
    modes: list[ProvisionMode],
    outcomes: list[Any],
    timeouts: list[float],
    with_setup: bool,
    call_seconds: int,
) -> None:
    clock = Test_Clock()
    sandbox = Fake_SandboxProvider(clock, call_duration=timedelta(seconds=call_seconds))
    sandbox.script_provision(*modes)

    queue = list(outcomes)

    def handler(sb: FakeSandbox, command: Sequence[str], user: str) -> ExecResult | None:
        if not queue:
            return None
        outcome = queue.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        assert isinstance(outcome, ExecResult)
        return outcome

    sandbox.exec_handler = handler

    events: list[tuple[str, str, float | None]] = []
    real_exec, real_destroy = sandbox.exec, sandbox.destroy

    def spy_exec(handle, command, **kwargs):  # type: ignore[no-untyped-def]
        events.append(("exec", command[2], kwargs["timeout_s"]))
        return real_exec(handle, command, **kwargs)

    def spy_destroy(handle):  # type: ignore[no-untyped-def]
        events.append(("destroy", handle.sandbox_id, None))
        return real_destroy(handle)

    sandbox.exec = spy_exec  # type: ignore[method-assign]
    sandbox.destroy = spy_destroy  # type: ignore[method-assign]

    checks = tuple(
        LabCheckUnderVerification(f"c{i}", f"CHECK:c{i}", t) for i, t in enumerate(timeouts)
    )
    lab = LabUnderVerification(
        lab_id="lab1",
        image="img",
        checks=checks,
        reference_solution="SOLVE",
        setup_script="SETUP" if with_setup else None,
    )

    with contextlib.suppress(Exception):  # raising is allowed; the invariants must hold
        VerifyLab(sandbox=sandbox, clock=clock).execute(lab)

    # 8.5: nothing managed is left behind.
    assert sandbox.list_sandboxes() == ()

    # 8.6: no destroy happens before the last exec finished or failed.
    kinds = [kind for kind, _, _ in events]
    if "destroy" in kinds:
        first_destroy = kinds.index("destroy")
        assert "exec" not in kinds[first_destroy:]

    # 8.7: every exec is bounded by min(check timeout, USABLE_RUN_BUDGET).
    by_script = {c.script: c.timeout_s for c in checks}
    for kind, script, timeout in events:
        if kind != "exec":
            continue
        assert timeout is not None
        assert timeout <= _USABLE_S
        if script in by_script:
            assert timeout <= min(by_script[script], _USABLE_S)
