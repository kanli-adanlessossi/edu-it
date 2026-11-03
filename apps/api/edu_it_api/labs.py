"""Labs router under ``/v1`` (task 7.3, Req 6.4, 14.2, 14.7, 15.3, 17.1-17.5, 30.6).

A thin HTTP boundary over the lab use cases in ``edu_it_application.labs``
(``api -> application -> domain``). Resources are addressed by ``stable_id`` only;
no internal numeric key appears in a body, header, or cursor (Req 17.4). Lab
errors are not handled here: ``errors/labs.py`` renders them as RFC 9457 problems.

Grading-run and terminal routes belong to other tasks and are not registered here.
The use cases, catalog, clock and identity resolver are injected through
:class:`LabServices`, so the composition root picks the adapters.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import asdict, dataclass, field
from typing import Annotated

from edu_it_application.labs import (
    AuthorizeTerminal,
    EndSessionUseCase,
    GetSessionUseCase,
    HintNotAvailableError,
    LabNotFoundError,
    LabSessionNotFoundError,
    ListSessionsUseCase,
    RequestGradingUseCase,
    ResetLabUseCase,
    RevealLabHintUseCase,
    SaveFilesUseCase,
    StartLabUseCase,
    TerminalGatewayCore,
)
from edu_it_application.learning.pagination import CursorCodec
from edu_it_domain import config as domain_config
from edu_it_domain.config import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from edu_it_domain.labs.grading import CheckResultView, to_learner_view
from edu_it_domain.labs.models import GradingRun, LabSession, RunStatus, SessionStatus
from edu_it_domain.labs.session_rules import deadlines
from edu_it_domain.ports.clock import Clock
from edu_it_domain.ports.lab_catalog import LabCatalog
from edu_it_domain.ports.lab_sessions import GradingRunRepository
from fastapi import APIRouter, Header, Query, Request, Response, status
from starlette.concurrency import run_in_threadpool
from starlette.responses import StreamingResponse

from edu_it_api.errors.handlers import request_locale
from edu_it_api.errors.labs import LAB_PROVISION_FAILED
from edu_it_api.labs_schemas import (
    CheckResultResponse,
    GradingRunEventResponse,
    GradingRunResponse,
    HintRequest,
    HintResponse,
    LabResponse,
    LabSessionPageResponse,
    LabSessionResponse,
    LabTaskResponse,
    SaveFilesRequest,
    SaveFilesResponse,
    SessionErrorResponse,
    StartLabRequest,
)
from edu_it_api.terminal import SESSION_COOKIE_NAME, add_terminal_route

__all__ = ["FeedbackSource", "HintTextSource", "LabServices", "Sleep", "build_labs_router"]

HintTextSource = Callable[[str, str, int, str], str | None]
"""``(lab_id, task_id, level, locale) -> hint text``; ``None`` when the level has no text."""

FeedbackSource = Callable[[str, str], Mapping[str, str]]
"""``(lab_id, locale) -> {feedback_key: translated message}``."""

Sleep = Callable[[float], Awaitable[None]]
"""Injectable async sleep for the SSE poll loop (seconds)."""

_SESSIONS_SCOPE = "lab-sessions"
_DEFAULT_LOCALE = "en"


@dataclass(frozen=True, slots=True)
class LabServices:
    """The use cases, ports and identity resolver the labs router needs."""

    current_user: Callable[[Request], str]
    catalog: LabCatalog
    start: StartLabUseCase
    reset: ResetLabUseCase
    end: EndSessionUseCase
    get_session: GetSessionUseCase
    list_sessions: ListSessionsUseCase
    save_files: SaveFilesUseCase
    reveal_hint: RevealLabHintUseCase
    hint_text: HintTextSource
    cursor_codec: CursorCodec
    clock: Clock
    config: object = domain_config
    request_grading: RequestGradingUseCase | None = None
    runs: GradingRunRepository | None = None
    feedback_messages: FeedbackSource = field(default=lambda _lab, _locale: {})
    sleep: Sleep = asyncio.sleep
    authorize_terminal: AuthorizeTerminal | None = None
    terminal_gateway: TerminalGatewayCore | None = None
    session_cookie_name: str = SESSION_COOKIE_NAME


def _localized(texts: Mapping[str, str], locale: str) -> str:
    """Text in ``locale``, falling back to English, then to any available text."""
    if locale in texts:
        return texts[locale]
    if _DEFAULT_LOCALE in texts:
        return texts[_DEFAULT_LOCALE]
    return next(iter(texts.values()), "")


def build_labs_router(services: LabServices) -> APIRouter:
    """Build the labs router for the ``/v1`` sub-application."""
    router = APIRouter(tags=["labs"])
    user_of = services.current_user

    def session_view(session: LabSession, locale: str) -> LabSessionResponse:
        dl = deadlines(session, services.config)
        interval = services.config.LAB_GRADING_MIN_INTERVAL  # type: ignore[attr-defined]
        accepted = session.last_grading_accepted_at
        error: SessionErrorResponse | None = None
        if session.status is SessionStatus.FAILED:
            problem = LAB_PROVISION_FAILED
            error = SessionErrorResponse(
                type=problem.type_uri,
                title=_localized(problem.title, locale),
                detail=_localized(problem.detail, locale),
            )
        return LabSessionResponse(
            stable_id=session.stable_id,
            lab_id=session.lab_id,
            status=session.status.value,
            created_at=session.created_at,
            ttl_at=session.ttl_at,
            idle_deadline=dl.idle_deadline,
            grading_available_at=None if accepted is None else accepted + interval,
            ended_at=session.ended_at,
            server_now=services.clock.now(),
            error=error,
        )

    @router.get("/labs/{lab_id}", response_model=LabResponse)
    def get_lab(
        lab_id: str,
        request: Request,
        locale: Annotated[str, Query(min_length=2, max_length=16)] = _DEFAULT_LOCALE,
    ) -> LabResponse:
        """A lab's instructions, tasks and starter file names; hint text is withheld."""
        user_of(request)  # authenticated like every lab route (api-route-wiring Req 1.3)
        lab = services.catalog.get(lab_id)
        if lab is None or lab.archived:
            raise LabNotFoundError(lab_id)
        return LabResponse(
            lab_id=lab.lab_id,
            kind=lab.kind,
            title=_localized(lab.text, locale),
            locale=locale,
            concept_ids=list(lab.concept_ids),
            tasks=[
                LabTaskResponse(
                    task_id=t.task_id,
                    required=t.required,
                    text=_localized(t.text, locale),
                    hint_count=t.hint_count,
                )
                for t in lab.tasks
            ],
            learner_files=list(lab.learner_files),
            content_version=lab.content_version,
        )

    @router.post(
        "/lab-sessions",
        response_model=LabSessionResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def start_session(
        body: StartLabRequest,
        request: Request,
        idempotency_key: Annotated[str, Header(min_length=1, max_length=255)],
    ) -> LabSessionResponse:
        """Start a lab session; ``Idempotency-Key`` is required. Status is ``provisioning``."""
        result = services.start.execute(user_of(request), body.lab_id, idempotency_key)
        return session_view(result.session, request_locale(request))

    @router.get("/lab-sessions", response_model=LabSessionPageResponse)
    def list_sessions(
        request: Request,
        cursor: Annotated[str | None, Query(max_length=1024)] = None,
        limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    ) -> LabSessionPageResponse:
        """The caller's sessions, oldest first, one keyset page at a time."""
        after = None if cursor is None else services.cursor_codec.decode(_SESSIONS_SCOPE, cursor)
        page = services.list_sessions.execute(user_of(request), limit=limit, after=after)
        locale = request_locale(request)
        return LabSessionPageResponse(
            items=[session_view(s, locale) for s in page.items],
            next_cursor=(
                None
                if page.next_after is None
                else services.cursor_codec.encode(_SESSIONS_SCOPE, page.next_after)
            ),
        )

    @router.get("/lab-sessions/{session_id}", response_model=LabSessionResponse)
    def get_session(session_id: str, request: Request) -> LabSessionResponse:
        """Status, deadlines, ``server_now`` and, for ``failed``, a translated error."""
        session = services.get_session.execute(user_of(request), session_id)
        return session_view(session, request_locale(request))

    @router.delete("/lab-sessions/{session_id}", response_model=LabSessionResponse)
    def end_session(session_id: str, request: Request) -> LabSessionResponse:
        """End the session; ending an already ended session is a no-op."""
        session = services.end.execute(user_of(request), session_id)
        return session_view(session, request_locale(request))

    @router.post(
        "/lab-sessions/{session_id}/reset",
        response_model=LabSessionResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def reset_session(
        session_id: str,
        request: Request,
        idempotency_key: Annotated[str, Header(min_length=1, max_length=255)],
    ) -> LabSessionResponse:
        """End the session and start a fresh one for the same lab."""
        result = services.reset.execute(user_of(request), session_id, idempotency_key)
        return session_view(result.session, request_locale(request))

    @router.put("/lab-sessions/{session_id}/files", response_model=SaveFilesResponse)
    def save_files(session_id: str, body: SaveFilesRequest, request: Request) -> SaveFilesResponse:
        """Save learner files into the sandbox workspace (caps enforced, Req 14.7)."""
        saved = services.save_files.execute(user_of(request), session_id, body.files)
        return SaveFilesResponse(saved=saved)

    @router.post("/lab-sessions/{session_id}/hints", response_model=HintResponse)
    def reveal_hint(session_id: str, body: HintRequest, request: Request) -> HintResponse:
        """Reveal a hint level of a task in order; records a ``hint_reveal`` (Req 14.2, 15.3)."""
        user_id = user_of(request)
        session = services.get_session.execute(user_id, session_id)
        grant = services.reveal_hint.execute(user_id, session_id, body.task_id, body.level)
        text = services.hint_text(
            session.lab_id, grant.task_id, grant.level, request_locale(request)
        )
        if text is None:
            raise HintNotAvailableError(grant.task_id, grant.level)
        return HintResponse(
            task_id=grant.task_id,
            level=grant.level,
            total_levels=grant.total_levels,
            newly_revealed=grant.newly_revealed,
            text=text,
        )

    if services.request_grading is not None and services.runs is not None:
        _add_grading_routes(router, services, services.request_grading, services.runs)

    if services.authorize_terminal is not None and services.terminal_gateway is not None:
        add_terminal_route(
            router,
            authorize=services.authorize_terminal,
            gateway=services.terminal_gateway,
            cookie_name=services.session_cookie_name,
        )

    return router


def _sse(event: str, event_id: int, data: Mapping[str, object]) -> bytes:
    payload = json.dumps(data, separators=(",", ":"), default=str)
    return f"id: {event_id}\nevent: {event}\ndata: {payload}\n\n".encode()


def _add_grading_routes(
    router: APIRouter,
    services: LabServices,
    request_grading: RequestGradingUseCase,
    runs: GradingRunRepository,
) -> None:
    """Grading-run endpoints and the result stream (task 7.5, Req 11.6, 18.1-18.5)."""
    user_of = services.current_user

    def owned_run(user_id: str, session_id: str, run_id: str) -> tuple[LabSession, GradingRun]:
        """The run of a session the caller owns; anything else is an indistinguishable 404."""
        session = services.get_session.execute(user_id, session_id)
        run = runs.get(run_id)
        if run is None or run.session_id != session.stable_id:
            raise LabSessionNotFoundError(session_id)
        return session, run

    def results_of(run: GradingRun, lab_id: str, locale: str) -> list[CheckResultResponse]:
        messages = services.feedback_messages(lab_id, locale)
        return [
            CheckResultResponse.model_validate(_fields(to_learner_view(r, messages)))
            for r in run.results
        ]

    def run_view(run: GradingRun, lab_id: str, locale: str) -> GradingRunResponse:
        return GradingRunResponse(
            stable_id=run.stable_id,
            session_id=run.session_id,
            status=run.status.value,
            accepted_at=run.accepted_at,
            deadline_at=run.deadline_at,
            started_at=run.started_at,
            graded_at=run.graded_at,
            results=results_of(run, lab_id, locale),
        )

    @router.post(
        "/lab-sessions/{session_id}/grading-runs",
        response_model=GradingRunResponse,
        status_code=status.HTTP_202_ACCEPTED,
    )
    def request_run(
        session_id: str,
        request: Request,
        response: Response,
        idempotency_key: Annotated[str, Header(min_length=1, max_length=255)],
    ) -> GradingRunResponse:
        """Accept a grading request; ``Idempotency-Key`` is required."""
        user_id = user_of(request)
        accepted = request_grading.execute(user_id, session_id, idempotency_key)
        session = services.get_session.execute(user_id, session_id)
        response.headers["Location"] = (
            f"/v1/lab-sessions/{session_id}/grading-runs/{accepted.run.stable_id}"
        )
        return run_view(accepted.run, session.lab_id, request_locale(request))

    @router.get(
        "/lab-sessions/{session_id}/grading-runs/{run_id}", response_model=GradingRunResponse
    )
    def get_run(session_id: str, run_id: str, request: Request) -> GradingRunResponse:
        """The run with the learner-facing view of its results so far."""
        session, run = owned_run(user_of(request), session_id, run_id)
        return run_view(run, session.lab_id, request_locale(request))

    @router.get("/lab-sessions/{session_id}/grading-runs/{run_id}/events")
    def run_events(
        session_id: str,
        run_id: str,
        request: Request,
        last_event_id: Annotated[str | None, Header()] = None,
    ) -> StreamingResponse:
        """SSE: one ``result`` event per Check, then one ``run`` event, then the stream ends.

        Event ids are the 1-based position in that sequence, so ``Last-Event-ID``
        resumes after the last event the client received.
        """
        session, _ = owned_run(user_of(request), session_id, run_id)
        locale = request_locale(request)
        seen = _parse_last_event_id(last_event_id)
        interval = services.config.LAB_RESULT_POLL_INTERVAL.total_seconds()  # type: ignore[attr-defined]

        async def stream() -> AsyncIterator[bytes]:
            sent = seen
            while True:
                run = await run_in_threadpool(runs.get, run_id)
                if run is None:
                    return
                messages = services.feedback_messages(session.lab_id, locale)
                for index, result in enumerate(run.results, start=1):
                    if index > sent:
                        view = to_learner_view(result, messages)
                        yield _sse("result", index, _fields(view))
                        sent = index
                if run.status is not RunStatus.RUNNING:
                    final_id = len(run.results) + 1
                    if final_id > sent:
                        event = GradingRunEventResponse(
                            stable_id=run.stable_id,
                            session_id=run.session_id,
                            status=run.status.value,
                            graded_at=run.graded_at,
                        )
                        yield _sse("run", final_id, event.model_dump(mode="json"))
                    return
                await services.sleep(interval)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )


def _parse_last_event_id(value: str | None) -> int:
    """The last event number the client saw; anything unparseable restarts the stream."""
    if value is None:
        return 0
    try:
        return max(0, int(value))
    except ValueError:
        return 0


def _fields(view: CheckResultView) -> dict[str, object]:
    """Fields of a learner-facing Check result view."""
    return asdict(view)
