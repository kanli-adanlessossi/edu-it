"""The ``VerifyLab`` use case (ai-content Requirements 8.1 to 8.7).

A generated lab is proven against two states. The Starting_State sandbox must fail every
check; the Reference_Solution sandbox, where the solution script has been applied, must
pass every check. Both sandboxes are provisioned as one unit and destroyed together in a
``finally`` only after both phases finished or failed. Every run is bounded by
``min(check.timeout, USABLE_RUN_BUDGET)`` and every phase by ``LAB_GRADING_RUN_DEADLINE``.

Provisioning, setup, solution and exec failures are findings. Nothing here ever turns a
failure into a silent pass, and the lab draft is never mutated.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from enum import Enum
from typing import TYPE_CHECKING, Final, Protocol

from edu_it_domain import config as domain_config
from edu_it_domain.ai.verification import FindingKind, VerificationFinding
from edu_it_domain.labs.sandbox_spec import LEARNER_USER, WORKSPACE_PATH, build_sandbox_spec
from edu_it_domain.ports.sandbox import ProvisionError

if TYPE_CHECKING:
    from datetime import datetime

    from edu_it_domain.ai.verification import LabCheckUnderVerification, LabUnderVerification
    from edu_it_domain.ports.clock import Clock
    from edu_it_domain.ports.sandbox import ExecResult, SandboxHandle, SandboxProvider

__all__ = ["LabVerifier", "VerifyLab"]

_LOG = logging.getLogger(__name__)

_BASH: Final = "/bin/bash"
_LAB_TARGET: Final = "lab"
_SIGNAL_EXIT_FLOOR: Final = 128


class LabVerifier(Protocol):
    """What ``VerifyItem`` needs to verify a lab."""

    def execute(self, lab: LabUnderVerification) -> tuple[VerificationFinding, ...]:
        """Return every finding for ``lab``; an empty tuple means both phases held."""
        ...


class _Phase(Enum):
    STARTING = "starting_state"
    REFERENCE = "reference_solution"


class _Verdict(Enum):
    PASS = "pass"
    FAIL = "fail"
    INCONCLUSIVE = "inconclusive"


def _verdict(result: ExecResult) -> _Verdict:
    if result.timed_out or result.exit_code < 0 or result.exit_code > _SIGNAL_EXIT_FLOOR:
        return _Verdict.INCONCLUSIVE
    return _Verdict.PASS if result.exit_code == 0 else _Verdict.FAIL


class _Image:
    """Adapter giving ``build_sandbox_spec`` the one attribute it reads."""

    def __init__(self, image: str) -> None:
        self.image = image


class VerifyLab:
    """Verify the generated checks of one lab against both states."""

    def __init__(
        self,
        *,
        sandbox: SandboxProvider,
        clock: Clock,
        config: object = domain_config,
    ) -> None:
        """Wire the ports; the sandbox is called directly, no session is involved."""
        self._sandbox = sandbox
        self._clock = clock
        self._config = config

    def execute(self, lab: LabUnderVerification) -> tuple[VerificationFinding, ...]:
        """Run the lab matrix and return its findings in a stable order."""
        findings: list[VerificationFinding] = []
        handles: list[SandboxHandle] = []
        try:
            starting, reference = self._provision_unit(lab, handles)
            if starting is None or reference is None:
                findings.append(
                    VerificationFinding(
                        FindingKind.SANDBOX_UNAVAILABLE,
                        _LAB_TARGET,
                        "the verification sandboxes could not be provisioned",
                    )
                )
            else:
                findings.extend(self._phase(_Phase.STARTING, starting, lab))
                findings.extend(self._phase(_Phase.REFERENCE, reference, lab))
        finally:
            self._destroy_all(handles)
        return tuple(findings)

    # -- provisioning ----------------------------------------------------------

    def _provision_unit(
        self, lab: LabUnderVerification, handles: list[SandboxHandle]
    ) -> tuple[SandboxHandle | None, SandboxHandle | None]:
        """Provision both sandboxes; every created handle is appended for cleanup."""
        created: list[SandboxHandle] = []
        for phase in (_Phase.STARTING, _Phase.REFERENCE):
            spec = build_sandbox_spec(_Image(lab.image), f"verify-{lab.lab_id}-{phase.value}")
            try:
                handle = self._sandbox.provision(spec)
            except Exception as exc:
                if isinstance(exc, ProvisionError) and exc.partial_handle is not None:
                    handles.append(exc.partial_handle)
                _LOG.warning("verification sandbox provisioning failed: %s", type(exc).__name__)
                return None, None
            handles.append(handle)
            created.append(handle)
        return created[0], created[1]

    def _destroy_all(self, handles: list[SandboxHandle]) -> None:
        for handle in handles:
            try:
                self._sandbox.destroy(handle)
            except Exception:  # keep destroying the rest; the reaper collects orphans
                _LOG.warning("destroying sandbox %s failed", handle.sandbox_id, exc_info=True)

    # -- phases ----------------------------------------------------------------

    def _phase(
        self, phase: _Phase, handle: SandboxHandle, lab: LabUnderVerification
    ) -> list[VerificationFinding]:
        deadline: timedelta = self._config.LAB_GRADING_RUN_DEADLINE  # type: ignore[attr-defined]
        started = self._clock.now()
        findings: list[VerificationFinding] = []
        pending = list(lab.checks)
        try:
            if lab.setup_script and not self._run_script(
                handle, lab.setup_script, started, deadline
            ):
                return [VerificationFinding(FindingKind.SETUP_FAILED, _LAB_TARGET, phase.value)]
            if phase is _Phase.REFERENCE and not self._run_script(
                handle, lab.reference_solution, started, deadline
            ):
                return [
                    VerificationFinding(
                        FindingKind.REFERENCE_SOLUTION_FAILED, _LAB_TARGET, phase.value
                    )
                ]
            while pending:
                check = pending[0]
                bound = self._bound(check.timeout_s, started, deadline)
                if bound <= 0:
                    break
                result = self._exec(handle, check.script, bound)
                pending.pop(0)
                found = self._classify(phase, check, result)
                if found is not None:
                    findings.append(found)
        except Exception as exc:
            _LOG.warning("verification %s phase failed: %s", phase.value, type(exc).__name__)
            findings.append(
                VerificationFinding(
                    FindingKind.SANDBOX_UNAVAILABLE,
                    _LAB_TARGET,
                    f"{phase.value}: {type(exc).__name__}",
                )
            )
            return findings
        findings.extend(
            VerificationFinding(FindingKind.PHASE_DEADLINE_EXCEEDED, c.check_id, phase.value)
            for c in pending
        )
        return findings

    def _classify(
        self, phase: _Phase, check: LabCheckUnderVerification, result: ExecResult
    ) -> VerificationFinding | None:
        verdict = _verdict(result)
        if verdict is _Verdict.INCONCLUSIVE:
            return VerificationFinding(
                FindingKind.CHECK_INCONCLUSIVE,
                check.check_id,
                f"{phase.value}: timed out or killed (exit {result.exit_code})",
            )
        if phase is _Phase.STARTING and verdict is _Verdict.PASS:
            return VerificationFinding(
                FindingKind.CHECK_PASSES_ON_STARTING_STATE, check.check_id, phase.value
            )
        if phase is _Phase.REFERENCE and verdict is _Verdict.FAIL:
            return VerificationFinding(
                FindingKind.CHECK_FAILS_ON_REFERENCE_SOLUTION,
                check.check_id,
                f"{phase.value}: exit {result.exit_code}",
            )
        return None

    # -- exec helpers ------------------------------------------------------------

    def _bound(self, check_timeout_s: float, started: datetime, deadline: timedelta) -> float:
        """``min(check timeout, USABLE_RUN_BUDGET, time left in the phase)`` in seconds."""
        usable: timedelta = self._config.USABLE_RUN_BUDGET  # type: ignore[attr-defined]
        left = deadline - (self._clock.now() - started)
        return min(check_timeout_s, usable.total_seconds(), left.total_seconds())

    def _run_script(
        self, handle: SandboxHandle, script: str, started: datetime, deadline: timedelta
    ) -> bool:
        """Run a setup or solution script; ``True`` only on a clean exit within its bound."""
        bound = self._bound(float("inf"), started, deadline)
        if bound <= 0:
            return False
        result = self._exec(handle, script, bound)
        return not result.timed_out and result.exit_code == 0

    def _exec(self, handle: SandboxHandle, script: str, timeout_s: float) -> ExecResult:
        return self._sandbox.exec(
            handle,
            [_BASH, "-c", script],
            as_user=LEARNER_USER,
            env={},
            cwd=WORKSPACE_PATH,
            timeout_s=timeout_s,
        )
