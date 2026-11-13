"""Composition root for the served API (api-route-wiring design, "Composition").

Three functions share one wiring path for the served app, the OpenAPI export, and tests:

* :func:`build_adapters` returns one frozen :class:`ApiAdapters` with every port
  implementation. ``fakes=False`` builds PostgreSQL, Redis, the configured Mailer and the
  sandbox provider chosen by ``SANDBOX_PROVIDER``; ``fakes=True`` builds only in-memory
  fakes and creates no Redis or PostgreSQL client.
* :func:`build_api_routers` builds the Account, Learning, List, Content and Lab routers and
  hands the *same* :class:`~edu_it_api.identity.SessionUserResolver` to each. It reads no
  environment variable: every input arrives through its arguments.
* ``serve.build_served_app`` calls both and passes ``v1_routers`` to ``create_app``.

Real clients and engines are created lazily, so building the real adapters contacts no
datastore. A required adapter that cannot be built stops the start, logging its name only
(:func:`edu_it_api.startup.refuse_start`). Time comes from ``System_Clock`` and ids from
``Uuid_IdGenerator`` in the real composition; ``Test_Clock`` is wired only with fakes.
"""

from __future__ import annotations

import json
import secrets
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from edu_it_application.ai import (
    DismissRecommendation,
    EligibleItems,
    GetRecommendation,
    ListReviewTasks,
    ReviewTaskActions,
    ServableCatalog,
    SubmitProblemReport,
    decode_problem_report_result,
    encode_problem_report_result,
    enqueue_item_evaluation,
)
from edu_it_application.authenticate import (
    AuthenticateSessionUseCase,
    AuthenticateUseCase,
    SignOutUseCase,
)
from edu_it_application.gdpr import DeleteAccountUseCase, ExportUserDataUseCase
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
from edu_it_application.learning import (
    AttemptQuizUseCase,
    EnrollUseCase,
    GetLessonUseCase,
    GetProgressUseCase,
    GetProjectUseCase,
    GetQuizUseCase,
    ListTracksUseCase,
    RevealHintUseCase,
    StartReviewUseCase,
    SubmitProjectUseCase,
    SubmitReviewUseCase,
)
from edu_it_application.learning.attempt_codec import decode_attempt_result, encode_attempt_result
from edu_it_application.learning.pagination import (
    CursorCodec,
    ListEnrollmentsUseCase,
    ListQuizAttemptsUseCase,
)
from edu_it_application.oauth_sign_in import (
    CompleteOAuthCallbackUseCase,
    ConfirmOAuthLinkUseCase,
    DeclineOAuthLinkUseCase,
)
from edu_it_application.resend_verification import ResendVerificationUseCase
from edu_it_application.reset_password import (
    CompletePasswordResetUseCase,
    RequestPasswordResetUseCase,
)
from edu_it_application.sign_up import SignUpUseCase
from edu_it_application.verify_email import VerifyEmailUseCase
from edu_it_content.lab_runtime import feedback_messages_for, hint_text_for, load_synced_store
from edu_it_domain import config as domain_config
from edu_it_domain.labs.models import (
    CheckOutcome,
    CheckResult,
    GradingRun,
    LabSession,
    RunStatus,
    SessionStatus,
)
from edu_it_domain.ports.attempts import AttemptRepository
from edu_it_domain.ports.catalog import CatalogRepository
from edu_it_domain.ports.clock import Clock
from edu_it_domain.ports.credential_store import CredentialStore
from edu_it_domain.ports.enrollment import EnrollmentRepository
from edu_it_domain.ports.github_oauth import GitHubOAuthClient
from edu_it_domain.ports.id_generator import IdGenerator
from edu_it_domain.ports.idempotency import IdempotencyStore
from edu_it_domain.ports.item_flags import ItemFlagWriter
from edu_it_domain.ports.item_quality import ObservationRecorder
from edu_it_domain.ports.lab_catalog import LabCatalog
from edu_it_domain.ports.lab_sessions import GradingRunRepository, LabSessionRepository
from edu_it_domain.ports.lessons import LessonRepository, ModuleLessonReader
from edu_it_domain.ports.link_builder import LinkBuilder
from edu_it_domain.ports.mailer import Mailer
from edu_it_domain.ports.mastery import MasteryRepository
from edu_it_domain.ports.oauth_link import OAuthLinkRepository
from edu_it_domain.ports.object_storage import ObjectStorage
from edu_it_domain.ports.path_recommendations import PathRecommendationRepository
from edu_it_domain.ports.pending_oauth_link import PendingOAuthLinkRepository
from edu_it_domain.ports.personal_data import PersonalDataGateway
from edu_it_domain.ports.problem_reports import ProblemReportRepository
from edu_it_domain.ports.progress import ProgressRepository
from edu_it_domain.ports.project_reviewer import ProjectReviewer
from edu_it_domain.ports.rate_limiter import SignInRateLimiter
from edu_it_domain.ports.repository import SessionRepository, UserRepository
from edu_it_domain.ports.reset_link import ResetLinkRepository
from edu_it_domain.ports.review import ReviewRepository
from edu_it_domain.ports.review_tasks import ReviewTaskRepository
from edu_it_domain.ports.role_assignment import RoleAssignmentRepository
from edu_it_domain.ports.sandbox import SandboxProvider
from edu_it_domain.ports.session_store import SessionStore
from edu_it_domain.ports.task_queue import TaskQueue
from edu_it_domain.ports.variants import VariantRepository
from edu_it_domain.ports.verification_link import VerificationLinkRepository
from edu_it_infrastructure.adapters.content_text import (
    Content_StoreTextSource,
    Content_StoreTitleReader,
)
from edu_it_infrastructure.adapters.idempotency_store import Redis_IdempotencyStore
from edu_it_infrastructure.adapters.lab_catalog import Content_LabCatalog
from edu_it_infrastructure.adapters.rate_limiter import Redis_SignInRateLimiter
from edu_it_infrastructure.adapters.redis_support import redis_from_url
from edu_it_infrastructure.adapters.session_store import Redis_SessionStore
from edu_it_infrastructure.adapters.task_queue import Redis_TaskQueue
from edu_it_infrastructure.clock import System_Clock
from edu_it_infrastructure.composition import (
    CompositionSettings,
    SandboxBackend,
    build_fake_clock,
    build_mailer,
    build_object_storage,
    build_sandbox_provider,
)
from edu_it_infrastructure.fakes import (
    Console_Mailer,
    Fake_ProjectReviewer,
    In_Memory_AttemptRepository,
    In_Memory_CatalogRepository,
    In_Memory_CredentialStore,
    In_Memory_EnrollmentRepository,
    In_Memory_GradingRunRepository,
    In_Memory_IdempotencyStore,
    In_Memory_ItemQualityRepository,
    In_Memory_LabSessionRepository,
    In_Memory_LessonRepository,
    In_Memory_MasteryRepository,
    In_Memory_OAuthLinkRepository,
    In_Memory_ObjectStorage,
    In_Memory_PathRecommendationRepository,
    In_Memory_PendingOAuthLinkRepository,
    In_Memory_PersonalDataGateway,
    In_Memory_ProblemReportRepository,
    In_Memory_ProgressRepository,
    In_Memory_ResetLinkRepository,
    In_Memory_ReviewRepository,
    In_Memory_ReviewTaskRepository,
    In_Memory_RoleAssignmentRepository,
    In_Memory_SessionRepository,
    In_Memory_SessionStore,
    In_Memory_SignInRateLimiter,
    In_Memory_TaskQueue,
    In_Memory_UserRepository,
    In_Memory_VariantRepository,
    In_Memory_VerificationLinkRepository,
    Sequential_IdGenerator,
)
from edu_it_infrastructure.fakes.github_oauth import Fake_GitHubOAuthClient
from edu_it_infrastructure.github_oauth import Urllib_GitHubOAuthClient
from edu_it_infrastructure.id_generator import Uuid_IdGenerator
from edu_it_infrastructure.link_builder import Web_LinkBuilder
from edu_it_infrastructure.password_hasher import Scrypt_PasswordHasher
from edu_it_infrastructure.persistence.account_repositories import (
    Postgres_CredentialStore,
    Postgres_OAuthLinkRepository,
    Postgres_PendingOAuthLinkRepository,
    Postgres_ResetLinkRepository,
    Postgres_VerificationLinkRepository,
)
from edu_it_infrastructure.persistence.item_quality import Postgres_ItemQualityRepository
from edu_it_infrastructure.persistence.lab_repositories import (
    Postgres_GradingRunRepository,
    Postgres_LabSessionRepository,
)
from edu_it_infrastructure.persistence.learning_repositories import (
    LessonContentSource,
    Postgres_AttemptRepository,
    Postgres_CatalogRepository,
    Postgres_EnrollmentRepository,
    Postgres_LessonRepository,
    Postgres_MasteryRepository,
    Postgres_ProgressRepository,
    Postgres_ReviewRepository,
)
from edu_it_infrastructure.persistence.path_recommendations import (
    Postgres_PathRecommendationRepository,
)
from edu_it_infrastructure.persistence.personal_data import Postgres_PersonalDataGateway
from edu_it_infrastructure.persistence.problem_reports import Postgres_ProblemReportRepository
from edu_it_infrastructure.persistence.repositories import (
    Postgres_RoleAssignmentRepository,
    Postgres_SessionRepository,
    Postgres_UserRepository,
)
from edu_it_infrastructure.persistence.variants import (
    Postgres_ReviewTaskRepository,
    Postgres_VariantRepository,
)
from edu_it_infrastructure.project_reviewer import Pending_ProjectReviewer
from edu_it_infrastructure.telemetry import RequestIdTaskQueue
from fastapi import APIRouter

from edu_it_api.accounts import AccountServices, build_account_router, export_key
from edu_it_api.content_routes import ContentServices, build_content_router
from edu_it_api.identity import SessionUserResolver
from edu_it_api.labs import LabServices, build_labs_router
from edu_it_api.learning import LearningServices, build_learning_router
from edu_it_api.learning_lists import ListServices, build_list_router
from edu_it_api.oauth import OAuthServices, build_oauth_router
from edu_it_api.path_recommendations import (
    PathRecommendationServices,
    build_path_recommendations_router,
)
from edu_it_api.problem_reports import ProblemReportServices, build_problem_reports_router
from edu_it_api.review_tasks import ReviewTaskServices, build_review_tasks_router
from edu_it_api.session_cookie import CookieSettings
from edu_it_api.startup import check_github_oauth_settings, refuse_start

__all__ = [
    "ApiAdapters",
    "ApiRouters",
    "build_adapters",
    "build_api_routers",
    "decode_result",
    "encode_result",
]

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
        "capacity_attempts": s.capacity_attempts,
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
        capacity_attempts=int(d.get("capacity_attempts", 0)),  # type: ignore[call-overload]
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


# -- Adapters -------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ApiAdapters:
    """Every port implementation the API routers are built from."""

    settings: CompositionSettings
    fakes: bool
    provider_name: str
    clock: Clock
    ids: IdGenerator
    mailer: Mailer
    users: UserRepository
    sessions: SessionRepository
    session_store: SessionStore
    rate_limiter: SignInRateLimiter
    credentials: CredentialStore
    verification_links: VerificationLinkRepository
    reset_links: ResetLinkRepository
    password_hasher: Callable[[str], str]
    link_builder: LinkBuilder
    catalog: CatalogRepository
    enrollments: EnrollmentRepository
    progress: ProgressRepository
    mastery: MasteryRepository
    reviews: ReviewRepository
    attempts: AttemptRepository
    lessons_factory: Callable[[LessonContentSource], LessonRepository]
    attempt_idempotency: IdempotencyStore
    lab_idempotency: IdempotencyStore
    report_idempotency: IdempotencyStore
    project_reviewer: ProjectReviewer
    lab_sessions: LabSessionRepository
    grading_runs: GradingRunRepository
    task_queue: TaskQueue
    sandbox: SandboxProvider
    redis: object | None
    """The shared Redis client; ``None`` with fakes (typed ``object`` to keep ``redis`` out)."""
    oauth_links: OAuthLinkRepository
    pending_oauth_links: PendingOAuthLinkRepository
    object_storage: ObjectStorage
    personal_data: PersonalDataGateway
    variants: VariantRepository
    review_tasks: ReviewTaskRepository
    item_flags: ItemFlagWriter
    observations: ObservationRecorder
    problem_reports: ProblemReportRepository
    path_recommendations: PathRecommendationRepository
    roles: RoleAssignmentRepository
    oauth_client: GitHubOAuthClient | None = None
    """The GitHub OAuth client; ``None`` when OAuth is not configured (routes not mounted)."""


def _built[T](name: str, factory: Callable[[], T]) -> T:
    """Build one adapter; on failure log its ``name`` (never a secret) and stop the start."""
    try:
        return factory()
    except Exception as exc:
        raise refuse_start(name, exc) from exc


def build_adapters(
    settings: CompositionSettings, *, fakes: bool, force_oauth: bool = False
) -> ApiAdapters:
    """Build the adapters for ``settings``; ``fakes=True`` uses in-memory fakes only.

    GitHub OAuth is on when all three ``GITHUB_OAUTH_*`` settings are set and off when none
    is; a partial configuration refuses to start (Req 1.9, 1.10). ``force_oauth`` (the
    OpenAPI export) turns it on with ``Fake_GitHubOAuthClient`` whatever the settings.
    """
    configured = check_github_oauth_settings(
        settings.github_client_id, settings.github_client_secret, settings.github_callback_url
    )
    oauth_client: GitHubOAuthClient | None = None
    if force_oauth or (fakes and configured):
        oauth_client = Fake_GitHubOAuthClient()
    elif configured:
        oauth_client = _built("github_oauth", lambda: _urllib_oauth_client(settings))
    adapters = _fake_adapters(settings) if fakes else _real_adapters(settings)
    return replace(adapters, oauth_client=oauth_client)


def _urllib_oauth_client(settings: CompositionSettings) -> GitHubOAuthClient:
    assert settings.github_client_id and settings.github_client_secret
    assert settings.github_callback_url
    return Urllib_GitHubOAuthClient(
        settings.github_client_id, settings.github_client_secret, settings.github_callback_url
    )


def _fake_adapters(settings: CompositionSettings) -> ApiAdapters:
    clock = build_fake_clock()
    lessons = In_Memory_LessonRepository()
    runs = In_Memory_GradingRunRepository()
    variants = In_Memory_VariantRepository(clock)
    item_quality = In_Memory_ItemQualityRepository()
    return ApiAdapters(
        settings=settings,
        fakes=True,
        provider_name="fake",
        clock=clock,
        ids=Sequential_IdGenerator("id"),
        mailer=Console_Mailer(),
        users=In_Memory_UserRepository(),
        sessions=In_Memory_SessionRepository(),
        session_store=In_Memory_SessionStore(clock),
        rate_limiter=In_Memory_SignInRateLimiter(clock),
        credentials=In_Memory_CredentialStore(),
        verification_links=In_Memory_VerificationLinkRepository(),
        reset_links=In_Memory_ResetLinkRepository(),
        # The in-memory credential store compares the stored secret with the raw
        # password, so the fake composition stores it unchanged; never used with real data.
        password_hasher=lambda raw: raw,
        link_builder=Web_LinkBuilder(settings.web_base_url),
        catalog=In_Memory_CatalogRepository(),
        enrollments=In_Memory_EnrollmentRepository(),
        progress=In_Memory_ProgressRepository(),
        mastery=In_Memory_MasteryRepository(),
        reviews=In_Memory_ReviewRepository(),
        attempts=In_Memory_AttemptRepository(),
        lessons_factory=lambda _source: lessons,
        attempt_idempotency=In_Memory_IdempotencyStore(),
        lab_idempotency=In_Memory_IdempotencyStore(),
        report_idempotency=In_Memory_IdempotencyStore(),
        project_reviewer=Fake_ProjectReviewer(pending=True),
        lab_sessions=In_Memory_LabSessionRepository(runs),
        grading_runs=runs,
        task_queue=RequestIdTaskQueue(In_Memory_TaskQueue()),
        sandbox=build_sandbox_provider(
            settings.model_copy(update={"sandbox_provider": SandboxBackend.FAKE}), clock=clock
        ),
        redis=None,
        oauth_links=In_Memory_OAuthLinkRepository(),
        pending_oauth_links=In_Memory_PendingOAuthLinkRepository(),
        object_storage=In_Memory_ObjectStorage(),
        personal_data=In_Memory_PersonalDataGateway(),
        variants=variants,
        review_tasks=In_Memory_ReviewTaskRepository(clock, variants),
        item_flags=item_quality,
        observations=item_quality,
        problem_reports=In_Memory_ProblemReportRepository(),
        path_recommendations=In_Memory_PathRecommendationRepository(),
        roles=In_Memory_RoleAssignmentRepository(),
    )


def _real_adapters(settings: CompositionSettings) -> ApiAdapters:
    url = settings.database_url
    clock = System_Clock()
    redis = _built("redis", lambda: redis_from_url(settings.redis_url))
    hasher = _built("password_hasher", Scrypt_PasswordHasher)
    item_quality = Postgres_ItemQualityRepository(url, clock)
    return ApiAdapters(
        settings=settings,
        fakes=False,
        provider_name=settings.sandbox_provider.value,
        clock=clock,
        ids=Uuid_IdGenerator(),
        mailer=_built("mailer", lambda: build_mailer(settings)),
        users=Postgres_UserRepository(url),
        sessions=Postgres_SessionRepository(url),
        session_store=Redis_SessionStore(redis, clock),
        rate_limiter=Redis_SignInRateLimiter(redis, clock),
        credentials=Postgres_CredentialStore(url, hasher),
        verification_links=Postgres_VerificationLinkRepository(url),
        reset_links=Postgres_ResetLinkRepository(url),
        password_hasher=hasher,
        link_builder=_built("link_builder", lambda: Web_LinkBuilder(settings.web_base_url)),
        catalog=Postgres_CatalogRepository(url),
        enrollments=Postgres_EnrollmentRepository(url),
        progress=Postgres_ProgressRepository(url),
        mastery=Postgres_MasteryRepository(url),
        reviews=Postgres_ReviewRepository(url),
        attempts=Postgres_AttemptRepository(url),
        lessons_factory=lambda source: Postgres_LessonRepository(url, source),
        attempt_idempotency=Redis_IdempotencyStore(
            redis,
            encode=encode_attempt_result,
            decode=decode_attempt_result,
            namespace="edu_it:attempt-idempotency",
        ),
        lab_idempotency=Redis_IdempotencyStore(redis, encode=encode_result, decode=decode_result),
        report_idempotency=Redis_IdempotencyStore(
            redis,
            encode=encode_problem_report_result,
            decode=decode_problem_report_result,
            namespace="edu_it:problem-report-idempotency",
        ),
        project_reviewer=Pending_ProjectReviewer(),
        lab_sessions=Postgres_LabSessionRepository(url),
        grading_runs=Postgres_GradingRunRepository(url),
        task_queue=RequestIdTaskQueue(Redis_TaskQueue(redis)),
        sandbox=_built("sandbox_provider", lambda: build_sandbox_provider(settings, clock=clock)),
        redis=redis,
        oauth_links=Postgres_OAuthLinkRepository(url),
        pending_oauth_links=Postgres_PendingOAuthLinkRepository(url),
        object_storage=_built("object_storage", lambda: build_object_storage(settings)),
        personal_data=Postgres_PersonalDataGateway(url),
        variants=Postgres_VariantRepository(url, clock),
        review_tasks=Postgres_ReviewTaskRepository(url, clock),
        item_flags=item_quality,
        observations=item_quality,
        problem_reports=Postgres_ProblemReportRepository(url),
        path_recommendations=Postgres_PathRecommendationRepository(url),
        roles=Postgres_RoleAssignmentRepository(url),
    )


# -- Routers --------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ApiRouters:
    """The routers for the ``/v1`` sub-application and what the server drains on shutdown."""

    v1_routers: tuple[APIRouter, ...]
    resolver: SessionUserResolver
    registry: TerminalRegistry
    lab_router: APIRouter
    oauth_router: APIRouter | None = None
    """The GitHub OAuth router; ``None`` (not mounted) when OAuth is not configured."""


def _lesson_source(store: Any, content_root: Path) -> LessonContentSource:
    """Lesson titles from the synced store; bodies from ``mdx/{id}.{locale}.mdx`` if present."""
    titles: dict[str, dict[str, str]] = {}
    for item in store.list_items("lesson"):
        payload = json.loads(item.payload)
        raw = payload.get("title")
        if isinstance(raw, Mapping):
            titles[item.stable_id] = {str(k): str(v) for k, v in raw.items()}

    def source(stable_id: str) -> tuple[Mapping[str, str], Mapping[str, str]]:
        lesson_titles = titles.get(stable_id, {})
        bodies: dict[str, str] = {}
        for locale, title in lesson_titles.items():
            path = content_root / "mdx" / f"{stable_id}.{locale}.mdx"
            bodies[locale] = path.read_text(encoding="utf-8") if path.is_file() else f"# {title}"
        return lesson_titles, bodies

    return source


def build_api_routers(
    adapters: ApiAdapters,
    *,
    content_root: str | Path,
    config: object = domain_config,
) -> ApiRouters:
    """Build every ``/v1`` router from ``adapters``; reads no environment variable."""
    root = Path(content_root)
    store, _report = load_synced_store(root)
    clock, ids = adapters.clock, adapters.ids
    labs: LabCatalog = Content_LabCatalog(store)
    text = Content_StoreTextSource(store)
    titles = Content_StoreTitleReader(store)
    resolver = SessionUserResolver(adapters.session_store)

    def new_token() -> str:
        return secrets.token_urlsafe(32)

    cookies = CookieSettings(
        environment=adapters.settings.environment,
        domain=adapters.settings.cookie_domain,
        max_age=domain_config.SESSION_DURATION,
    )
    account = build_account_router(
        AccountServices(
            resolver=resolver,
            cookies=cookies,
            sign_up=SignUpUseCase(
                users=adapters.users,
                credentials=adapters.credentials,
                verification_links=adapters.verification_links,
                link_builder=adapters.link_builder,
                mailer=adapters.mailer,
                clock=clock,
                token_factory=new_token,
                stable_id_factory=ids.new_id,
                password_hasher=adapters.password_hasher,
            ),
            verify_email=VerifyEmailUseCase(
                users=adapters.users, verification_links=adapters.verification_links, clock=clock
            ),
            resend=ResendVerificationUseCase(
                users=adapters.users,
                verification_links=adapters.verification_links,
                link_builder=adapters.link_builder,
                mailer=adapters.mailer,
                clock=clock,
                token_factory=new_token,
            ),
            sign_in=AuthenticateUseCase(
                users=adapters.users,
                credentials=adapters.credentials,
                sessions=adapters.sessions,
                session_store=adapters.session_store,
                rate_limiter=adapters.rate_limiter,
                clock=clock,
                session_ref_factory=new_token,
            ),
            sign_out=SignOutUseCase(
                sessions=adapters.sessions, session_store=adapters.session_store, clock=clock
            ),
            authenticate_session=AuthenticateSessionUseCase(session_store=adapters.session_store),
            request_reset=RequestPasswordResetUseCase(
                users=adapters.users,
                reset_links=adapters.reset_links,
                link_builder=adapters.link_builder,
                mailer=adapters.mailer,
                clock=clock,
                token_factory=new_token,
            ),
            complete_reset=CompletePasswordResetUseCase(
                reset_links=adapters.reset_links,
                credentials=adapters.credentials,
                sessions=adapters.sessions,
                session_store=adapters.session_store,
                clock=clock,
                password_hasher=adapters.password_hasher,
                session_ref_factory=new_token,
            ),
            export_data=ExportUserDataUseCase(
                personal_data=adapters.personal_data,
                object_storage=adapters.object_storage,
                key_factory=export_key,
            ),
            object_storage=adapters.object_storage,
            delete_account=DeleteAccountUseCase(
                personal_data=adapters.personal_data,
                sessions=adapters.sessions,
                session_store=adapters.session_store,
                clock=clock,
                lab_sessions=adapters.lab_sessions,
                sandbox=adapters.sandbox,
                sandbox_provider_name=adapters.provider_name,
            ),
        )
    )

    lessons = adapters.lessons_factory(_lesson_source(store, root))
    # Learners only ever see unflagged questions and Approved variants (ai-content 14.1, 20.1).
    servable = ServableCatalog(adapters.catalog, adapters.item_flags, adapters.variants)
    learning = build_learning_router(
        LearningServices(
            current_user=resolver,
            enroll=EnrollUseCase(
                catalog=adapters.catalog,
                enrollments=adapters.enrollments,
                progress=adapters.progress,
                mastery=adapters.mastery,
                clock=clock,
                ids=ids,
            ),
            progress=GetProgressUseCase(
                titles=titles,
                catalog=adapters.catalog,
                enrollments=adapters.enrollments,
                progress=adapters.progress,
                mastery=adapters.mastery,
                labs=labs,
                lab_completions=adapters.grading_runs,  # type: ignore[arg-type]
                lessons=lessons if isinstance(lessons, ModuleLessonReader) else None,
            ),
            lessons=GetLessonUseCase(lessons=lessons, labs=labs),
            attempt_quiz=AttemptQuizUseCase(
                catalog=servable,
                attempts=adapters.attempts,
                mastery=adapters.mastery,
                reviews=adapters.reviews,
                idempotency=adapters.attempt_idempotency,
                clock=clock,
                ids=ids,
                observations=adapters.observations,
            ),
            reveal_hint=RevealHintUseCase(catalog=servable, mastery=adapters.mastery, clock=clock),
            start_review=StartReviewUseCase(
                catalog=servable, reviews=adapters.reviews, clock=clock, resolver=servable
            ),
            submit_review=SubmitReviewUseCase(
                catalog=servable,
                reviews=adapters.reviews,
                mastery=adapters.mastery,
                clock=clock,
                resolver=servable,
                observations=adapters.observations,
            ),
            submit_project=SubmitProjectUseCase(
                catalog=adapters.catalog,
                attempts=adapters.attempts,
                reviewer=adapters.project_reviewer,
                ids=ids,
            ),
        )
    )
    codec = CursorCodec(clock)
    lists = build_list_router(
        ListServices(
            current_user=resolver,
            list_enrollments=ListEnrollmentsUseCase(
                enrollments=adapters.enrollments,  # type: ignore[arg-type]
                codec=codec,
            ),
            list_quiz_attempts=ListQuizAttemptsUseCase(
                attempts=adapters.attempts,  # type: ignore[arg-type]
                codec=codec,
            ),
            list_tracks=ListTracksUseCase(titles=titles, codec=codec),
        )
    )
    content = build_content_router(
        ContentServices(
            current_user=resolver,
            get_quiz=GetQuizUseCase(catalog=servable, text=text),
            get_project=GetProjectUseCase(catalog=adapters.catalog, text=text),
        )
    )

    end = EndSessionUseCase(sessions=adapters.lab_sessions, queue=adapters.task_queue, clock=clock)
    start = StartLabUseCase(
        sessions=adapters.lab_sessions,
        catalog=labs,
        queue=adapters.task_queue,
        idempotency=adapters.lab_idempotency,
        clock=clock,
        ids=ids,
    )
    gateway = TerminalGatewayCore(
        sessions=adapters.lab_sessions,
        sandbox=adapters.sandbox,
        clock=clock,
        provider_name=adapters.provider_name,
    )
    lab_router = build_labs_router(
        LabServices(
            current_user=resolver,
            catalog=labs,
            start=start,
            reset=ResetLabUseCase(sessions=adapters.lab_sessions, end=end, start=start),
            end=end,
            get_session=GetSessionUseCase(sessions=adapters.lab_sessions),
            list_sessions=ListSessionsUseCase(sessions=adapters.lab_sessions),
            save_files=SaveFilesUseCase(
                sessions=adapters.lab_sessions,
                catalog=labs,
                sandbox=adapters.sandbox,
                clock=clock,
                provider_name=adapters.provider_name,
            ),
            reveal_hint=RevealLabHintUseCase(
                sessions=adapters.lab_sessions,
                catalog=labs,
                mastery=adapters.mastery,
                clock=clock,
            ),
            hint_text=hint_text_for(store),
            cursor_codec=codec,
            clock=clock,
            config=config,
            request_grading=RequestGradingUseCase(
                sessions=adapters.lab_sessions,
                runs=adapters.grading_runs,
                queue=adapters.task_queue,
                idempotency=adapters.lab_idempotency,
                clock=clock,
                ids=ids,
            ),
            runs=adapters.grading_runs,
            feedback_messages=feedback_messages_for(store),
            authorize_terminal=AuthorizeTerminal(
                sessions=adapters.lab_sessions,
                session_store=adapters.session_store,
                clock=clock,
                # The same allowlist as CORS: the Web_App origin of WEB_BASE_URL.
                allowed_origins=adapters.settings.allowed_web_origins,
            ),
            terminal_gateway=gateway,
            session_cookie_name=resolver.cookie_name,
        )
    )
    review_tasks = build_review_tasks_router(
        ReviewTaskServices(
            current_user=resolver,
            list_tasks=ListReviewTasks(
                tasks=adapters.review_tasks, roles=adapters.roles, codec=codec
            ),
            actions=ReviewTaskActions(
                tasks=adapters.review_tasks,
                variants=adapters.variants,
                roles=adapters.roles,
                clock=clock,
                ids=ids,
                flags=adapters.item_flags,
            ),
        )
    )
    problem_reports = build_problem_reports_router(
        ProblemReportServices(
            current_user=resolver,
            submit=SubmitProblemReport(
                reports=adapters.problem_reports,
                idempotency=adapters.report_idempotency,
                clock=clock,
                ids=ids,
                on_stored=lambda item: enqueue_item_evaluation(adapters.task_queue, item),
            ),
        )
    )
    path_recommendations = build_path_recommendations_router(
        PathRecommendationServices(
            current_user=resolver,
            get=GetRecommendation(
                recommendations=adapters.path_recommendations,
                eligible=EligibleItems(servable, adapters.mastery),
            ),
            dismiss=DismissRecommendation(
                recommendations=adapters.path_recommendations, clock=clock
            ),
        )
    )
    oauth_router = _build_oauth_router(adapters, cookies, new_token)
    return ApiRouters(
        v1_routers=(
            account,
            learning,
            lists,
            content,
            lab_router,
            review_tasks,
            problem_reports,
            path_recommendations,
            *(() if oauth_router is None else (oauth_router,)),
        ),
        resolver=resolver,
        registry=gateway.registry,
        lab_router=lab_router,
        oauth_router=oauth_router,
    )


def _build_oauth_router(
    adapters: ApiAdapters, cookies: CookieSettings, new_token: Callable[[], str]
) -> APIRouter | None:
    """Build the GitHub OAuth router (Req 1.9, 6); ``None`` leaves it unmounted.

    Without an ``oauth_client`` nothing is mounted, so a request under ``/v1/auth/github``
    gets the generic 404 Problem_Details.
    """
    if adapters.oauth_client is None:
        return None
    clock = adapters.clock
    return build_oauth_router(
        OAuthServices(
            client=adapters.oauth_client,
            cookies=cookies,
            web_base_url=adapters.settings.web_base_url,
            complete=CompleteOAuthCallbackUseCase(
                users=adapters.users,
                oauth_links=adapters.oauth_links,
                pending_links=adapters.pending_oauth_links,
                sessions=adapters.sessions,
                session_store=adapters.session_store,
                clock=clock,
                stable_id_factory=adapters.ids.new_id,
                pending_token_factory=new_token,
                session_ref_factory=new_token,
            ),
            confirm=ConfirmOAuthLinkUseCase(
                pending_links=adapters.pending_oauth_links,
                oauth_links=adapters.oauth_links,
                sessions=adapters.sessions,
                session_store=adapters.session_store,
                clock=clock,
                session_ref_factory=new_token,
            ),
            decline=DeclineOAuthLinkUseCase(
                pending_links=adapters.pending_oauth_links, clock=clock
            ),
        )
    )
