"""The ``ProvisionSandbox`` worker job logic (Req 6.2, 6.3, 6.6, 7.6, 25.1-25.3).

One delivery of ``lab.provision``: claim an attempt by CAS on ``provision_attempts``,
build the hardened spec, ``provision`` under ``LAB_PROVISION_TIMEOUT``, copy the lab's
setup script in and run it as the learner, remove it, then CAS
``provisioning -> ready`` recording ``sandbox_ref``.

On failure any partial handle is destroyed and ``next_provision_at`` is set to
``now + LAB_PROVISION_MIN_INTERVAL`` (10 s); the third failed attempt sets ``failed``.
A duplicate delivery sees a different ``provision_attempts`` or a non-``provisioning``
status and exits without touching the provider.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Final

from edu_it_domain import config as domain_config
from edu_it_domain.labs.models import SessionStatus
from edu_it_domain.labs.sandbox_spec import (
    LEARNER_USER,
    TMP_PATH,
    WORKSPACE_PATH,
    build_sandbox_spec,
)
from edu_it_domain.ports.sandbox import (
    ProvisionError,
    ProvisionTimeout,
    SandboxError,
    SandboxHandle,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from edu_it_domain.labs.models import StableId
    from edu_it_domain.ports.clock import Clock
    from edu_it_domain.ports.lab_catalog import LabCatalog, LabDefinition
    from edu_it_domain.ports.lab_sessions import LabSessionRepository
    from edu_it_domain.ports.sandbox import SandboxProvider

__all__ = [
    "ProvisionOutcome",
    "ProvisionSandbox",
    "SetupScriptResolver",
    "setup_script_argv",
]

_LOG = logging.getLogger(__name__)

#: Absolute paths and argv forms recorded in ``lab-images/README.md`` (task 3.4).
_TIMEOUT: Final = "/usr/bin/timeout"
_BASH: Final = "/bin/bash"
_RM: Final = "/bin/rm"
_SETUP_DEST: Final = f"{TMP_PATH}/setup.sh"

type SetupScriptResolver = Callable[[LabDefinition], Path | None]
"""Maps a lab to the host path of its setup script, or ``None`` when it has none."""


def setup_script_argv(timeout_s: float) -> list[str]:
    """Run the copied setup script with a hard deadline (``timeout -s KILL``)."""
    return [_TIMEOUT, "-s", "KILL", f"{max(timeout_s, 1.0):.0f}", _BASH, _SETUP_DEST]


class ProvisionOutcome(Enum):
    """What one delivery of the job did."""

    READY = "ready"
    RETRY_SCHEDULED = "retry-scheduled"
    FAILED = "failed"
    SKIPPED = "skipped"
    """Duplicate delivery, session gone or no longer provisioning, or not yet due."""


class ProvisionSandbox:
    """Provision the sandbox of one ``provisioning`` Lab_Session."""

    def __init__(
        self,
        *,
        sessions: LabSessionRepository,
        catalog: LabCatalog,
        sandbox: SandboxProvider,
        clock: Clock,
        setup_scripts: SetupScriptResolver | None = None,
        config: object = domain_config,
    ) -> None:
        """Wire the ports the job orchestrates."""
        self._sessions = sessions
        self._catalog = catalog
        self._sandbox = sandbox
        self._clock = clock
        self._setup_scripts = setup_scripts
        self._config = config

    def execute(self, session_id: StableId) -> ProvisionOutcome:
        """Run one provisioning attempt for ``session_id``."""
        session = self._sessions.get(session_id)
        if session is None or session.status is not SessionStatus.PROVISIONING:
            return ProvisionOutcome.SKIPPED
        now = self._clock.now()
        if session.next_provision_at is not None and now < session.next_provision_at:
            return ProvisionOutcome.SKIPPED

        max_attempts: int = self._config.LAB_PROVISION_MAX_RETRIES + 1  # type: ignore[attr-defined]
        if session.provision_attempts >= max_attempts:
            # Attempts exhausted by an earlier delivery that died before marking failed.
            self._fail(session_id, now)
            return ProvisionOutcome.FAILED
        if not self._sessions.claim_provision_attempt(
            session_id, expected_attempts=session.provision_attempts, now=now
        ):
            return ProvisionOutcome.SKIPPED
        attempt = session.provision_attempts + 1

        handle: SandboxHandle | None = None
        try:
            lab = self._catalog.get(session.lab_id)
            if lab is None:
                raise ProvisionError(f"lab {session.lab_id} is not in the catalog")
            started = self._clock.now()
            timeout: timedelta = self._config.LAB_PROVISION_TIMEOUT  # type: ignore[attr-defined]
            handle = self._sandbox.provision(build_sandbox_spec(lab, session_id))
            self._run_setup(handle, lab, started, timeout)
        except SandboxError as exc:
            if isinstance(exc, ProvisionError) and exc.partial_handle is not None:
                handle = exc.partial_handle
            return self._on_failure(session_id, handle, attempt, max_attempts, exc)
        except Exception as exc:  # unexpected: never leak a sandbox
            return self._on_failure(session_id, handle, attempt, max_attempts, exc)

        won = self._sessions.transition(
            session_id,
            expected={SessionStatus.PROVISIONING},
            to=SessionStatus.READY,
            sandbox_ref=handle.sandbox_id,
            last_activity_at=self._clock.now(),
        )
        if not won:  # ended or expired while provisioning: nobody owns this sandbox
            self._destroy(handle)
            return ProvisionOutcome.SKIPPED
        return ProvisionOutcome.READY

    # -- steps ---------------------------------------------------------------

    def _run_setup(
        self, handle: SandboxHandle, lab: LabDefinition, started: datetime, timeout: timedelta
    ) -> None:
        self._check_budget(started, timeout)
        script = self._setup_scripts(lab) if self._setup_scripts is not None else None
        if script is not None:
            self._sandbox.copy_in(handle, Path(script), _SETUP_DEST, as_user=LEARNER_USER)
            remaining = self._remaining(started, timeout)
            result = self._sandbox.exec(
                handle,
                setup_script_argv(remaining),
                as_user=LEARNER_USER,
                env={},
                cwd=WORKSPACE_PATH,
                timeout_s=remaining,
            )
            self._sandbox.exec(
                handle,
                [_RM, "-f", _SETUP_DEST],
                as_user=LEARNER_USER,
                env={},
                cwd=WORKSPACE_PATH,
                timeout_s=5.0,
            )
            if result.timed_out or result.exit_code != 0:
                raise ProvisionError(f"setup script failed (exit {result.exit_code})")
        self._check_budget(started, timeout)

    def _remaining(self, started: datetime, timeout: timedelta) -> float:
        return (started + timeout - self._clock.now()).total_seconds()

    def _check_budget(self, started: datetime, timeout: timedelta) -> None:
        if self._remaining(started, timeout) <= 0:
            raise ProvisionTimeout("provisioning exceeded LAB_PROVISION_TIMEOUT")

    def _on_failure(
        self,
        session_id: StableId,
        handle: SandboxHandle | None,
        attempt: int,
        max_attempts: int,
        exc: Exception,
    ) -> ProvisionOutcome:
        _LOG.warning(
            "lab provisioning attempt %d/%d failed for session %s: %s",
            attempt,
            max_attempts,
            session_id,
            type(exc).__name__,
        )
        if handle is not None:
            self._destroy(handle)
        now = self._clock.now()
        if attempt >= max_attempts:
            self._fail(session_id, now)
            return ProvisionOutcome.FAILED
        interval: timedelta = self._config.LAB_PROVISION_MIN_INTERVAL  # type: ignore[attr-defined]
        self._sessions.transition(
            session_id,
            expected={SessionStatus.PROVISIONING},
            to=SessionStatus.PROVISIONING,
            next_provision_at=now + interval,
        )
        return ProvisionOutcome.RETRY_SCHEDULED

    def _fail(self, session_id: StableId, now: datetime) -> None:
        self._sessions.transition(
            session_id,
            expected={SessionStatus.PROVISIONING},
            to=SessionStatus.FAILED,
            ended_at=now,
        )

    def _destroy(self, handle: SandboxHandle) -> None:
        try:
            self._sandbox.destroy(handle)
        except Exception:  # the reaper retries orphans; never mask the original outcome
            _LOG.warning("destroying sandbox %s failed", handle.sandbox_id, exc_info=True)
