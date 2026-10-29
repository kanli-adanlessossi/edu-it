"""Framework-free domain ports (Req 5.1).

Every external system the domain depends on sits behind a port defined here, so
the domain and application layers are testable without any real infrastructure
and each port is substitutable for a fake (Req 5.2). These declarations are the
inner contracts of the ``api -> application -> domain <- infrastructure``
layering; concrete adapters (PostgreSQL, Redis, SMTP, object storage, ...) and
the fakes live in the infrastructure layer and implement these contracts.

The ports are expressed as :class:`typing.Protocol` classes so an
implementation satisfies a port by structural typing, with no inheritance and,
crucially, no web or API framework import (Req 1.8,
``.kiro/steering/structure.md``). The external-system port list from
``.kiro/steering/tech.md`` is ``Clock``, ``Mailer``, ``ObjectStorage``,
``TaskQueue``, and ``LLMProvider``; the identity-persistence ports added for the
account system (ADR 0004) are the ``UserRepository`` / ``SessionRepository``
durable-record ports, the server-side ``SessionStore``, and the
``SignInRateLimiter``.
"""

from edu_it_domain.ports.attempts import AttemptRepository
from edu_it_domain.ports.catalog import (
    CatalogRepository,
    ConceptRecord,
    ModuleRecord,
    QuestionRecord,
    QuizRecord,
)
from edu_it_domain.ports.clock import Clock
from edu_it_domain.ports.credential_store import CredentialStore
from edu_it_domain.ports.enrollment import (
    EnrollmentRecord,
    EnrollmentRepository,
)
from edu_it_domain.ports.health import DependencyProbe
from edu_it_domain.ports.id_generator import IdGenerator
from edu_it_domain.ports.idempotency import (
    IdempotencyReservation,
    IdempotencyStore,
    ReservationStatus,
)
from edu_it_domain.ports.lab_check import LabCheck
from edu_it_domain.ports.llm_provider import (
    LLMCompletion,
    LLMProvider,
)
from edu_it_domain.ports.mailer import (
    Mailer,
    OutboundMail,
)
from edu_it_domain.ports.mastery import (
    HintRevealRecord,
    MasteryRepository,
)
from edu_it_domain.ports.oauth_link import (
    PROVIDER_GITHUB,
    OAuthLink,
    OAuthLinkRepository,
)
from edu_it_domain.ports.object_storage import (
    ObjectStorage,
    StoredObject,
)
from edu_it_domain.ports.pending_oauth_link import (
    PendingOAuthLink,
    PendingOAuthLinkRepository,
)
from edu_it_domain.ports.personal_data import (
    PERSONAL_DATA_TABLES,
    PersonalDataExport,
    PersonalDataGateway,
)
from edu_it_domain.ports.progress import (
    MODULE_STATE_COMPLETED,
    MODULE_STATE_IN_PROGRESS,
    MODULE_STATE_LOCKED,
    MODULE_STATE_UNLOCKED,
    LessonCompletionRecord,
    ModuleProgressRecord,
    ProgressRepository,
)
from edu_it_domain.ports.project_reviewer import ProjectReviewer
from edu_it_domain.ports.rate_limiter import SignInRateLimiter
from edu_it_domain.ports.repository import (
    SessionRecord,
    SessionRepository,
    UserRecord,
    UserRepository,
)
from edu_it_domain.ports.reset_link import (
    ResetLink,
    ResetLinkRepository,
)
from edu_it_domain.ports.review import ReviewRepository
from edu_it_domain.ports.role_assignment import (
    PRIVILEGED_ROLES,
    ROLE_ADMIN,
    ROLE_AUTHOR,
    ROLE_LEARNER,
    ROLE_REVIEWER,
    ROLES,
    RoleAssignment,
    RoleAssignmentRepository,
)
from edu_it_domain.ports.session_store import (
    ResolvedSession,
    SessionStore,
)
from edu_it_domain.ports.task_queue import (
    JobHandler,
    TaskQueue,
)
from edu_it_domain.ports.verification_link import (
    VerificationLink,
    VerificationLinkRepository,
)

__all__ = [
    "MODULE_STATE_COMPLETED",
    "MODULE_STATE_IN_PROGRESS",
    "MODULE_STATE_LOCKED",
    "MODULE_STATE_UNLOCKED",
    "PERSONAL_DATA_TABLES",
    "PRIVILEGED_ROLES",
    "PROVIDER_GITHUB",
    "ROLES",
    "ROLE_ADMIN",
    "ROLE_AUTHOR",
    "ROLE_LEARNER",
    "ROLE_REVIEWER",
    "AttemptRepository",
    "CatalogRepository",
    "Clock",
    "ConceptRecord",
    "CredentialStore",
    "DependencyProbe",
    "EnrollmentRecord",
    "EnrollmentRepository",
    "HintRevealRecord",
    "IdGenerator",
    "IdempotencyReservation",
    "IdempotencyStore",
    "JobHandler",
    "LLMCompletion",
    "LLMProvider",
    "LabCheck",
    "LessonCompletionRecord",
    "Mailer",
    "MasteryRepository",
    "ModuleProgressRecord",
    "ModuleRecord",
    "OAuthLink",
    "OAuthLinkRepository",
    "ObjectStorage",
    "OutboundMail",
    "PendingOAuthLink",
    "PendingOAuthLinkRepository",
    "PersonalDataExport",
    "PersonalDataGateway",
    "ProgressRepository",
    "ProjectReviewer",
    "QuestionRecord",
    "QuizRecord",
    "ReservationStatus",
    "ResetLink",
    "ResetLinkRepository",
    "ResolvedSession",
    "ReviewRepository",
    "RoleAssignment",
    "RoleAssignmentRepository",
    "SessionRecord",
    "SessionRepository",
    "SessionStore",
    "SignInRateLimiter",
    "StoredObject",
    "TaskQueue",
    "UserRecord",
    "UserRepository",
    "VerificationLink",
    "VerificationLinkRepository",
]
