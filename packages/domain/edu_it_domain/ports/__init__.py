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

from edu_it_domain.ports.clock import Clock
from edu_it_domain.ports.credential_store import CredentialStore
from edu_it_domain.ports.health import DependencyProbe
from edu_it_domain.ports.llm_provider import (
    LLMCompletion,
    LLMProvider,
)
from edu_it_domain.ports.mailer import (
    Mailer,
    OutboundMail,
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
    "PERSONAL_DATA_TABLES",
    "PRIVILEGED_ROLES",
    "PROVIDER_GITHUB",
    "ROLES",
    "ROLE_ADMIN",
    "ROLE_AUTHOR",
    "ROLE_LEARNER",
    "ROLE_REVIEWER",
    "Clock",
    "CredentialStore",
    "DependencyProbe",
    "JobHandler",
    "LLMCompletion",
    "LLMProvider",
    "Mailer",
    "OAuthLink",
    "OAuthLinkRepository",
    "ObjectStorage",
    "OutboundMail",
    "PendingOAuthLink",
    "PendingOAuthLinkRepository",
    "PersonalDataExport",
    "PersonalDataGateway",
    "ResetLink",
    "ResetLinkRepository",
    "ResolvedSession",
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
