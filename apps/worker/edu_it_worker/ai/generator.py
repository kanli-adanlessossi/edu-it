"""The Generator: one Stage per run (ai-content Req 5, 10, 12).

Order of a run, with no way to reorder or skip a step:

1. validate the AI Config and the request;
2. stage gate: the preceding Stage needs Stage_Markers on the integration branch or on the
   named Base_Ref. On refusal there is no job, no branch and no model call;
3. budget gate: daily, per-track and Cost_Cap, read from the ledger. On refusal, same;
4. plan the units, skipping those already committed (resume, keyed by ``stable_id``);
5. create the Generation_Branch and open the job;
6. per unit: draft, verify (labs through ``VerifyLab``), build both locales, validate
   structure with ``include_review_rule=False``, then commit the complete unit;
7. commit the Generation_Report and governance files, close the job, return an exit code.

The Base_Ref only widens where markers and existing items are read. The Git port offers no
operation that joins branches or publishes to a remote, and this module names none, so the
Generator can only add commits to its own new branch.
"""

from __future__ import annotations

import json
import logging
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, time
from enum import IntEnum
from pathlib import Path
from typing import Final

from edu_it_application.ai import VerifyItem
from edu_it_content.validation import ContentLoadError, load_content, validate_content
from edu_it_domain import config as domain_config
from edu_it_domain.ai.budget import check_attempt
from edu_it_domain.ai.generation import STAGES, missing_stage, remaining_units
from edu_it_domain.ai.verification import CommandOutput
from edu_it_domain.config import AiConfigError, validate_ai_config
from edu_it_domain.ports.clock import Clock
from edu_it_domain.ports.generation_ledger import (
    GenerationLedger,
    JobKind,
    JobRef,
    JobStatus,
    NewJob,
)
from edu_it_domain.ports.git_workspace import GitError, GitWorkspace, generation_branch_name
from edu_it_domain.ports.id_generator import IdGenerator
from edu_it_domain.ports.llm_provider import (
    AttemptUsage,
    BudgetReachedError,
    LLMProvider,
    MalformedOutputError,
    MissingFixtureError,
    PersonalDataInRequestError,
    PromptRef,
    ProviderBindingError,
    ProviderUnavailableError,
)
from edu_it_infrastructure.llm.ledger_binding import JobScope
from edu_it_infrastructure.llm.pydantic_schema import PydanticOutputSchema

from edu_it_worker.ai.content_writer import ContentWriter, GenerationInfo
from edu_it_worker.ai.prompt_store import GLOSSARY_PROMPT_ID, PromptStore, glossary_pairs
from edu_it_worker.ai.report import (
    CODEOWNERS_PATH,
    BudgetRemaining,
    DiscardedUnit,
    GeneratedItem,
    GenerationReport,
    NotGenerated,
    ReportedFinding,
    governance_files,
)
from edu_it_worker.ai.stages import committed_units, content_dir, stages_with_markers
from edu_it_worker.ai.units import BuildEnv, BuiltUnit, StageBuilder, UnitPlan, stage_builder

__all__ = [
    "ExitCode",
    "GenerationOutcome",
    "GenerationRequest",
    "Generator",
    "GeneratorDeps",
    "RunProviders",
]

_LOG = logging.getLogger(__name__)

_TEXT_SUFFIXES: Final = (".json", ".mdx")
_LAB_IMAGES_DIR: Final = "lab-images"
_MAX_REASON_FAILURES: Final = 5
_CATALOG_DEPTH: Final = 2  # content/<track>/<type>.json
_NON_CATALOG: Final = frozenset({"brief.json", "pending-review.json"})


class ExitCode(IntEnum):
    """Process exit codes of ``edu-it-generate``."""

    OK = 0
    FAILED = 1
    """A provider failure, or at least one unit discarded or not generated."""

    USAGE = 2
    """Invalid arguments or invalid Config."""

    STAGE_GATE = 3
    """The preceding Stage has no Stage_Markers. No job, branch or model call."""

    BUDGET = 4
    """A Budget was reached."""

    INTERRUPTED = 130


@dataclass(frozen=True, slots=True)
class GenerationRequest:
    """What one invocation asks for. There is no field that could skip a gate."""

    track: str
    stage: str
    live: bool = False
    cost_cap_cents: int | None = None
    limit_units: int | None = None
    base_ref: str | None = None


@dataclass(frozen=True, slots=True)
class RunProviders:
    """The model providers bound to one job."""

    llm: LLMProvider
    """Drafts the units."""

    verifier: VerifyItem
    """Verifies each unit; owns the independent answerer, the sandbox and ``VerifyLab``."""


@dataclass(frozen=True, slots=True)
class GeneratorDeps:
    """Everything the Generator reads from outside."""

    git: GitWorkspace
    ledger: GenerationLedger
    clock: Clock
    ids: IdGenerator
    prompts: PromptStore
    wire: Callable[[JobScope], RunProviders]
    """Binds the providers to a job scope (budget gate and ledger recording)."""

    base_branch: str
    """The integration branch the Generation_Branch starts from and markers are read on."""

    config: object = domain_config


@dataclass(frozen=True, slots=True)
class GenerationOutcome:
    """The result of one run."""

    exit_code: ExitCode
    message: str
    branch: str | None = None
    run_id: str | None = None
    committed: tuple[str, ...] = ()
    discarded: tuple[DiscardedUnit, ...] = ()
    not_generated: tuple[NotGenerated, ...] = ()
    findings: int = 0


class _TallyLedger:
    """Delegates to a ledger and keeps every attempt of this run for the report."""

    def __init__(self, inner: GenerationLedger) -> None:
        self._inner = inner
        self.attempts: list[AttemptUsage] = []

    def open_job(self, job: NewJob) -> JobRef:
        return self._inner.open_job(job)

    def record_attempt(self, job: JobRef, usage: AttemptUsage) -> None:
        self._inner.record_attempt(job, usage)
        self.attempts.append(usage)

    def close_job(self, job: JobRef, status: JobStatus) -> None:
        self._inner.close_job(job, status)

    def spent_today(self, day_start: datetime) -> int:
        return self._inner.spent_today(day_start)

    def spent_for_track(self, track_stable_id: str) -> int:
        return self._inner.spent_for_track(track_stable_id)

    def spent_for_job(self, job: JobRef) -> int:
        return self._inner.spent_for_job(job)


class _RunSource:
    """Content as the run sees it: files written so far, then the Base_Ref, then the base.

    Reads only. The Base_Ref is one more place to look for files and nothing else.
    """

    def __init__(self, git: GitWorkspace, refs: tuple[str, ...]) -> None:
        self._git = git
        self._refs = refs
        self._texts: dict[str, str | None] = {}
        self._listed: dict[str, frozenset[str]] = {}
        self.written: dict[str, bytes] = {}

    def read(self, path: str) -> str | None:
        if path in self.written:
            return self.written[path].decode("utf-8")
        if path not in self._texts:
            found: str | None = None
            for ref in self._refs:
                found = self._git.read_text(ref, path)
                if found is not None:
                    break
            self._texts[path] = found
        return self._texts[path]

    def files(self, prefix: str) -> frozenset[str]:
        if prefix not in self._listed:
            listed: set[str] = set()
            for ref in self._refs:
                listed |= self._git.list_files(ref, prefix)
            self._listed[prefix] = frozenset(listed)
        return self._listed[prefix] | frozenset(p for p in self.written if p.startswith(prefix))


@dataclass
class _RunState:
    """Mutable record of one run."""

    items: list[GeneratedItem] = field(default_factory=list)
    findings: list[ReportedFinding] = field(default_factory=list)
    discarded: list[DiscardedUnit] = field(default_factory=list)
    not_generated: list[NotGenerated] = field(default_factory=list)
    committed: list[str] = field(default_factory=list)
    stop_reason: str = ""
    budget_kind: str | None = None
    provider_failed: bool = False
    interrupted: bool = False

    @property
    def stopped(self) -> bool:
        return self.budget_kind is not None or self.provider_failed or self.interrupted


class Generator:
    """Runs one Stage for one track."""

    def __init__(self, deps: GeneratorDeps) -> None:
        self._d = deps

    # -- public ---------------------------------------------------------------------------

    def run(self, request: GenerationRequest) -> GenerationOutcome:
        """Run the steps in order and return the outcome. Never raises for a refusal."""
        refusal = self._check_request(request)
        if refusal is not None:
            return refusal
        d = self._d
        track, stage = request.track, request.stage
        track_id = f"track.{track}"

        # Stage gate (Req 5.3, 5.11, 5.12, 5.14, 5.15): before any job, branch or model call.
        try:
            integrated = stages_with_markers(d.git, d.base_branch, track)
            named = (
                stages_with_markers(d.git, request.base_ref, track)
                if request.base_ref
                else frozenset()
            )
        except GitError as error:
            return GenerationOutcome(ExitCode.USAGE, f"cannot read stage markers: {error}")
        missing = missing_stage(stage, integrated, named)
        if missing is not None:
            where = f"{d.base_branch} or the named base ref" if request.base_ref else d.base_branch
            return GenerationOutcome(
                ExitCode.STAGE_GATE,
                f"stage {stage!r} cannot run: the preceding stage {missing!r} has no output on "
                f"{where}. No job, branch or model call was made.",
            )

        # Budget gate (Req 10.3, 10.5): before any job, branch or model call.
        now = d.clock.now()
        day_start = datetime.combine(now.date(), time.min, tzinfo=now.tzinfo)
        daily_limit: int = d.config.AI_BUDGET_DAILY_CENTS  # type: ignore[attr-defined]
        track_limit: int = d.config.AI_BUDGET_PER_TRACK_CENTS  # type: ignore[attr-defined]
        decision = check_attempt(
            daily_spent_cents=d.ledger.spent_today(day_start),
            track_spent_cents=d.ledger.spent_for_track(track_id),
            job_spent_cents=0,
            cost_cap_cents=request.cost_cap_cents,
            daily_limit_cents=daily_limit,
            track_limit_cents=track_limit,
        )
        if decision.reached is not None:
            return GenerationOutcome(
                ExitCode.BUDGET,
                f"budget reached: {decision.reached.value}. No job, branch or model call was made.",
            )

        # Plan, skipping committed units (Req 12.6).
        builder = stage_builder(stage)
        refs = (request.base_ref, d.base_branch) if request.base_ref else (d.base_branch,)
        src = _RunSource(d.git, refs)
        env = BuildEnv(track, src, ContentWriter(d.clock, d.ids), self._glossary())
        planned = builder.plan(env)
        if not planned:
            return GenerationOutcome(
                ExitCode.FAILED, f"nothing to plan for stage {stage!r}: its inputs are unreadable."
            )
        done: set[str] = set()
        for ref in refs:
            done |= committed_units(d.git, ref, track, stage)
        todo_ids = remaining_units([p.unit_id for p in planned], done)
        by_id = {p.unit_id: p for p in planned}
        todo = [by_id[unit] for unit in todo_ids]
        if not todo:
            return GenerationOutcome(
                ExitCode.OK, f"nothing to generate: all {len(planned)} unit(s) are committed."
            )
        limit = request.limit_units
        selected = todo if limit is None else todo[:limit]
        skipped = [] if limit is None else todo[limit:]

        # Branch and job.
        run_id = d.ids.new_id()
        try:
            branch = generation_branch_name(track, stage, run_id)
            d.git.create_branch(branch, d.base_branch)
        except (GitError, ValueError) as error:
            return GenerationOutcome(ExitCode.FAILED, f"cannot create the branch: {error}")
        version = self._prompt_version(builder.prompt_id)
        tally = _TallyLedger(d.ledger)
        job = tally.open_job(
            NewJob(
                stable_id=run_id,
                kind=JobKind.STAGE,
                track_stable_id=track_id,
                stage=stage,
                prompt_id=builder.prompt_id,
                prompt_version=version,
                cost_cap_cents=request.cost_cap_cents,
                branch=branch,
            )
        )
        state = _RunState()
        state.not_generated += [
            NotGenerated(p.unit_id, "not attempted: --limit-units reached") for p in skipped
        ]
        scope = JobScope(
            ledger=tally,
            job=job,
            clock=d.clock,
            track_stable_id=track_id,
            cost_cap_cents=request.cost_cap_cents,
            daily_limit_cents=daily_limit,
            track_limit_cents=track_limit,
        )
        try:
            providers = d.wire(scope)
        except Exception as error:
            _LOG.error("provider setup failed: %s", type(error).__name__)
            tally.close_job(job, JobStatus.FAILED)
            return GenerationOutcome(
                ExitCode.FAILED,
                f"provider setup failed ({type(error).__name__}); branch {branch} has no commits",
                branch=branch,
                run_id=run_id,
            )

        # Units.
        try:
            self._run_units(
                builder, selected, providers, env, src, branch, version, state, request.track
            )
        except KeyboardInterrupt:
            state.interrupted = True
            state.stop_reason = "interrupted"

        # Report and governance files; the branch stays unintegrated.
        remaining = self._remaining(
            tally, job, track_id, request.cost_cap_cents, daily_limit, track_limit
        )
        report = self._report(request, run_id, branch, state, tally, remaining)
        exit_code, message, status = self._finish(state, branch)
        try:
            files = governance_files(report, {CODEOWNERS_PATH: src.read(CODEOWNERS_PATH) or ""})
            d.git.commit_files(branch, files, f"generate({track}/{stage}): report {run_id}")
        except (GitError, ValueError) as error:
            tally.close_job(job, JobStatus.FAILED)
            return GenerationOutcome(
                ExitCode.FAILED,
                f"cannot commit the report: {error}",
                branch=branch,
                run_id=run_id,
                committed=tuple(state.committed),
            )
        tally.close_job(job, status)
        return GenerationOutcome(
            exit_code,
            message,
            branch=branch,
            run_id=run_id,
            committed=tuple(state.committed),
            discarded=tuple(state.discarded),
            not_generated=tuple(state.not_generated),
            findings=len(state.findings),
        )

    # -- request and prompts ----------------------------------------------------------------

    def _check_request(self, request: GenerationRequest) -> GenerationOutcome | None:
        try:
            validate_ai_config(self._d.config)
        except AiConfigError as error:
            keys = ", ".join(error.keys)
            return GenerationOutcome(ExitCode.USAGE, f"invalid AI Config ({keys}): {error}")
        if request.stage not in STAGES:
            return GenerationOutcome(
                ExitCode.USAGE,
                f"unknown stage {request.stage!r}; expected one of {', '.join(STAGES)}",
            )
        try:
            content_dir(request.track)
        except ValueError:
            return GenerationOutcome(ExitCode.USAGE, f"invalid track {request.track!r}")
        if request.limit_units is not None and request.limit_units < 1:
            return GenerationOutcome(ExitCode.USAGE, "--limit-units must be at least 1")
        if request.cost_cap_cents is not None and request.cost_cap_cents < 1:
            return GenerationOutcome(ExitCode.USAGE, "--cost-cap-usd must be at least USD 0.01")
        if request.base_ref is not None and not request.base_ref.strip():
            return GenerationOutcome(ExitCode.USAGE, "--base-ref must not be empty")
        return None

    def _prompt_version(self, prompt_id: str) -> str:
        versions = [int(v) for (pid, v) in self._d.prompts.available() if pid == prompt_id]
        if not versions:
            msg = f"no prompt file for {prompt_id!r}"
            raise LookupError(msg)
        return str(max(versions))

    def _glossary(self) -> str:
        pairs = glossary_pairs(
            self._d.prompts.load(GLOSSARY_PROMPT_ID, self._prompt_version(GLOSSARY_PROMPT_ID))
        )
        return json.dumps([list(pair) for pair in pairs], ensure_ascii=False)

    # -- units ------------------------------------------------------------------------------

    def _run_units(
        self,
        builder: StageBuilder,
        units: list[UnitPlan],
        providers: RunProviders,
        env: BuildEnv,
        src: _RunSource,
        branch: str,
        version: str,
        state: _RunState,
        track: str,
    ) -> None:
        prompt = PromptRef(builder.prompt_id, version)
        schema = PydanticOutputSchema(builder.model)
        retries: int = domain_config.LLM_MAX_RETRIES
        for index, plan in enumerate(units):
            try:
                result = providers.llm.complete(prompt, plan.input, schema, retries)
                draft = builder.model.model_validate(result.output)
                subjects = builder.subjects(plan, draft, env)
                outputs: dict[str, tuple[CommandOutput, ...]] = {}
                for position, subject in enumerate(subjects):
                    context = builder.verification_context(plan, env, subjects[:position])
                    found, produced = providers.verifier.execute(subject, context)
                    state.findings += [ReportedFinding(subject.item_id, f) for f in found]
                    outputs[subject.item_id] = produced
                info = GenerationInfo(
                    result.model, result.prompt.prompt_id, result.prompt.prompt_version
                )
                built = builder.build(plan, draft, info, outputs, env)
            except BudgetReachedError as error:
                state.budget_kind = error.kind
                state.stop_reason = f"budget reached: {error.kind}"
                state.discarded.append(
                    DiscardedUnit(plan.unit_id, f"stopped by the {error.kind} budget")
                )
                self._not_generated(units[index + 1 :], state, f"{error.kind} budget reached")
                return
            except (ProviderUnavailableError, ProviderBindingError, MissingFixtureError) as error:
                state.provider_failed = True
                state.stop_reason = f"provider failure: {type(error).__name__}"
                state.not_generated.append(
                    NotGenerated(plan.unit_id, f"provider failure: {type(error).__name__}")
                )
                self._not_generated(units[index + 1 :], state, "provider failure")
                return
            except MalformedOutputError:
                state.discarded.append(
                    DiscardedUnit(
                        plan.unit_id, "malformed output after every attempt; not corrected"
                    )
                )
                continue
            except PersonalDataInRequestError:
                state.discarded.append(
                    DiscardedUnit(plan.unit_id, "the request carried personal data; no call made")
                )
                continue
            except KeyboardInterrupt:
                state.discarded.append(DiscardedUnit(plan.unit_id, "interrupted before completion"))
                self._not_generated(units[index + 1 :], state, "interrupted")
                state.interrupted = True
                state.stop_reason = "interrupted"
                return
            except ValueError as error:
                state.discarded.append(
                    DiscardedUnit(plan.unit_id, f"incomplete or invalid unit: {error}")
                )
                continue

            problems = self._structural_failures(src, track, built)
            if problems:
                shown = "; ".join(problems[:_MAX_REASON_FAILURES])
                state.discarded.append(
                    DiscardedUnit(plan.unit_id, f"structural validation failed: {shown}")
                )
                continue
            try:
                self._d.git.commit_files(
                    branch, dict(built.files), f"generate({track}/{builder.stage}): {plan.unit_id}"
                )
            except GitError as error:
                state.provider_failed = True
                state.stop_reason = f"commit failed: {error}"
                state.discarded.append(DiscardedUnit(plan.unit_id, "the commit failed"))
                self._not_generated(units[index + 1 :], state, "commit failed")
                return
            src.written.update(built.files)
            state.committed.append(plan.unit_id)
            state.items.append(
                GeneratedItem(
                    stable_id=plan.unit_id,
                    kind=plan.kind,
                    model=result.model,
                    prompt_id=result.prompt.prompt_id,
                    prompt_version=result.prompt.prompt_version,
                    content_version=built.content_version,
                    input_tokens=sum(a.input_tokens for a in result.attempts),
                    output_tokens=sum(a.output_tokens for a in result.attempts),
                    cost_cents=sum(a.cost_cents for a in result.attempts),
                    generation_date=info_date(self._d.clock.now()),
                )
            )

    @staticmethod
    def _not_generated(units: list[UnitPlan], state: _RunState, reason: str) -> None:
        state.not_generated += [NotGenerated(p.unit_id, reason) for p in units]

    # -- structure --------------------------------------------------------------------------

    def _structural_failures(self, src: _RunSource, track: str, built: BuiltUnit) -> list[str]:
        """Validate the track as it would be with this unit, without the review rule (Req 6.2)."""
        base = content_dir(track)
        contents: dict[str, bytes] = {}
        for path in sorted(src.files(f"{base}/")):
            text = src.read(path) if path.endswith(_TEXT_SUFFIXES) else ""
            contents[path] = (text or "").encode("utf-8")
        contents.update(built.files)
        if not any(
            path.endswith(".json")
            and path.count("/") == _CATALOG_DEPTH
            and name not in _NON_CATALOG
            for path in contents
            for name in (path.rsplit("/", 1)[-1],)
        ):
            return []  # the brief alone is not a catalog list: nothing to load
        images = {
            path.split("/")[1] for path in src.files(f"{_LAB_IMAGES_DIR}/") if path.count("/") >= 2
        }
        try:
            with tempfile.TemporaryDirectory(prefix="edu-it-validate-") as tmp:
                root = Path(tmp)
                for path, data in contents.items():
                    target = root / path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(data)
                    if path.endswith(".mdx"):
                        shared = root / "content" / "mdx" / Path(path).name
                        shared.parent.mkdir(parents=True, exist_ok=True)
                        shared.write_bytes(data)
                for image in images:
                    (root / _LAB_IMAGES_DIR / image).mkdir(parents=True, exist_ok=True)
                failures = validate_content(
                    load_content(root / "content"), repo_root=root, include_review_rule=False
                )
        except ContentLoadError as error:
            return [f"content could not be loaded: {error}"]
        return [failure.message() for failure in failures]

    # -- report -----------------------------------------------------------------------------

    def _remaining(
        self,
        tally: _TallyLedger,
        job: JobRef,
        track_id: str,
        cap: int | None,
        daily_limit: int,
        track_limit: int,
    ) -> BudgetRemaining:
        now = self._d.clock.now()
        day_start = datetime.combine(now.date(), time.min, tzinfo=now.tzinfo)
        return BudgetRemaining(
            daily_cents=daily_limit - tally.spent_today(day_start),
            track_cents=track_limit - tally.spent_for_track(track_id),
            cap_cents=None if cap is None else cap - tally.spent_for_job(job),
        )

    @staticmethod
    def _report(
        request: GenerationRequest,
        run_id: str,
        branch: str,
        state: _RunState,
        tally: _TallyLedger,
        remaining: BudgetRemaining,
    ) -> GenerationReport:
        attempts = tally.attempts
        return GenerationReport(
            track=request.track,
            stage=request.stage,
            run=run_id,
            branch=branch,
            live=request.live,
            items=tuple(state.items),
            findings=tuple(state.findings),
            remaining=remaining,
            discarded=tuple(state.discarded),
            not_generated=tuple(state.not_generated),
            extra_input_tokens=max(
                0, sum(a.input_tokens for a in attempts) - sum(i.input_tokens for i in state.items)
            ),
            extra_output_tokens=max(
                0,
                sum(a.output_tokens for a in attempts) - sum(i.output_tokens for i in state.items),
            ),
            extra_cost_cents=max(
                0, sum(a.cost_cents for a in attempts) - sum(i.cost_cents for i in state.items)
            ),
            extra_models=tuple(sorted({a.model for a in attempts})),
            stop_reason=state.stop_reason,
        )

    @staticmethod
    def _finish(state: _RunState, branch: str) -> tuple[ExitCode, str, JobStatus]:
        done = len(state.committed)
        tail = f"{done} unit(s) committed on {branch}; the branch is left for review."
        if state.interrupted:
            return ExitCode.INTERRUPTED, f"interrupted. {tail}", JobStatus.INTERRUPTED
        if state.budget_kind is not None:
            return (
                ExitCode.BUDGET,
                f"budget reached: {state.budget_kind}. {tail}",
                JobStatus.BUDGET_REACHED,
            )
        if state.provider_failed:
            return ExitCode.FAILED, f"{state.stop_reason}. {tail}", JobStatus.FAILED
        if state.discarded:
            return (
                ExitCode.FAILED,
                f"{len(state.discarded)} unit(s) discarded. {tail}",
                JobStatus.FAILED,
            )
        return ExitCode.OK, tail, JobStatus.COMPLETED


def info_date(instant: datetime) -> str:
    """Return the generation date as it is written in Provenance."""
    return instant.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
