"""Lab session use cases (hands-on-labs)."""

from __future__ import annotations

from edu_it_application.labs.end_session import DESTROY_JOB, EndSessionUseCase, destroy_dedupe_key
from edu_it_application.labs.errors import (
    ActiveSessionConflictError,
    GradingRejectedError,
    HintNotAvailableError,
    HintOutOfOrderError,
    LabError,
    LabFilesRejectedError,
    LabNotFoundError,
    LabProvisionRateLimitedError,
    LabSessionNotFoundError,
    LabSessionNotReadyError,
    LabTaskNotFoundError,
)
from edu_it_application.labs.grade_run import GradeRun, RecordCheckResult, load_expected_cases
from edu_it_application.labs.provision_sandbox import (
    ProvisionOutcome,
    ProvisionSandbox,
    SetupScriptResolver,
    setup_script_argv,
)
from edu_it_application.labs.purge_lab_data import PurgeLabData
from edu_it_application.labs.queries import GetSessionUseCase, ListSessionsUseCase, SessionPage
from edu_it_application.labs.request_grading import (
    GRADE_JOB,
    RequestGradingResult,
    RequestGradingUseCase,
    grade_dedupe_key,
)
from edu_it_application.labs.reveal_hint import HintGrant, RevealLabHintUseCase
from edu_it_application.labs.save_files import SaveFilesUseCase, sandbox_handle
from edu_it_application.labs.start_lab import (
    PROVISION_JOB,
    ResetLabUseCase,
    StartLabResult,
    StartLabUseCase,
    provision_dedupe_key,
)

__all__ = [
    "DESTROY_JOB",
    "GRADE_JOB",
    "PROVISION_JOB",
    "ActiveSessionConflictError",
    "EndSessionUseCase",
    "GetSessionUseCase",
    "GradeRun",
    "GradingRejectedError",
    "HintGrant",
    "HintNotAvailableError",
    "HintOutOfOrderError",
    "LabError",
    "LabFilesRejectedError",
    "LabNotFoundError",
    "LabProvisionRateLimitedError",
    "LabSessionNotFoundError",
    "LabSessionNotReadyError",
    "LabTaskNotFoundError",
    "ListSessionsUseCase",
    "ProvisionOutcome",
    "ProvisionSandbox",
    "PurgeLabData",
    "RecordCheckResult",
    "RequestGradingResult",
    "RequestGradingUseCase",
    "ResetLabUseCase",
    "RevealLabHintUseCase",
    "SaveFilesUseCase",
    "SessionPage",
    "SetupScriptResolver",
    "StartLabResult",
    "StartLabUseCase",
    "destroy_dedupe_key",
    "grade_dedupe_key",
    "load_expected_cases",
    "provision_dedupe_key",
    "sandbox_handle",
    "setup_script_argv",
]

from edu_it_application.labs.reap_sessions import (  # noqa: E402
    DestroyOutcome,
    DestroySandbox,
    ReapReport,
    ReapSessions,
)

__all__ += ["DestroyOutcome", "DestroySandbox", "ReapReport", "ReapSessions"]

from edu_it_application.labs.terminal import (  # noqa: E402
    AuthorizeTerminal,
    Frame,
    SocketPort,
    TerminalAuthorization,
    TerminalGatewayCore,
    TerminalRefusal,
    TerminalRegistry,
)

__all__ += [
    "AuthorizeTerminal",
    "Frame",
    "SocketPort",
    "TerminalAuthorization",
    "TerminalGatewayCore",
    "TerminalRefusal",
    "TerminalRegistry",
]
