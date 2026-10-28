"""Unit tests for the framework-free domain ports (task 4.1, Req 5.1, 5.5).

These assert the five external-system ports named in ``.kiro/steering/tech.md``
(``Clock``, ``Mailer``, ``ObjectStorage``, ``TaskQueue``, ``LLMProvider``) are
declared, importable from the domain package, and structurally satisfiable by a
minimal in-test implementation — the substitutability the fakes (task 5.3) will
rely on. They also guard the import-boundary invariant (Req 1.8): the ports
module imports no banned web/API framework.

The implementations below are deliberately minimal test doubles, not the
production fakes; they exist only to prove each Protocol is satisfiable.
"""

from __future__ import annotations

import ast
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

import edu_it_domain
from edu_it_domain.ports import (
    Clock,
    JobHandler,
    LLMCompletion,
    LLMProvider,
    Mailer,
    ObjectStorage,
    OutboundMail,
    StoredObject,
    TaskQueue,
)

EXPECTED_PORTS = ("Clock", "Mailer", "ObjectStorage", "TaskQueue", "LLMProvider")


def test_all_five_ports_are_exported_from_the_domain_package() -> None:
    # The port list in tech steering, re-exported from the package root.
    for name in EXPECTED_PORTS:
        assert hasattr(edu_it_domain, name), f"domain is missing port {name!r}"
        assert name in edu_it_domain.__all__


def test_ports_are_runtime_checkable_protocols() -> None:
    # runtime_checkable lets wiring/tests assert substitutability via isinstance.
    for port in (Clock, Mailer, ObjectStorage, TaskQueue, LLMProvider):
        assert getattr(port, "_is_runtime_protocol", False) is True


# --- Minimal in-test doubles proving each Protocol is satisfiable ------------


class _FixedClock:
    def now(self) -> datetime:
        return datetime(2024, 1, 1, tzinfo=UTC)


class _RecordingMailer:
    def __init__(self) -> None:
        self.sent: list[OutboundMail] = []

    def send(self, mail: OutboundMail) -> None:
        self.sent.append(mail)


class _MemoryObjectStorage:
    def __init__(self) -> None:
        self._store: dict[str, StoredObject] = {}

    def put(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str = "application/octet-stream",
        metadata: dict[str, str] | None = None,
    ) -> None:
        self._store[key] = StoredObject(key, data, content_type, metadata or {})

    def get(self, key: str) -> StoredObject:
        return self._store[key]

    def exists(self, key: str) -> bool:
        return key in self._store

    def delete(self, key: str) -> None:
        self._store.pop(key, None)


class _MemoryTaskQueue:
    def __init__(self) -> None:
        self.enqueued: list[tuple[str, Mapping[str, object], str | None]] = []
        self.handlers: dict[str, JobHandler] = {}

    def enqueue(
        self,
        job_name: str,
        payload: Mapping[str, object],
        *,
        dedupe_key: str | None = None,
    ) -> None:
        self.enqueued.append((job_name, payload, dedupe_key))

    def consume(self, job_name: str, handler: JobHandler) -> None:
        self.handlers[job_name] = handler


class _StubLLMProvider:
    def complete(
        self,
        prompt: str,
        *,
        model: str,
        stop: Sequence[str] | None = None,
    ) -> LLMCompletion:
        return LLMCompletion(text=prompt, model=model)


def test_minimal_double_satisfies_each_port_structurally() -> None:
    assert isinstance(_FixedClock(), Clock)
    assert isinstance(_RecordingMailer(), Mailer)
    assert isinstance(_MemoryObjectStorage(), ObjectStorage)
    assert isinstance(_MemoryTaskQueue(), TaskQueue)
    assert isinstance(_StubLLMProvider(), LLMProvider)


def test_clock_returns_timezone_aware_instant() -> None:
    clock: Clock = _FixedClock()
    assert clock.now().tzinfo is not None


def test_mailer_retains_sent_mail_for_inspection() -> None:
    mailer = _RecordingMailer()
    port: Mailer = mailer
    port.send(OutboundMail(to="a@example.test", subject="s", body="b"))
    assert mailer.sent[0].to == "a@example.test"


def test_object_storage_round_trips_and_delete_is_idempotent() -> None:
    storage: ObjectStorage = _MemoryObjectStorage()
    storage.put("k", b"bytes", content_type="text/plain")
    assert storage.exists("k") is True
    assert storage.get("k").data == b"bytes"
    storage.delete("k")
    storage.delete("k")  # idempotent: deleting a missing key is a no-op
    assert storage.exists("k") is False


def test_task_queue_records_enqueue_and_registers_handler() -> None:
    queue = _MemoryTaskQueue()
    port: TaskQueue = queue
    port.enqueue("verify_email", {"user": "u1"}, dedupe_key="u1")
    received: list[Mapping[str, object]] = []
    port.consume("verify_email", received.append)
    assert queue.enqueued == [("verify_email", {"user": "u1"}, "u1")]
    assert "verify_email" in queue.handlers


def test_llm_provider_returns_completion_with_provenance() -> None:
    provider: LLMProvider = _StubLLMProvider()
    result = provider.complete("hello", model="fake-1")
    assert result.text == "hello"
    assert result.model == "fake-1"


def test_ports_package_imports_no_banned_framework() -> None:
    # Mirror the import-boundary invariant (Req 1.8) at the port level: no
    # top-level import in the ports package names a web/API framework.
    banned = {"fastapi", "starlette", "pydantic", "sqlalchemy", "redis", "httpx"}
    ports_dir = Path(edu_it_domain.__file__).parent / "ports"
    for path in sorted(ports_dir.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".", 1)[0] not in banned
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                assert node.module.split(".", 1)[0] not in banned
