"""Real composition of the labs router for the served API (hands-on-labs design, Req 6, 12).

:func:`build_lab_router` wires the lab use cases, the labs router (start, session, grading,
hints, files) and the terminal WebSocket over the real adapters: PostgreSQL repositories,
the Redis task queue / session store / idempotency store, and the sandbox provider chosen
by ``SANDBOX_PROVIDER`` (``docker`` is development only and refused in production by
``build_sandbox_provider``). The lab Config is validated by ``create_app`` before this
router is mounted, so a bad Config creates no Sandbox.

Authentication here is only the resolution of the opaque ``edu_it_session`` cookie through
the :class:`~edu_it_domain.ports.session_store.SessionStore` (ADR 0004); this module adds no
sign-up or sign-in route.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from edu_it_application.labs import (
    AuthorizeTerminal,
    EndSessionUseCase,
    GetSessionUseCase,
    ListSessionsUseCase,
    RequestGradingResult,
    RequestGradingUseCase,
    ResetLabUseCase,
    RevealLabHintUseCase,
    SaveFilesUseCase,
    StartLabResult,
    StartLabUseCase,
    TerminalGatewayCore,
    TerminalRegistry,
)
from edu_it_application.learning.pagination import CursorCodec
from edu_it_content.lab_runtime import feedback_messages_for, hint_text_for, load_synced_store
from edu_it_domain.labs.models import (
    CheckOutcome,
    CheckResult,
    GradingRun,
    LabSession,
    RunStatus,
    SessionStatus,
)
from edu_it_infrastructure.adapters.idempotency_store import Redis_IdempotencyStore
from edu_it_infrastructure.adapters.lab_catalog import Content_LabCatalog
from edu_it_infrastructure.composition import CompositionSettings, build_sandbox_provider
from edu_it_infrastructure.lab_composition import build_lab_ports
from fastapi import APIRouter, HTTPException, Request

from edu_it_api.labs import LabServices, build_labs_router
from edu_it_api.terminal import SESSION_COOKIE_NAME

__all__ = ["LabComposition", "build_lab_router", "decode_result", "encode_result"]


@dataclass(frozen=True, slots=True)
class LabComposition:
    """The labs router and the terminal registry the server drains on shutdown."""

    router: APIRouter
    registry: TerminalRegistry


# -- Idempotency result codec ------------------------------------------------------------


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _parse(value: str | None) -> datetime | None:
    return None if value is None else datetime.fromisoformat(value)


def _session_to_json(s: LabSession) -> dict[str, object]:
    return {
        "stable_id": s.stable_id,
        "user_id": s.user_id,
        "lab_id": s.lab_id,
        "status": s.status.value,
        "created_at": _iso(s.created_at),
        "ttl_at": _iso(s.ttl_at),
        "sandbox_ref": s.sandbox_ref,
        "last_activity_at": _iso(s.last_activity_at),
        "last_grading_accepted_at": _iso(s.last_grading_accepted_at),
        "ended_at": _iso(s.ended_at),
        "provision_attempts": s.provision_attempts,
        "next_provision_at": _iso(s.next_provision_at),
        "terminal_epoch": s.terminal_epoch,
    }


def _session_from_json(d: dict[str, object]) -> LabSession:
    created, ttl = _parse(str(d["created_at"])), _parse(str(d["ttl_at"]))
    assert created is not None and ttl is not None
    return LabSession(
        stable_id=str(d["stable_id"]),
        user_id=str(d["user_id"]),
        lab_id=str(d["lab_id"]),
        status=SessionStatus(str(d["status"])),
        created_at=created,
        ttl_at=ttl,
        sandbox_ref=None if d["sandbox_ref"] is None else str(d["sandbox_ref"]),
        last_activity_at=_parse(d["last_activity_at"]),  # type: ignore[arg-type]
        last_grading_accepted_at=_parse(d["last_grading_accepted_at"]),  # type: ignore[arg-type]
        ended_at=_parse(d["ended_at"]),  # type: ignore[arg-type]
        provision_attempts=int(d["provision_attempts"]),  # type: ignore[call-overload]
        next_provision_at=_parse(d["next_provision_at"]),  # type: ignore[arg-type]
        terminal_epoch=int(d["terminal_epoch"]),  # type: ignore[call-overload]
    )


def _run_to_json(r: GradingRun) -> dict[str, object]:
    return {
        "stable_id": r.stable_id,
        "session_id": r.session_id,
        "status": r.status.value,
        "accepted_at": _iso(r.accepted_at),
        "deadline_at": _iso(r.deadline_at),
        "started_at": _iso(r.started_at),
        "graded_at": _iso(r.graded_at),
        "results": [
            [c.check_id, c.task_id, c.outcome.value, c.outcome_code, c.feedback_key]
            for c in r.results
        ],
    }


def _run_from_json(d: dict[str, object]) -> GradingRun:
    accepted, deadline = _parse(str(d["accepted_at"])), _parse(str(d["deadline_at"]))
    assert accepted is not None and deadline is not None
    return GradingRun(
        stable_id=str(d["stable_id"]),
        session_id=str(d["session_id"]),
        status=RunStatus(str(d["status"])),
        accepted_at=accepted,
        deadline_at=deadline,
        started_at=_parse(d["started_at"]),  # type: ignore[arg-type]
        graded_at=_parse(d["graded_at"]),  # type: ignore[arg-type]
        results=tuple(
            CheckResult(c[0], c[1], CheckOutcome(c[2]), c[3], c[4])
            for c in d["results"]  # type: ignore[attr-defined]
        ),
    )


def encode_result(result: object) -> str:
    """Serialize a start-lab or grading-request result for the idempotency store."""
    if isinstance(result, StartLabResult):
        return json.dumps(
            {"kind": "start", "replayed": result.replayed, "body": _session_to_json(result.session)}
        )
    if isinstance(result, RequestGradingResult):
        return json.dumps(
            {"kind": "grade", "replayed": result.replayed, "body": _run_to_json(result.run)}
        )
    raise TypeError(f"cannot store idempotent result of type {type(result).__name__}")


def decode_result(text: str) -> object:
    """Inverse of :func:`encode_result`; a replay is reported as ``replayed=True``."""
    data = json.loads(text)
    if data["kind"] == "start":
        return StartLabResult(_session_from_json(data["body"]), replayed=True)
    if data["kind"] == "grade":
        return RequestGradingResult(_run_from_json(data["body"]), replayed=True)
    raise ValueError(f"unknown stored result kind {data['kind']!r}")


# -- Composition --------------------------------------------------------------------------


def build_lab_router(
    *,
    content_root: str | Path,
    settings: CompositionSettings | None = None,
    cookie_name: str = SESSION_COOKIE_NAME,
) -> LabComposition:
    """Build the labs router over the real adapters; contacts no datastore until used."""
    settings = settings if settings is not None else CompositionSettings.from_env()
    ports = build_lab_ports(settings)
    sandbox = build_sandbox_provider(settings, clock=ports.clock)
    provider_name = settings.sandbox_provider.value
    store, _report = load_synced_store(content_root)
    catalog = Content_LabCatalog(store)
    idempotency = Redis_IdempotencyStore(
        ports.redis,  # type: ignore[arg-type]
        encode=encode_result,
        decode=decode_result,
    )

    def current_user(request: Request) -> str:
        ref = request.cookies.get(cookie_name)
        resolved = None if ref is None else ports.session_store.resolve(ref)
        if resolved is None:
            raise HTTPException(status_code=401)
        return resolved.user_stable_id

    end = EndSessionUseCase(sessions=ports.sessions, queue=ports.queue, clock=ports.clock)
    start = StartLabUseCase(
        sessions=ports.sessions,
        catalog=catalog,
        queue=ports.queue,
        idempotency=idempotency,
        clock=ports.clock,
        ids=ports.ids,
    )
    gateway = TerminalGatewayCore(
        sessions=ports.sessions,
        sandbox=sandbox,
        clock=ports.clock,
        provider_name=provider_name,
    )
    services = LabServices(
        current_user=current_user,
        catalog=catalog,
        start=start,
        reset=ResetLabUseCase(sessions=ports.sessions, end=end, start=start),
        end=end,
        get_session=GetSessionUseCase(sessions=ports.sessions),
        list_sessions=ListSessionsUseCase(sessions=ports.sessions),
        save_files=SaveFilesUseCase(
            sessions=ports.sessions,
            catalog=catalog,
            sandbox=sandbox,
            clock=ports.clock,
            provider_name=provider_name,
        ),
        reveal_hint=RevealLabHintUseCase(
            sessions=ports.sessions,
            catalog=catalog,
            mastery=ports.mastery,
            clock=ports.clock,
        ),
        hint_text=hint_text_for(store),
        cursor_codec=CursorCodec(ports.clock),
        clock=ports.clock,
        request_grading=RequestGradingUseCase(
            sessions=ports.sessions,
            runs=ports.runs,
            queue=ports.queue,
            idempotency=idempotency,
            clock=ports.clock,
            ids=ports.ids,
        ),
        runs=ports.runs,
        feedback_messages=feedback_messages_for(store),
        authorize_terminal=AuthorizeTerminal(
            sessions=ports.sessions, session_store=ports.session_store
        ),
        terminal_gateway=gateway,
        session_cookie_name=cookie_name,
    )
    return LabComposition(router=build_labs_router(services), registry=gateway.registry)
