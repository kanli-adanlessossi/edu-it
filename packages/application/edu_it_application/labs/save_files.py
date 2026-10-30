"""The ``SaveFiles`` use case (Req 14.6, 14.7, 7.2).

Validates the file count, size and name caps from Config against the lab's
declared learner files, then ``copy_in``s them into the workspace as the learner
user and records learner activity. A rejected set copies nothing (Req 14.7).
A non-owner is indistinguishable from a missing session.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from edu_it_domain import config as domain_config
from edu_it_domain.labs.files import FileLimits, validate_learner_files
from edu_it_domain.labs.models import SessionStatus
from edu_it_domain.labs.sandbox_spec import LEARNER_USER, WORKSPACE_PATH
from edu_it_domain.ports.sandbox import SandboxHandle

from edu_it_application.labs.errors import (
    LabFilesRejectedError,
    LabNotFoundError,
    LabSessionNotFoundError,
    LabSessionNotReadyError,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from edu_it_domain.labs.models import StableId
    from edu_it_domain.ports.clock import Clock
    from edu_it_domain.ports.lab_catalog import LabCatalog
    from edu_it_domain.ports.lab_sessions import LabSessionRepository
    from edu_it_domain.ports.sandbox import SandboxProvider

__all__ = ["SaveFilesUseCase", "sandbox_handle"]


def sandbox_handle(session_id: StableId, sandbox_ref: str, provider_name: str) -> SandboxHandle:
    """Rebuild the provider handle from the session's persisted ``sandbox_ref``."""
    return SandboxHandle(
        sandbox_id=sandbox_ref, session_stable_id=session_id, provider=provider_name
    )


class SaveFilesUseCase:
    """Copy a learner's files into the sandbox workspace."""

    def __init__(
        self,
        *,
        sessions: LabSessionRepository,
        catalog: LabCatalog,
        sandbox: SandboxProvider,
        clock: Clock,
        provider_name: str = "docker",
        config: object = domain_config,
    ) -> None:
        """Wire the ports the use case orchestrates."""
        self._sessions = sessions
        self._catalog = catalog
        self._sandbox = sandbox
        self._clock = clock
        self._provider_name = provider_name
        self._config = config

    def execute(
        self, user_id: StableId, session_id: StableId, files: Mapping[str, str | bytes]
    ) -> int:
        """Save ``files`` for ``user_id``'s session and return how many were saved.

        Raises:
            LabSessionNotFoundError: absent or owned by another learner.
            LabSessionNotReadyError: the session is not ``ready`` (no sandbox to write to,
                or grading has frozen the learner's processes).
            LabNotFoundError: the session's lab is no longer in the catalog.
            LabFilesRejectedError: caps or names violated; nothing was copied.
            SandboxGone: the sandbox vanished underneath the session.
        """
        session = self._sessions.get_for_owner(session_id, user_id)
        if session is None:
            raise LabSessionNotFoundError(session_id)
        if session.status is not SessionStatus.READY or session.sandbox_ref is None:
            raise LabSessionNotReadyError(session_id, session.status)
        lab = self._catalog.get(session.lab_id)
        if lab is None:
            raise LabNotFoundError(session.lab_id)

        limits = FileLimits(
            max_count=self._config.LAB_FILE_MAX_COUNT,  # type: ignore[attr-defined]
            max_bytes=self._config.LAB_FILE_MAX_BYTES,  # type: ignore[attr-defined]
        )
        verdict = validate_learner_files(files, lab.learner_files, limits)
        if not verdict.ok:
            assert verdict.violation is not None
            raise LabFilesRejectedError(verdict.violation, verdict.name)

        if files:
            handle = sandbox_handle(session_id, session.sandbox_ref, self._provider_name)
            with tempfile.TemporaryDirectory() as staging:
                root = Path(staging)
                for name, content in files.items():
                    data = content if isinstance(content, bytes) else content.encode("utf-8")
                    (root / name).write_bytes(data)
                self._sandbox.copy_in(handle, root, WORKSPACE_PATH, as_user=LEARNER_USER)

        self._sessions.touch_activity(session_id, self._clock.now())
        return len(files)
