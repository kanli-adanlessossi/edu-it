"""The ``GradeRun`` worker job and ``RecordCheckResult`` (Req 10.1-10.9, 11.2-11.4, 11.6, 11.8,
14.3-14.5, 14.11, 27.3, 27.4, 28.1, 28.3-28.5, 28.8, 29.9).

Deadline: ``accepted_at + 15 s`` = freeze bound (1 s) + Usable_Run_Budget (12 s) + teardown
reserve (2 s), all from Config. The whole body runs in a ``try`` whose ``finally`` always
removes ``/grader``, thaws the learner (nested ``finally``), finishes the run and CASes the
session ``grading -> ready``. Nothing the learner controls reaches any argument of any call.

Step order: budget, verified fail-closed freeze, copy-in, Checks, teardown. Argv forms and
absolute paths come from ``lab-images/README.md`` (task 3.4): the post-Check cleanup is
``kill -s KILL -1`` as the grader, because BusyBox ``kill`` rejects ``kill -KILL -- -1``.
"""

from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Final

from edu_it_domain import config as domain_config
from edu_it_domain.labs.grading import (
    ExpectedCase,
    PsRow,
    aggregate_run,
    effective_timeout,
    learner_all_stopped,
    map_exit_code,
    parse_harness_report,
    remaining_budget,
)
from edu_it_domain.labs.models import CheckOutcome, CheckResult, RunStatus, SessionStatus
from edu_it_domain.labs.progression import LabProgression, apply_lab_results
from edu_it_domain.labs.sandbox_spec import GRADER_PATH, LEARNER_USER
from edu_it_domain.ports.sandbox import SandboxGone, SandboxHandle

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from datetime import datetime

    from edu_it_domain.labs.models import GradingRun, LabSession, StableId
    from edu_it_domain.ports.clock import Clock
    from edu_it_domain.ports.lab_catalog import LabCatalog, LabCheckDefinition, LabDefinition
    from edu_it_domain.ports.lab_sessions import GradingRunRepository, LabSessionRepository
    from edu_it_domain.ports.mastery import MasteryRepository
    from edu_it_domain.ports.sandbox import ExecResult, SandboxProvider

__all__ = [
    "GRADER_USER",
    "GradeRun",
    "RecordCheckResult",
    "load_expected_cases",
]

_LOG = logging.getLogger(__name__)

GRADER_USER: Final = "grader"
_ROOT: Final = "root"

#: Absolute paths recorded in ``lab-images/README.md`` (task 3.4).
_KILL: Final = "/bin/kill"
_PS: Final = "/bin/ps"
_BASH: Final = "/bin/bash"
_SH: Final = "/bin/sh"
_PYTHON: Final = "/usr/bin/python3"
_FREEZE: Final = [_KILL, "-s", "STOP", "-1"]
_THAW: Final = [_KILL, "-s", "CONT", "-1"]
_KILL_ALL: Final = [_KILL, "-s", "KILL", "-1"]
_PS_LIST: Final = [_PS, "-e", "-o", "user=", "-o", "stat=", "-o", "pid=", "-o", "comm="]
#: Listing for providers whose containers have separate process namespaces (Kubernetes layout B):
#: it runs in the learner's own container, because the grader container sees none of the learner
#: processes. The first line names the PID of the listing process itself (``exec`` keeps the PID),
#: which is running by definition and is not a learner process to wait for. Nothing here comes
#: from the sandbox.
_SELF_MARK: Final = "SELF"
_PS_LIST_IN_LEARNER: Final = [
    _BASH,
    "-c",
    f'echo "{_SELF_MARK} $$"; exec {_PS} -e -o user= -o stat= -o pid= -o comm=',
]
#: Providers that verify the freeze from inside the learner container. With separate process
#: namespaces a listing as the grader would be vacuously "all stopped". Any other provider lists
#: as the grader in the one shared namespace.
_VERIFY_IN_LEARNER_CONTAINER: Final = frozenset({"kubernetes"})

#: Fixed environment of every Check: built from constants only, never from the sandbox.
_CLEAN_ENV: Final[Mapping[str, str]] = {
    "PATH": "/usr/bin:/bin",
    "LANG": "C",
    "HOME": f"{GRADER_PATH}/run",
}
_RUN_DIR: Final = f"{GRADER_PATH}/run"
_HARNESS: Final = f"{GRADER_PATH}/harness/run_cases.py"
_ROOT_CWD: Final = "/"
_UNSAFE_PATH_EXIT: Final = 90
_HOUSEKEEPING_TIMEOUT_S: Final = 5.0

#: Shell that empties ``/grader`` (a mount point) and then verifies it is empty.
_CLEAN_GRADER: Final = [
    _BASH,
    "-c",
    'shopt -s dotglob nullglob; /bin/rm -rf -- /grader/*; [ -z "$(/bin/ls -A /grader)" ]',
]
#: Creates the Grader_User-owned, mode 0700 working directory ``/grader/run`` without CAP_CHOWN
#: (cap_drop ALL, so root cannot hand a directory over, and CAP_DAC_OVERRIDE is gone too).
#: The ``/grader`` tmpfs is root-owned with group ``grader`` and mode 0775 (``docker_spec``);
#: the learner is neither owner nor group member. Three steps, all after the verified freeze:
#: root opens ``/grader`` to its group (idempotent), the grader creates ``run`` itself, and root
#: closes ``/grader`` to 0755 BEFORE any Check runs, so during Checks nothing in ``/grader`` is
#: group- or world-writable and the bundle stays root-owned (Req 10.2). No world-writable mode.
_OPEN_GRADER: Final = ["/bin/chmod", "0775", GRADER_PATH]
_MAKE_RUN_DIR: Final = ["/bin/mkdir", "-m", "0700", "--", _RUN_DIR]
_CLOSE_GRADER: Final = ["/bin/chmod", "0755", GRADER_PATH]
#: Empties the grader's working directory as the grader (root cannot enter a 0700 grader dir);
#: the now empty ``run`` is removed by ``_CLEAN_GRADER`` as root, who owns ``/grader``.
_CLEAN_RUN_DIR: Final = [
    _BASH,
    "-c",
    f"shopt -s dotglob nullglob; /bin/rm -rf -- {_RUN_DIR}/*",
]
type ExpectedLoader = Callable[[Path, LabCheckDefinition], Mapping[str, ExpectedCase]]


def load_expected_cases(bundle_dir: Path, check: LabCheckDefinition) -> dict[str, ExpectedCase]:
    """Read ``expected/<check_id>.json`` from the read-only bundle (never from the sandbox).

    Shape: ``{"cases": {"<case_id>": {"stdout_b64": "...", "exit_status": 0}}}``.
    """
    doc = json.loads((bundle_dir / "expected" / f"{check.check_id}.json").read_bytes())
    cases = doc["cases"]
    return {
        str(case_id): ExpectedCase(
            stdout=base64.b64decode(spec["stdout_b64"], validate=True),
            exit_status=int(spec.get("exit_status", 0)),
        )
        for case_id, spec in cases.items()
    }


# --- RecordCheckResult -------------------------------------------------------


class RecordCheckResult:
    """Persist one Check result and apply mastery for tasks that just became passing.

    Persisting per Check is what makes the SSE stream incremental and unlocks dependent
    modules mid-lab (Req 11.6, 19.6). ``append_result`` is idempotent per check id, so a
    re-delivery cannot double-count; completions are idempotent per ``(user, task)``.
    """

    def __init__(
        self, *, runs: GradingRunRepository, mastery: MasteryRepository, clock: Clock
    ) -> None:
        """Wire the ports the use case orchestrates."""
        self._runs = runs
        self._mastery = mastery
        self._clock = clock

    def execute(
        self, run_id: StableId, session: LabSession, lab: LabDefinition, result: CheckResult
    ) -> LabProgression:
        """Append ``result`` to the run and apply progression for the run so far."""
        self._runs.append_result(run_id, result)
        if result.outcome is not CheckOutcome.PASSED:
            return LabProgression()  # failed / could-not-complete never change mastery
        run = self._runs.get(run_id)
        if run is None:
            return LabProgression()
        user_id = session.user_id
        completed = {
            t.task_id for t in lab.tasks if self._runs.first_pass_exists(user_id, t.task_id)
        }
        hinted = {
            r.lab_task_id
            for r in self._mastery.list_hint_reveals(user_id)
            if r.lab_task_id is not None
        }
        current = {}
        for concept_id in lab.concept_ids:
            found = self._mastery.get(user_id, concept_id)
            if found is not None:
                current[concept_id] = found
        progression = apply_lab_results(
            lab=lab,
            user_id=user_id,
            run_id=run_id,
            results=run.results,
            completed_task_ids=completed,
            hinted_task_ids=hinted,
            mastery=current,
            clock=self._clock,
        )
        if progression.completions:
            self._runs.add_task_completions(run_id, progression.completions)
        for updated in progression.mastery:
            self._mastery.save(updated)
        return progression


# --- GradeRun ------------------------------------------------------------------


class _Abort(Exception):
    """Stop running Checks; the remaining Checks and the run are could-not-complete."""


@dataclass(slots=True)
class _State:
    run: GradingRun
    session: LabSession
    lab: LabDefinition
    handle: SandboxHandle
    job_started: datetime
    abort: bool = False
    gone: bool = False


class GradeRun:
    """Run one ``lab.grade`` delivery."""

    def __init__(
        self,
        *,
        sessions: LabSessionRepository,
        runs: GradingRunRepository,
        catalog: LabCatalog,
        sandbox: SandboxProvider,
        record: RecordCheckResult,
        clock: Clock,
        bundle_root: Path,
        provider_name: str = "docker",
        expected_loader: ExpectedLoader = load_expected_cases,
        config: object = domain_config,
    ) -> None:
        """Wire the ports the job orchestrates."""
        self._sessions = sessions
        self._runs = runs
        self._catalog = catalog
        self._sandbox = sandbox
        self._record = record
        self._clock = clock
        self._bundle_root = Path(bundle_root)
        self._provider = provider_name
        self._load_expected = expected_loader
        self._config = config

    def execute(self, run_id: StableId) -> RunStatus | None:
        """Grade ``run_id``; ``None`` when the delivery is a duplicate or has nothing to grade."""
        run = self._runs.get(run_id)
        if run is None or run.status is not RunStatus.RUNNING:
            return None
        session = self._sessions.get(run.session_id)
        if (
            session is None
            or session.status is not SessionStatus.GRADING
            or session.sandbox_ref is None
        ):
            return None
        lab = self._catalog.get(session.lab_id)
        if lab is None:
            return None
        state = _State(
            run=run,
            session=session,
            lab=lab,
            handle=SandboxHandle(
                sandbox_id=session.sandbox_ref,
                session_stable_id=session.stable_id,
                provider=self._provider,
            ),
            job_started=self._clock.now(),
        )
        try:
            self._body(state)
        except SandboxGone:
            state.abort = state.gone = True
        except _Abort:
            state.abort = True
        except Exception:
            _LOG.warning("grading run %s failed unexpectedly", run_id, exc_info=True)
            state.abort = True
        finally:
            status = self._teardown(state)
        return status

    # -- steps ---------------------------------------------------------------

    def _body(self, st: _State) -> None:
        remaining = remaining_budget(st.run.accepted_at, st.job_started, timedelta(0), self._config)
        if remaining <= timedelta(0):
            raise _Abort  # (1) no freeze, no copy; every Check recorded could-not-complete
        self._freeze(st)  # (2) fail-closed before any copy_in or Check
        bundle_dir = self._bundle_root / st.lab.bundle_path
        self._sandbox.copy_in(st.handle, bundle_dir / "sandbox", GRADER_PATH, as_user=_ROOT)  # (3)
        for argv, user in (
            (_OPEN_GRADER, _ROOT),
            (_MAKE_RUN_DIR, GRADER_USER),
            (_CLOSE_GRADER, _ROOT),
        ):
            self._exec(st, argv, user, _ROOT_CWD, _HOUSEKEEPING_TIMEOUT_S, check=True)
        checks_started = self._clock.now()
        for check in st.lab.checks:  # (4)
            spent = self._clock.now() - checks_started
            left = remaining_budget(st.run.accepted_at, st.job_started, spent, self._config)
            timeout = effective_timeout(timedelta(seconds=check.timeout_s), left)
            if timeout <= timedelta(0):
                self._record_result(st, check, CheckOutcome.COULD_NOT_COMPLETE, "no-budget")
                continue
            try:
                outcome, code = self._run_check(st, bundle_dir, check, timeout.total_seconds())
            finally:
                self._exec(st, _KILL_ALL, GRADER_USER, _RUN_DIR, _HOUSEKEEPING_TIMEOUT_S)
            self._record_result(st, check, outcome, code)  # (5)
            if code == "unsafe-learner-file":
                raise _Abort

    def _freeze(self, st: _State) -> None:
        bound: timedelta = self._config.LAB_GRADING_FREEZE_BOUND  # type: ignore[attr-defined]
        attempts: int = self._config.LAB_GRADING_FREEZE_ATTEMPTS  # type: ignore[attr-defined]
        started = self._clock.now()
        for _ in range(attempts):
            if self._clock.now() - started >= bound:
                break
            stop = self._try_exec(st, _FREEZE, LEARNER_USER, _ROOT_CWD, bounded=False)
            in_learner = self._provider in _VERIFY_IN_LEARNER_CONTAINER
            # In the learner container the listing is unbounded like the freeze: a provider's
            # learner-owned ``timeout`` wrapper would be one more (sleeping) learner process in
            # the very listing that must show only stopped ones.
            listing = self._try_exec(
                st,
                _PS_LIST_IN_LEARNER if in_learner else _PS_LIST,
                LEARNER_USER if in_learner else GRADER_USER,
                _ROOT_CWD,
                bounded=not in_learner,
            )
            if self._clock.now() - started > bound:
                break
            # In the learner's own container ``kill -s STOP -1`` exits 1 (ESRCH) when the learner
            # has no process besides the init, which is a frozen learner, not a failure (Docker
            # always has its learner-owned keep-alive to signal). There the verified listing is
            # the proof; the stop exec must still have run.
            if (
                stop is not None
                and (stop.exit_code == 0 or in_learner)
                and listing is not None
                and listing.exit_code == 0
                and not listing.timed_out
                and learner_all_stopped(_parse_ps(listing.stdout, in_learner), LEARNER_USER)
            ):
                return
        raise _Abort

    def _run_check(
        self, st: _State, bundle_dir: Path, check: LabCheckDefinition, timeout_s: float
    ) -> tuple[CheckOutcome, str]:
        if st.lab.kind == "code":
            result = self._exec(
                st,
                [_PYTHON, "-I", _HARNESS, check.check_id],
                GRADER_USER,
                _RUN_DIR,
                timeout_s,
            )
            if result.timed_out:
                return CheckOutcome.COULD_NOT_COMPLETE, "timeout"
            try:
                expected = self._load_expected(bundle_dir, check)
            except Exception:
                _LOG.warning("expected values of %s unreadable", check.check_id, exc_info=True)
                return CheckOutcome.COULD_NOT_COMPLETE, "expected-unavailable"
            verdict = parse_harness_report(result.stdout, check.check_id, expected)
            return verdict.outcome, verdict.outcome_code
        result = self._exec(
            st,
            [_SH, f"{GRADER_PATH}/checks/{check.check_id}.sh"],
            GRADER_USER,
            _RUN_DIR,
            timeout_s,
        )
        outcome = map_exit_code(None, result.exit_code, result.timed_out)
        declared = check.failure_exit_codes.get(result.exit_code) if result.exit_code else None
        if declared is not None and not result.timed_out and result.exit_code != _UNSAFE_PATH_EXIT:
            return CheckOutcome.FAILED, declared
        if outcome is CheckOutcome.PASSED:
            return outcome, "pass"
        if outcome is CheckOutcome.FAILED:
            return outcome, "fail"
        if result.exit_code == _UNSAFE_PATH_EXIT and not result.timed_out:
            return outcome, "unsafe-path"
        # Req 28.7 / Property 10: a timeout and any signal kill (OOM, fork bomb, runaway
        # allocation: exit code above 128 or negative) are recorded identically.
        if (
            result.timed_out
            or result.exit_code is None
            or result.exit_code > 128
            or result.exit_code < 0
        ):
            return outcome, "timeout"
        return outcome, "could-not-complete"

    def _record_result(
        self, st: _State, check: LabCheckDefinition, outcome: CheckOutcome, code: str
    ) -> None:
        result = CheckResult(
            check_id=check.check_id,
            task_id=check.task_id,
            outcome=outcome,
            outcome_code=code,
            feedback_key=check.outcomes.get(code) or f"lab.feedback.{code}",
        )
        self._record.execute(st.run.stable_id, st.session, st.lab, result)

    def _teardown(self, st: _State) -> RunStatus:
        try:
            try:
                self._teardown_exec(st, _CLEAN_RUN_DIR, GRADER_USER)
                self._teardown_exec(st, _CLEAN_GRADER, _ROOT)
            finally:
                self._teardown_exec(st, _THAW, LEARNER_USER, bounded=False)
        finally:
            status = self._finish(st)
        return status

    def _teardown_exec(
        self, st: _State, argv: list[str], user: str, *, bounded: bool = True
    ) -> None:
        if st.gone:
            return
        try:
            result = self._try_exec(st, argv, user, _ROOT_CWD)
        except SandboxGone:
            return  # st.gone is set; nothing left to clean or thaw
        if result is not None and (result.timed_out or result.exit_code != 0):
            _LOG.warning("teardown command %s exited %s", argv[0], result.exit_code)

    def _finish(self, st: _State) -> RunStatus:
        run = self._runs.get(st.run.stable_id) or st.run
        done = {r.check_id for r in run.results}
        for check in st.lab.checks:
            if check.check_id not in done:
                try:
                    self._record_result(
                        st, check, CheckOutcome.COULD_NOT_COMPLETE, "could-not-complete"
                    )
                except Exception:
                    _LOG.warning("recording result of %s failed", check.check_id, exc_info=True)
        run = self._runs.get(st.run.stable_id) or st.run
        status = aggregate_run(run.results, st.abort)
        now = self._clock.now()
        self._runs.finish(st.run.stable_id, status=status, graded_at=now)
        self._sessions.transition(
            st.session.stable_id,
            expected={SessionStatus.GRADING},
            to=SessionStatus.READY,
        )
        return status

    # -- exec helpers ----------------------------------------------------------

    def _exec(
        self,
        st: _State,
        argv: list[str],
        user: str,
        cwd: str,
        timeout_s: float | None,
        *,
        check: bool = False,
    ) -> ExecResult:
        # ``timeout_s=None`` sends the command without the provider's ``timeout`` wrapper.
        result = self._sandbox.exec(
            st.handle, argv, as_user=user, env=_CLEAN_ENV, cwd=cwd, timeout_s=timeout_s
        )
        if check and (result.timed_out or result.exit_code != 0):
            raise _Abort
        return result

    def _try_exec(
        self, st: _State, argv: list[str], user: str, cwd: str, *, bounded: bool = True
    ) -> ExecResult | None:
        """Housekeeping exec: a provider error other than SandboxGone is logged, not fatal.

        ``bounded=False`` sends no ``timeout_s``. Freeze and thaw need this, since
        ``kill -s STOP|CONT -1`` signals every process of the exec user, including the
        provider's learner-owned ``timeout`` wrapper, which would stop the exec itself. Only
        the provider's operation timeout limits those calls; the caller's clock enforces the
        freeze bound.
        """
        try:
            return self._exec(st, argv, user, cwd, _HOUSEKEEPING_TIMEOUT_S if bounded else None)
        except SandboxGone:
            st.gone = True
            raise
        except Exception:
            _LOG.warning("sandbox exec failed", exc_info=True)
            return None


def _parse_ps(stdout: bytes, in_learner_container: bool = False) -> list[PsRow]:
    """Parse ``user stat pid comm`` rows; PID 1 (the init) never stops and is ignored.

    A malformed row is reported as a running learner process, so verification fails closed.

    ``in_learner_container`` is the output of ``_PS_LIST_IN_LEARNER``. Every process of that
    container is a learner process whatever name its UID resolves to (the lab image has no entry
    for the pod UIDs, so ``ps`` prints a number), so the user column is replaced by the learner.
    The listing must name its own PID and show that PID in the table; otherwise it did not run
    where it should, and it counts as a running learner process (fail closed).
    """
    rows: list[PsRow] = []
    own_pid: str | None = None
    own_seen = False
    for line in stdout.decode("utf-8", errors="replace").splitlines():
        parts = line.split()
        if not parts:
            continue
        if in_learner_container and parts[0] == _SELF_MARK and len(parts) == 2:
            own_pid = parts[1]
            continue
        if len(parts) < 3:
            rows.append(PsRow(LEARNER_USER, ""))
            continue
        if parts[2] == "1":
            continue
        if in_learner_container:
            if parts[2] == own_pid:
                own_seen = True
            else:
                rows.append(PsRow(LEARNER_USER, parts[1]))
            continue
        rows.append(PsRow(parts[0], parts[1]))
    if in_learner_container and not own_seen:
        rows.append(PsRow(LEARNER_USER, ""))
    return rows
