"""Application-layer errors raised by the lab session use cases.

Plain exceptions carrying the structured facts the ``apps/api`` boundary needs to
render translated RFC 9457 problems (task 7.1). The messages are diagnostics for
logs, never shown to learners. Standard library and domain imports only.
"""

from __future__ import annotations

from datetime import timedelta

from edu_it_domain.labs.files import FileViolation
from edu_it_domain.labs.models import SessionStatus, StableId
from edu_it_domain.labs.session_rules import RejectionReason

__all__ = [
    "ActiveSessionConflictError",
    "GradingRejectedError",
    "HintNotAvailableError",
    "HintOutOfOrderError",
    "LabError",
    "LabFilesRejectedError",
    "LabNotFoundError",
    "LabProvisionRateLimitedError",
    "LabSessionNotFoundError",
    "LabSessionNotReadyError",
    "LabTaskNotFoundError",
]


class LabError(Exception):
    """Base class of every lab session use-case failure."""


class LabNotFoundError(LabError):
    """The requested lab is unknown or archived (404)."""

    def __init__(self, lab_id: StableId) -> None:
        """Record the lab id."""
        super().__init__(f"lab {lab_id!r} not found")
        self.lab_id = lab_id


class LabSessionNotFoundError(LabError):
    """The session is absent or owned by someone else; the two are indistinguishable (404)."""

    def __init__(self, session_id: StableId) -> None:
        """Record the session id."""
        super().__init__(f"lab session {session_id!r} not found")
        self.session_id = session_id


class LabFilesRejectedError(LabError):
    """The submitted files violate the caps or the allowed names (422, Req 14.6, 14.7).

    Nothing was copied into the sandbox.
    """

    def __init__(self, violation: FileViolation, name: str | None = None) -> None:
        """Record the violation and the offending file name, if any."""
        super().__init__(f"learner files rejected: {violation.value} ({name!r})")
        self.violation = violation
        self.name = name


class LabSessionNotReadyError(LabError):
    """The session cannot accept the operation in its current status (409)."""

    def __init__(self, session_id: StableId, status: SessionStatus) -> None:
        """Record the session and its status."""
        super().__init__(f"lab session {session_id!r} is {status.value}")
        self.session_id = session_id
        self.status = status


class GradingRejectedError(LabError):
    """A grading request was rejected by the eligibility rules (Req 11.1, 27.5, 27.7, 30.4).

    ``reason`` selects the API problem type; ``retry_after`` is set when throttled.
    """

    def __init__(self, reason: RejectionReason, retry_after: timedelta | None = None) -> None:
        """Record the rejection reason and the throttle delay."""
        super().__init__(f"grading rejected: {reason.value}")
        self.reason = reason
        self.retry_after = retry_after


class LabTaskNotFoundError(LabError):
    """The task is not part of the session's lab (404)."""

    def __init__(self, task_id: StableId) -> None:
        """Record the task id."""
        super().__init__(f"lab task {task_id!r} not found")
        self.task_id = task_id


class HintNotAvailableError(LabError):
    """The requested hint level does not exist for the task (404)."""

    def __init__(self, task_id: StableId, level: int) -> None:
        """Record the task and the requested level."""
        super().__init__(f"task {task_id!r} has no hint level {level}")
        self.task_id = task_id
        self.level = level


class HintOutOfOrderError(LabError):
    """Hint level k was requested before k-1 was revealed (409, Req 14.2)."""

    def __init__(self, task_id: StableId, level: int, next_level: int) -> None:
        """Record the requested level and the level that may be revealed next."""
        super().__init__(f"task {task_id!r}: level {level} requested, next is {next_level}")
        self.task_id = task_id
        self.level = level
        self.next_level = next_level


class ActiveSessionConflictError(LabError):
    """The learner already has an Active_Session (409, Req 7.1, 7.7, 30.1).

    The existing session is left untouched and no session is created. The
    boundary names the running lab and offers to end it.
    """

    def __init__(
        self,
        *,
        active_session_id: StableId,
        active_lab_id: StableId,
        requested_lab_id: StableId,
    ) -> None:
        """Record the running session and lab, and the lab that was requested."""
        super().__init__(
            f"active session {active_session_id!r} for lab {active_lab_id!r} "
            f"blocks starting lab {requested_lab_id!r}"
        )
        self.active_session_id = active_session_id
        self.active_lab_id = active_lab_id
        self.requested_lab_id = requested_lab_id

    @property
    def same_lab(self) -> bool:
        """Whether the requested lab is the one already running."""
        return self.active_lab_id == self.requested_lab_id


class LabProvisionRateLimitedError(LabError):
    """The learner exceeded the provisioning rate limit (429, Req 7.5, 30.3)."""

    def __init__(self, retry_after: timedelta) -> None:
        """Record how long until another start is allowed (``Retry-After``)."""
        super().__init__(f"lab provisioning rate limited; retry after {retry_after}")
        self.retry_after = retry_after
