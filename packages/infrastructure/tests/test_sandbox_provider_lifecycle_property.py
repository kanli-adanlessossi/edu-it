"""Property test: provider lifecycle model (hands-on-labs, task 2.2).

Feature: hands-on-labs, Property 2: Provider lifecycle model, destroy is idempotent

Evidence class: this exercises the ``Fake_SandboxProvider`` call logic only. It is
not security evidence.

**Validates: Requirements 1.2, 8.4**
"""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from edu_it_domain.labs.sandbox_spec import build_sandbox_spec
from edu_it_domain.ports.sandbox import ProvisionError, SandboxGone, SandboxHandle
from edu_it_infrastructure.fakes import Fake_SandboxProvider, ProvisionMode, Test_Clock
from hypothesis import settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, precondition, rule

_T0 = datetime(2024, 1, 1, tzinfo=UTC)


class _Lab:
    image = "linux-base"


class SandboxLifecycleMachine(RuleBasedStateMachine):
    """Model: ``issued`` handles are live until destroyed; ``destroyed`` never revives."""

    def __init__(self) -> None:
        super().__init__()
        self.clock = Test_Clock(start=_T0)
        self.provider = Fake_SandboxProvider(self.clock, call_duration=timedelta(seconds=1))
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.live: list[SandboxHandle] = []
        self.destroyed: list[SandboxHandle] = []
        self.counter = 0

    def teardown(self) -> None:
        self._tmp.cleanup()

    def _gone_ops(self, h: SandboxHandle) -> None:
        src = self.tmp / "src.txt"
        src.write_bytes(b"x")
        with pytest.raises(SandboxGone):
            self.provider.exec(h, ["true"], as_user="learner", env={}, cwd="/")
        with pytest.raises(SandboxGone):
            self.provider.copy_in(h, src, "/f.txt")
        with pytest.raises(SandboxGone):
            self.provider.copy_out(h, "/f.txt", self.tmp / "out.txt")
        assert not self.provider.health(h).healthy

    @rule(
        mode=st.sampled_from(
            [ProvisionMode.OK, ProvisionMode.OK, ProvisionMode.PARTIAL, ProvisionMode.FAIL]
        )
    )
    def provision(self, mode: ProvisionMode) -> None:
        self.counter += 1
        self.provider.script_provision(mode)
        spec = build_sandbox_spec(_Lab(), f"s{self.counter}")
        if mode is ProvisionMode.OK:
            h = self.provider.provision(spec)
            assert h not in self.live
            assert h not in self.destroyed
            self.live.append(h)
        elif mode is ProvisionMode.PARTIAL:
            with pytest.raises(ProvisionError) as exc:
                self.provider.provision(spec)
            # The partial handle is reported so the caller can destroy it.
            assert exc.value.partial_handle is not None
            self.live.append(exc.value.partial_handle)
        else:
            with pytest.raises(ProvisionError):
                self.provider.provision(spec)

    @precondition(lambda self: bool(self.live))
    @rule(i=st.integers(min_value=0, max_value=1000), name=st.text(min_size=1, max_size=8))
    def exec_live(self, i: int, name: str) -> None:
        h = self.live[i % len(self.live)]
        assert (
            self.provider.exec(h, ["echo", name], as_user="learner", env={}, cwd="/").exit_code == 0
        )

    @precondition(lambda self: bool(self.live))
    @rule(i=st.integers(min_value=0, max_value=1000), data=st.binary(max_size=32))
    def copy_round_trip(self, i: int, data: bytes) -> None:
        h = self.live[i % len(self.live)]
        src = self.tmp / "in.bin"
        src.write_bytes(data)
        self.provider.copy_in(h, src, "/work/f.bin")
        out = self.tmp / "out.bin"
        self.provider.copy_out(h, "/work/f.bin", out)
        assert out.read_bytes() == data

    @precondition(lambda self: bool(self.live))
    @rule(i=st.integers(min_value=0, max_value=1000))
    def health_live(self, i: int) -> None:
        assert self.provider.health(self.live[i % len(self.live)]).healthy

    @precondition(lambda self: bool(self.live))
    @rule(i=st.integers(min_value=0, max_value=1000))
    def destroy_live(self, i: int) -> None:
        h = self.live.pop(i % len(self.live))
        self.provider.destroy(h)
        self.destroyed.append(h)

    @precondition(lambda self: bool(self.destroyed))
    @rule(i=st.integers(min_value=0, max_value=1000))
    def destroy_again(self, i: int) -> None:
        h = self.destroyed[i % len(self.destroyed)]
        self.provider.destroy(h)  # idempotent: must not raise
        self.provider.destroy(h)

    @precondition(lambda self: bool(self.destroyed))
    @rule(i=st.integers(min_value=0, max_value=1000))
    def use_destroyed(self, i: int) -> None:
        self._gone_ops(self.destroyed[i % len(self.destroyed)])

    @rule()
    def list_all(self) -> None:
        listed = list(self.provider.list_sandboxes())
        assert set(listed) == set(self.live)
        assert not set(listed) & set(self.destroyed)

    @invariant()
    def every_issued_handle_is_listed_or_destroyed(self) -> None:
        listed = set(self.provider.list_sandboxes())
        for h in self.live:
            assert h in listed
        for h in self.destroyed:
            assert h not in listed
            assert not self.provider.health(h).healthy
        assert len(listed) == len(self.live)

    @invariant()
    def clock_never_goes_backwards(self) -> None:
        assert self.clock.now() >= _T0


# Feature: hands-on-labs, Property 2: Provider lifecycle model, destroy is idempotent
SandboxLifecycleMachine.TestCase.settings = settings(
    max_examples=100, stateful_step_count=30, deadline=None
)
TestSandboxLifecycle = SandboxLifecycleMachine.TestCase
