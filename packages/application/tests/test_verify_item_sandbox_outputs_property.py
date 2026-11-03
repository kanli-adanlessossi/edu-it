"""Property test: command outputs come from the sandbox only (ai-content task 7.4).

Feature: ai-content, Property 13: Command outputs and executions come from the sandbox only

**Validates: Requirements 7.5, 7.6**
"""

from __future__ import annotations

import dataclasses
import json
from collections import deque
from collections.abc import Sequence

from edu_it_application.ai import ItemKind, ItemUnderVerification, VerifyItem
from edu_it_domain.ai.verification import FindingKind, LabUnderVerification, VerificationFinding
from edu_it_domain.ports.sandbox import ExecResult, SandboxHandle
from edu_it_infrastructure.fakes import Fake_LLMProvider, Fake_SandboxProvider, Test_Clock
from edu_it_infrastructure.fakes.sandbox_provider import FakeSandbox
from hypothesis import given, settings
from hypothesis import strategies as st

_text = st.text(
    alphabet=st.characters(blacklist_categories=["Cs"], blacklist_characters="\x00"),
    max_size=40,
)
_result = st.tuples(st.integers(min_value=0, max_value=255), _text, _text, st.booleans())
_commands = st.lists(st.text(min_size=1, max_size=20), min_size=1, max_size=6)


class _Labs:
    def execute(self, lab: LabUnderVerification) -> tuple[VerificationFinding, ...]:
        return ()


class _RecordingSandbox(Fake_SandboxProvider):
    """Records the handles returned by ``provision`` and targeted by ``exec``."""

    def __init__(self) -> None:
        super().__init__(Test_Clock())
        self.provisioned: list[SandboxHandle] = []
        self.exec_targets: list[SandboxHandle] = []

    def provision(self, spec):  # type: ignore[no-untyped-def]
        handle = super().provision(spec)
        self.provisioned.append(handle)
        return handle

    def exec(self, handle, command, **kwargs):  # type: ignore[no-untyped-def]
        self.exec_targets.append(handle)
        return super().exec(handle, command, **kwargs)


@settings(max_examples=100, deadline=None)
@given(data=st.data(), commands=_commands)
def test_property_13_outputs_and_execs_come_from_the_sandbox_only(
    data: st.DataObject, commands: list[str]
) -> None:
    scripted = [data.draw(_result) for _ in commands]
    queue = deque(scripted)
    sandbox = _RecordingSandbox()

    def handler(sb: FakeSandbox, cmd: Sequence[str], user: str) -> ExecResult:
        code, out, err, timed_out = queue.popleft()
        return ExecResult(code, out.encode(), err.encode(), timed_out)

    sandbox.exec_handler = handler
    llm = Fake_LLMProvider({"verify_answer": json.dumps({"selected": []})})
    verify = VerifyItem(independent_llm=llm, sandbox=sandbox, lab_verifier=_Labs())
    item = ItemUnderVerification("l1", ItemKind.LESSON, commands=tuple(commands), image="img")

    findings, outputs = verify.execute(item)

    # Outputs equal the scripted sandbox results, in command order.
    assert [(o.command, o.exit_code, o.stdout, o.stderr, o.timed_out) for o in outputs] == [
        (c, *r) for c, r in zip(commands, scripted, strict=True)
    ]
    # Only sandbox results exist: nothing came from the LLM.
    assert len(llm.calls) == 0
    assert not queue
    # Every exec targeted a handle returned by provision.
    assert len(sandbox.exec_targets) == len(commands)
    assert all(t in sandbox.provisioned for t in sandbox.exec_targets)
    # A failed or timed-out command is reported, never silently passed.
    failed = sum(1 for code, _, _, timed_out in scripted if timed_out or code != 0)
    assert sum(f.kind is FindingKind.COMMAND_FAILED for f in findings) == failed
    # No output field exists on the item schema.
    names = {f.name for f in dataclasses.fields(ItemUnderVerification)}
    assert not {n for n in names if "output" in n.lower() or "stdout" in n.lower()}
