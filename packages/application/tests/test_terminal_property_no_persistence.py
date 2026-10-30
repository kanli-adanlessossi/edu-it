"""Property test: no terminal I/O or learner file content is persisted (task 10.5).

Feature: hands-on-labs, Property 17: No terminal I/O or learner file content is persisted

Drives the real ``TerminalGatewayCore`` and ``SaveFilesUseCase`` over the
in-memory fakes with a unique marker carried by terminal frames (binary, text,
malformed) and saved files, including a socket drop mid-stream. The marker must
not appear in any repository row, log record, export payload or queue payload.
The sandbox's own workspace/TTY buffer is excluded: it is the learner's
ephemeral machine, not a persistence layer.
"""

from __future__ import annotations

import asyncio
import logging

from edu_it_application.labs import (
    Frame,
    SaveFilesUseCase,
    TerminalAuthorization,
    TerminalGatewayCore,
)
from edu_it_domain import config
from edu_it_domain.labs.models import LabSession, SessionStatus
from edu_it_domain.labs.sandbox_spec import build_sandbox_spec
from edu_it_domain.ports.lab_catalog import LabDefinition, LabTaskDefinition
from edu_it_infrastructure.fakes import (
    Fake_LabCatalog,
    Fake_SandboxProvider,
    In_Memory_GradingRunRepository,
    In_Memory_LabSessionRepository,
    In_Memory_PersonalDataGateway,
    In_Memory_TaskQueue,
    Test_Clock,
)
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

_LAB = LabDefinition(
    lab_id="lab1",
    kind="code",
    image="img",
    concept_ids=(),
    tasks=(LabTaskDefinition("t1", required=True, hint_count=0),),
    checks=(),
    learner_files=("main.py", "notes.txt"),
)

_markers = st.text(alphabet="0123456789abcdef", min_size=16, max_size=32).map(lambda s: f"MRK{s}")
# Frame kinds: raw binary line, JSON-looking text carrying the marker, malformed text.
_kinds = st.lists(st.sampled_from(["bin", "ping", "resize", "bad"]), min_size=1, max_size=8)
_files = st.lists(st.sampled_from(["main.py", "notes.txt"]), min_size=1, max_size=2, unique=True)


def _blob(*parts: object) -> str:
    return "\n".join(str(p) for p in parts)


def _frame(kind: str, marker: str) -> Frame:
    if kind == "bin":
        return Frame(data=f"echo {marker}\n".encode())
    if kind == "ping":
        return Frame(text='{"type":"ping","note":"' + marker + '"}')
    if kind == "resize":
        return Frame(text='{"type":"resize","rows":24,"cols":80,"x":"' + marker + '"}')
    return Frame(text=f"not json {marker}")


class _Socket:
    def __init__(self) -> None:
        self.inbox: asyncio.Queue[Frame | None] = asyncio.Queue()
        self.sent: list[Frame] = []
        self.closed: tuple[int, str] | None = None

    async def receive(self) -> Frame | None:
        return await self.inbox.get()

    async def send(self, frame: Frame) -> None:
        self.sent.append(frame)

    async def close(self, code: int, reason: str = "") -> None:
        if self.closed is None:
            self.closed = (code, reason)


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.blobs: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.blobs.append(_blob(record.getMessage(), record.args, record.__dict__.get("exc_text")))


async def _scenario(marker: str, kinds: list[str], names: list[str], drop_early: bool) -> str:
    clock = Test_Clock()
    runs = In_Memory_GradingRunRepository()
    sessions = In_Memory_LabSessionRepository(runs)
    sandbox = Fake_SandboxProvider(clock)
    queue = In_Memory_TaskQueue()
    personal = In_Memory_PersonalDataGateway()
    handle = sandbox.provision(build_sandbox_spec(_LAB, "s1"))
    now = clock.now()
    sessions.add_if_no_active(
        LabSession(
            stable_id="s1",
            user_id="u1",
            lab_id="lab1",
            status=SessionStatus.READY,
            created_at=now,
            ttl_at=now + config.LAB_SESSION_TTL,
            sandbox_ref=handle.sandbox_id,
        )
    )

    capture = _Capture()
    root = logging.getLogger()
    old_level = root.level
    root.addHandler(capture)
    root.setLevel(logging.DEBUG)
    try:
        SaveFilesUseCase(
            sessions=sessions,
            catalog=Fake_LabCatalog([_LAB]),
            sandbox=sandbox,
            clock=clock,
            provider_name="fake",
        ).execute("u1", "s1", {n: f"print('{marker}')\n".encode() for n in names})

        async def sleep(_: float) -> None:
            await asyncio.sleep(0.002)

        gateway = TerminalGatewayCore(
            sessions=sessions, sandbox=sandbox, clock=clock, provider_name="fake", sleep=sleep
        )
        sock = _Socket()
        task = asyncio.create_task(gateway.run(TerminalAuthorization("s1", "u1"), sock))
        for kind in kinds:
            await sock.inbox.put(_frame(kind, marker))
            if drop_early:
                break
        await asyncio.sleep(0.03)
        await sock.inbox.put(None)  # socket drop
        await asyncio.wait_for(task, 5)
    finally:
        root.removeHandler(capture)
        root.setLevel(old_level)

    # Everything that is persisted, exported or queued, rendered as text.
    export = personal.export_user_data("u1")
    return _blob(
        repr(sessions._sessions),
        repr(runs._runs),
        repr(runs._completions),
        repr(export),
        repr([(j.job_name, j.payload, j.dedupe_key) for j in queue._pending]),
        *capture.blobs,
    )


# Feature: hands-on-labs, Property 17: No terminal I/O or learner file content is persisted
# **Validates: Requirements 12.7, 14.9, 24.3, 26.6**
@settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(marker=_markers, kinds=_kinds, names=_files, drop_early=st.booleans())
def test_property_17_no_terminal_io_or_file_content_persisted(
    marker: str, kinds: list[str], names: list[str], drop_early: bool
) -> None:
    """Feature: hands-on-labs, Property 17: No terminal I/O or learner file content is persisted.

    **Validates: Requirements 12.7, 14.9, 24.3, 26.6**
    """
    observed = asyncio.run(_scenario(marker, kinds, names, drop_early))
    assert marker not in observed
    assert marker[3:] not in observed
