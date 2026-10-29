"""Fake port implementations for test and development (Req 5.2, 6).

These adapters implement the framework-free domain ports (``edu_it_domain.ports``)
with in-memory / controllable behavior so the domain and application layers can be
exercised without any real external system. Each fake is substitutable for its
production counterpart without changing domain code (Req 5.2).

The controllable :class:`Test_Clock` (Req 6) lives here and is wired only in the
test/dev composition root; production never registers an interface that sets or
advances the clock (Req 6.3).

Alongside it are the in-memory / console fakes for the remaining ports: the
console :class:`Console_Mailer`, which writes mail to standard output and retains
it for inspection (Req 5.3); the in-memory :class:`In_Memory_ObjectStorage` and
:class:`In_Memory_TaskQueue` (``enqueue`` / ``consume`` per ADR 0003); and the
:class:`Fake_LLMProvider`, which makes no network call (Req 5.4).

The learning ports (task 1.4) are faked here too: the configurable
:class:`Fake_ProjectReviewer` (score + pending outcome, Req 10.4, 10.5) and
:class:`Fake_LabCheck` (coverage + pass, Req 1.4), plus the in-memory learning
repositories (``In_Memory_CatalogRepository``, ``In_Memory_EnrollmentRepository``,
``In_Memory_ProgressRepository``, ``In_Memory_MasteryRepository``,
``In_Memory_ReviewRepository``, ``In_Memory_AttemptRepository``) that drive the
Milestone A domain services and Milestone C use cases without a database
(Req 11.4).
"""

from edu_it_infrastructure.fakes.clock import Test_Clock
from edu_it_infrastructure.fakes.idempotency import (
    In_Memory_IdempotencyStore,
    Sequential_IdGenerator,
)
from edu_it_infrastructure.fakes.lab_check import Fake_LabCheck
from edu_it_infrastructure.fakes.learning_repositories import (
    In_Memory_AttemptRepository,
    In_Memory_CatalogRepository,
    In_Memory_EnrollmentRepository,
    In_Memory_MasteryRepository,
    In_Memory_ProgressRepository,
    In_Memory_ReviewRepository,
)
from edu_it_infrastructure.fakes.llm_provider import Fake_LLMProvider
from edu_it_infrastructure.fakes.mailer import Console_Mailer
from edu_it_infrastructure.fakes.object_storage import In_Memory_ObjectStorage
from edu_it_infrastructure.fakes.personal_data import (
    In_Memory_PersonalDataGateway,
    PersonalDataDeletionFailure,
)
from edu_it_infrastructure.fakes.project_reviewer import Fake_ProjectReviewer
from edu_it_infrastructure.fakes.rate_limiter import In_Memory_SignInRateLimiter
from edu_it_infrastructure.fakes.repositories import (
    In_Memory_CredentialStore,
    In_Memory_OAuthLinkRepository,
    In_Memory_PendingOAuthLinkRepository,
    In_Memory_ResetLinkRepository,
    In_Memory_RoleAssignmentRepository,
    In_Memory_SessionRepository,
    In_Memory_UserRepository,
    In_Memory_VerificationLinkRepository,
)
from edu_it_infrastructure.fakes.session_store import In_Memory_SessionStore
from edu_it_infrastructure.fakes.task_queue import In_Memory_TaskQueue, QueuedJob

__all__ = [
    "Console_Mailer",
    "Fake_LLMProvider",
    "Fake_LabCheck",
    "Fake_ProjectReviewer",
    "In_Memory_AttemptRepository",
    "In_Memory_CatalogRepository",
    "In_Memory_CredentialStore",
    "In_Memory_EnrollmentRepository",
    "In_Memory_IdempotencyStore",
    "In_Memory_MasteryRepository",
    "In_Memory_OAuthLinkRepository",
    "In_Memory_ObjectStorage",
    "In_Memory_PendingOAuthLinkRepository",
    "In_Memory_PersonalDataGateway",
    "In_Memory_ProgressRepository",
    "In_Memory_ResetLinkRepository",
    "In_Memory_ReviewRepository",
    "In_Memory_RoleAssignmentRepository",
    "In_Memory_SessionRepository",
    "In_Memory_SessionStore",
    "In_Memory_SignInRateLimiter",
    "In_Memory_TaskQueue",
    "In_Memory_UserRepository",
    "In_Memory_VerificationLinkRepository",
    "PersonalDataDeletionFailure",
    "QueuedJob",
    "Sequential_IdGenerator",
    "Test_Clock",
]
