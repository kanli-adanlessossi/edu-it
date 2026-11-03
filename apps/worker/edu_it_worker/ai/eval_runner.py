"""The Eval_Suite runner behind ``edu-it-eval`` (ai-content Req 11).

A suite is a JSON file under ``evals/suites/`` listing cases built from the Golden_Set of
Phases 2 and 3. Three case kinds exist:

* ``schema-conformance``: the drafting prompt's output validates against its output schema;
* ``answer-key``: the Independent_Answerer, called blind, selects the golden key;
* ``glossary-terms``: required term pairs are in the current glossary and appear in the
  matching locale of the case text. No model call.

The run is deterministic (Req 11.5). Cases are processed in sorted id order, nothing reads the
wall clock, and the Eval_Report holds Prompt versions, case outcomes and the aggregate pass
rate only. Under ``EDU_IT_ENV=test`` only the Replay_Provider is accepted (Req 11.3); with
``--live`` the same budget gate as the Generator applies (Req 11.4).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, time
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Final

from edu_it_content.schemas import (
    BriefDraft,
    LabDraft,
    LessonDraft,
    OutlineDraft,
    ProjectDraft,
    QuizDraft,
)
from edu_it_domain.ai.budget import check_attempt
from edu_it_domain.ai.inputs import INPUT_FIELDS, InputKind
from edu_it_domain.config import AI_BUDGET_DAILY_CENTS, AI_BUDGET_PER_TRACK_CENTS
from edu_it_domain.ports.clock import Clock
from edu_it_domain.ports.generation_ledger import (
    GenerationLedger,
    JobKind,
    JobRef,
    JobStatus,
    NewJob,
)
from edu_it_domain.ports.llm_provider import (
    BudgetReachedError,
    LLMInput,
    LLMProvider,
    MalformedOutputError,
    MissingFixtureError,
    PersonalDataInRequestError,
    PromptRef,
    ProviderBindingError,
    ProviderUnavailableError,
)
from edu_it_infrastructure.clock import System_Clock
from edu_it_infrastructure.composition import (
    CompositionSettings,
    Environment,
    LLMBackend,
    LLMRole,
    ProviderSelectionError,
    build_generation_ledger,
    build_llm_provider,
)
from edu_it_infrastructure.fakes.generation_ledger import In_Memory_GenerationLedger
from edu_it_infrastructure.id_generator import Uuid_IdGenerator
from edu_it_infrastructure.llm.ledger_binding import JobScope
from edu_it_infrastructure.llm.pydantic_schema import PydanticOutputSchema
from pydantic import BaseModel

from edu_it_worker.ai.generator import ExitCode
from edu_it_worker.ai.prompt_renderer import store_prompt_renderer
from edu_it_worker.ai.prompt_store import GLOSSARY_PROMPT_ID, PromptStore, glossary_pairs

__all__ = [
    "ANSWER_PROMPT_ID",
    "CASE_KINDS",
    "CaseOutcome",
    "EvalCase",
    "EvalOutcome",
    "EvalReport",
    "EvalRequest",
    "EvalRun",
    "EvalRunner",
    "EvalSuiteError",
    "build_report",
    "default_suite_path",
    "load_suite",
    "run_eval",
]

_LOG = logging.getLogger(__name__)

#: Case kinds (design: Generator, prompts, evals and CLI).
SCHEMA_CONFORMANCE: Final = "schema-conformance"
ANSWER_KEY: Final = "answer-key"
GLOSSARY_TERMS: Final = "glossary-terms"
CASE_KINDS: Final = (ANSWER_KEY, GLOSSARY_TERMS, SCHEMA_CONFORMANCE)

#: The blind answerer prompt, the same one the Verifier uses.
ANSWER_PROMPT_ID: Final = "verify_answer"

#: Eval calls measure the first answer: no retries, so a run costs at most one attempt per case.
_EVAL_RETRIES: Final = 0

_SCHEMAS: Final[Mapping[str, type[BaseModel]]] = {
    model.__name__: model
    for model in (BriefDraft, OutlineDraft, LessonDraft, QuizDraft, LabDraft, ProjectDraft)
}
_RATE_PLACES: Final = Decimal("0.0001")
_SUITE_NAME_OK: Final = frozenset("abcdefghijklmnopqrstuvwxyz0123456789-_")


class EvalSuiteError(ValueError):
    """The suite file or one of its cases is not usable."""


# --- Suite ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EvalCase:
    """One case of a suite."""

    id: str
    kind: str
    data: Mapping[str, object] = field(default_factory=dict)
    """The kind-specific fields of the case, as read from the suite file."""

    @property
    def uses_model(self) -> bool:
        return self.kind != GLOSSARY_TERMS


def default_suite_path(repo: Path, suite: str) -> Path:
    """Return ``evals/suites/<suite>.json`` under ``repo``; refuse names that could escape it."""
    if not suite or not set(suite) <= _SUITE_NAME_OK:
        msg = f"invalid suite name {suite!r}"
        raise EvalSuiteError(msg)
    return repo / "evals" / "suites" / f"{suite}.json"


def _str_field(case: Mapping[str, object], key: str, where: str) -> str:
    value = case.get(key)
    if not isinstance(value, str) or not value:
        msg = f"{where}: {key!r} must be a non-empty string"
        raise EvalSuiteError(msg)
    return value


def _parse_case(raw: object, index: int) -> EvalCase:
    if not isinstance(raw, dict):
        msg = f"case #{index} is not an object"
        raise EvalSuiteError(msg)
    case_id = _str_field(raw, "id", f"case #{index}")
    where = f"case {case_id!r}"
    kind = _str_field(raw, "kind", where)
    if kind not in CASE_KINDS:
        msg = f"{where}: unknown kind {kind!r}; expected one of {', '.join(CASE_KINDS)}"
        raise EvalSuiteError(msg)
    if kind == ANSWER_KEY:
        options, expected = raw.get("options"), raw.get("expected")
        _str_field(raw, "question", where)
        _str_field(raw, "locale", where)
        if not isinstance(options, list) or not all(isinstance(o, str) for o in options):
            msg = f"{where}: 'options' must be a list of strings"
            raise EvalSuiteError(msg)
        if (
            not isinstance(expected, list)
            or not expected
            or not all(
                isinstance(i, int) and not isinstance(i, bool) and 0 <= i < len(options)
                for i in expected
            )
        ):
            msg = f"{where}: 'expected' must be a non-empty list of valid option indexes"
            raise EvalSuiteError(msg)
    elif kind == SCHEMA_CONFORMANCE:
        _str_field(raw, "prompt_id", where)
        if _str_field(raw, "schema", where) not in _SCHEMAS:
            msg = f"{where}: unknown schema {raw['schema']!r}"
            raise EvalSuiteError(msg)
        if not isinstance(raw.get("fields"), dict):
            msg = f"{where}: 'fields' must be an object"
            raise EvalSuiteError(msg)
    else:
        texts, terms = raw.get("texts"), raw.get("terms")
        if not isinstance(texts, dict) or not all(
            isinstance(texts.get(loc), str) for loc in ("en", "fr")
        ):
            msg = f"{where}: 'texts' must hold 'en' and 'fr' strings"
            raise EvalSuiteError(msg)
        if (
            not isinstance(terms, list)
            or not terms
            or not all(
                isinstance(t, list) and len(t) == 2 and all(isinstance(x, str) for x in t)
                for t in terms
            )
        ):
            msg = f"{where}: 'terms' must be a non-empty list of [term_en, term_fr] pairs"
            raise EvalSuiteError(msg)
    return EvalCase(
        id=case_id, kind=kind, data={k: v for k, v in raw.items() if k not in ("id", "kind")}
    )


def load_suite(path: Path) -> tuple[EvalCase, ...]:
    """Read a suite file and return its cases sorted by id.

    Raises ``EvalSuiteError`` for an unreadable file, an empty suite, a malformed case or a
    duplicate id. The order of the file never matters (Req 11.5).
    """
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        msg = f"cannot read suite {path.name}: {type(error).__name__}"
        raise EvalSuiteError(msg) from error
    raw_cases = document.get("cases") if isinstance(document, dict) else None
    if not isinstance(raw_cases, list) or not raw_cases:
        msg = f"suite {path.name} has no cases"
        raise EvalSuiteError(msg)
    cases = [_parse_case(raw, i) for i, raw in enumerate(raw_cases)]
    ids = [case.id for case in cases]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        msg = f"suite {path.name} repeats case id(s): {', '.join(duplicates)}"
        raise EvalSuiteError(msg)
    return tuple(sorted(cases, key=lambda c: c.id))


# --- Report -----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CaseOutcome:
    """The result of one case."""

    case_id: str
    kind: str
    passed: bool
    detail: str


@dataclass(frozen=True, slots=True)
class EvalReport:
    """The Eval_Report: Prompt versions, case outcomes and the aggregate pass rate (Req 11.2)."""

    suite: str
    prompt_versions: tuple[tuple[str, str], ...]
    outcomes: tuple[CaseOutcome, ...]

    @property
    def passed(self) -> int:
        return sum(1 for o in self.outcomes if o.passed)

    @property
    def total(self) -> int:
        return len(self.outcomes)

    @property
    def pass_rate(self) -> str:
        """``passed / total`` to four places, rounded half up; ``0.0000`` if empty."""
        if not self.outcomes:
            return str(Decimal(0).quantize(_RATE_PLACES))
        return str(
            (Decimal(self.passed) / Decimal(self.total)).quantize(_RATE_PLACES, ROUND_HALF_UP)
        )

    def to_json(self) -> str:
        """Return the canonical text of the report: sorted keys, no time, same input same bytes."""
        document = {
            "suite": self.suite,
            "prompt_versions": dict(self.prompt_versions),
            "outcomes": [
                {"id": o.case_id, "kind": o.kind, "passed": o.passed, "detail": o.detail}
                for o in self.outcomes
            ],
            "passed": self.passed,
            "total": self.total,
            "pass_rate": self.pass_rate,
        }
        return json.dumps(document, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def build_report(
    suite: str,
    prompt_versions: Iterable[tuple[str, str]],
    outcomes: Iterable[CaseOutcome],
) -> EvalReport:
    """Assemble a report with outcomes and Prompt versions in sorted order."""
    return EvalReport(
        suite=suite,
        prompt_versions=tuple(sorted(set(prompt_versions))),
        outcomes=tuple(sorted(outcomes, key=lambda o: o.case_id)),
    )


# --- Runner -----------------------------------------------------------------


class _AnswerSchema:
    """Validates ``{"selected": [int, ...]}`` for a question with ``option_count`` options."""

    def __init__(self, option_count: int) -> None:
        self._count = option_count

    def validate(self, text: str) -> Mapping[str, object]:
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError("answer is not JSON") from exc
        selected = data.get("selected") if isinstance(data, dict) else None
        if not isinstance(selected, list) or not all(
            isinstance(i, int) and not isinstance(i, bool) and 0 <= i < self._count
            for i in selected
        ):
            raise ValueError("selected must be a list of valid option indexes")
        return {"selected": tuple(selected)}


@dataclass(frozen=True, slots=True)
class EvalRun:
    """What one run produced."""

    report: EvalReport
    budget_kind: str | None = None
    """The Budget that stopped the run, if any."""

    provider_failed: bool = False


class EvalRunner:
    """Runs the cases of a suite against a provider and the current Prompt versions."""

    def __init__(self, llm: LLMProvider, prompts: PromptStore) -> None:
        self._llm = llm
        self._prompts = prompts

    def latest_version(self, prompt_id: str) -> str:
        """Return the highest version on disk for ``prompt_id``."""
        versions = [int(v) for (pid, v) in self._prompts.available() if pid == prompt_id]
        if not versions:
            msg = f"no prompt file for {prompt_id!r}"
            raise EvalSuiteError(msg)
        return str(max(versions))

    def prompt_versions(self, cases: Sequence[EvalCase]) -> tuple[tuple[str, str], ...]:
        """Return ``(prompt_id, version)`` for every prompt the cases use, sorted."""
        ids: set[str] = set()
        for case in cases:
            if case.kind == ANSWER_KEY:
                ids.add(ANSWER_PROMPT_ID)
            elif case.kind == SCHEMA_CONFORMANCE:
                ids.add(str(case.data["prompt_id"]))
            else:
                ids.add(GLOSSARY_PROMPT_ID)
        return tuple((pid, self.latest_version(pid)) for pid in sorted(ids))

    def run(self, suite: str, cases: Sequence[EvalCase]) -> EvalRun:
        """Run ``cases`` in sorted id order and return the report.

        A budget stop or a provider failure stops the model-backed cases; they are reported as
        failed and not run. Glossary cases need no model and still run.
        """
        ordered = sorted(cases, key=lambda c: c.id)
        versions = self.prompt_versions(ordered)
        outcomes: list[CaseOutcome] = []
        budget_kind: str | None = None
        provider_failed = False
        for case in ordered:
            if case.uses_model and (budget_kind is not None or provider_failed):
                reason = f"{budget_kind} budget reached" if budget_kind else "provider failure"
                outcomes.append(CaseOutcome(case.id, case.kind, False, f"not run: {reason}"))
                continue
            try:
                outcomes.append(self._run_case(case))
            except BudgetReachedError as error:
                budget_kind = error.kind
                outcomes.append(
                    CaseOutcome(case.id, case.kind, False, f"not run: {error.kind} budget reached")
                )
            except (ProviderUnavailableError, ProviderBindingError) as error:
                provider_failed = True
                outcomes.append(
                    CaseOutcome(
                        case.id, case.kind, False, f"provider failure: {type(error).__name__}"
                    )
                )
        return EvalRun(build_report(suite, versions, outcomes), budget_kind, provider_failed)

    # -- cases -----------------------------------------------------------------------------

    def _run_case(self, case: EvalCase) -> CaseOutcome:
        if case.kind == GLOSSARY_TERMS:
            return self._glossary_terms(case)
        try:
            if case.kind == ANSWER_KEY:
                return self._answer_key(case)
            return self._schema_conformance(case)
        except MissingFixtureError as error:
            return CaseOutcome(case.id, case.kind, False, f"missing fixture: {error.key}")
        except MalformedOutputError:
            return CaseOutcome(case.id, case.kind, False, "output failed validation")
        except PersonalDataInRequestError:
            return CaseOutcome(case.id, case.kind, False, "the request carried personal data")

    def _schema_conformance(self, case: EvalCase) -> CaseOutcome:
        prompt_id = str(case.data["prompt_id"])
        version = self.latest_version(prompt_id)
        prompt = self._prompts.load(prompt_id, version)
        fields = {str(k): str(v) for k, v in dict(case.data["fields"]).items()}  # type: ignore[call-overload]
        if "glossary" in INPUT_FIELDS[InputKind(prompt.input_kind)]:
            fields["glossary"] = self.glossary_text()
        self._llm.complete(
            PromptRef(prompt_id, version),
            LLMInput(prompt.input_kind, fields),
            PydanticOutputSchema(_SCHEMAS[str(case.data["schema"])]),
            _EVAL_RETRIES,
        )
        return CaseOutcome(case.id, case.kind, True, "output validates")

    def _answer_key(self, case: EvalCase) -> CaseOutcome:
        options = [str(o) for o in case.data["options"]]  # type: ignore[attr-defined]
        expected = sorted({int(i) for i in case.data["expected"]})  # type: ignore[attr-defined]
        result = self._llm.complete(
            PromptRef(ANSWER_PROMPT_ID, self.latest_version(ANSWER_PROMPT_ID)),
            LLMInput(
                InputKind.VERIFY_ANSWER.value,
                {
                    "question": str(case.data["question"]),
                    "options": json.dumps(options, ensure_ascii=False),
                    "locale": str(case.data["locale"]),
                },
            ),
            _AnswerSchema(len(options)),
            _EVAL_RETRIES,
        )
        selected = sorted(set(result.output["selected"]))  # type: ignore[call-overload]
        if selected == expected:
            return CaseOutcome(case.id, case.kind, True, f"selected {selected}")
        return CaseOutcome(case.id, case.kind, False, f"expected {expected}, selected {selected}")

    def _glossary_terms(self, case: EvalCase) -> CaseOutcome:
        known = set(
            glossary_pairs(
                self._prompts.load(GLOSSARY_PROMPT_ID, self.latest_version(GLOSSARY_PROMPT_ID))
            )
        )
        texts: Mapping[str, str] = case.data["texts"]  # type: ignore[assignment]
        en_text, fr_text = texts["en"].casefold(), texts["fr"].casefold()
        problems: list[str] = []
        for term_en, term_fr in sorted(tuple(t) for t in case.data["terms"]):  # type: ignore[attr-defined]
            if (term_en, term_fr) not in known:
                problems.append(f"{term_en} | {term_fr}: not in the glossary")
            if term_en.casefold() not in en_text:
                problems.append(f"{term_en}: missing from the English text")
            if term_fr.casefold() not in fr_text:
                problems.append(f"{term_fr}: missing from the French text")
        if problems:
            return CaseOutcome(case.id, case.kind, False, "; ".join(problems))
        return CaseOutcome(case.id, case.kind, True, f"{len(case.data['terms'])} term pair(s)")  # type: ignore[arg-type]

    def glossary_text(self) -> str:
        """The glossary field exactly as the Generator builds it, so Fixtures are shared."""
        pairs = glossary_pairs(
            self._prompts.load(GLOSSARY_PROMPT_ID, self.latest_version(GLOSSARY_PROMPT_ID))
        )
        return json.dumps([list(pair) for pair in pairs], ensure_ascii=False)


# --- Orchestration ----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EvalRequest:
    """What one ``edu-it-eval`` invocation asks for. No field can skip the budget gate."""

    suite: str = "golden"
    live: bool = False
    cost_cap_cents: int | None = None


@dataclass(frozen=True, slots=True)
class EvalOutcome:
    """The result of one invocation."""

    exit_code: ExitCode
    message: str
    report: EvalReport | None = None
    report_path: Path | None = None


def run_eval(
    settings: CompositionSettings,
    request: EvalRequest,
    *,
    repo: Path,
    report_dir: Path | None = None,
    clock: Clock | None = None,
    ledger: GenerationLedger | None = None,
    prompts: PromptStore | None = None,
    provider_factory: Callable[[JobScope], LLMProvider] | None = None,
) -> EvalOutcome:
    """Run a suite and write its Eval_Report.

    Order: suite, provider selection, budget gate (``--live``), job, cases, report. A refusal
    before the job makes no job and no model call. Under ``EDU_IT_ENV=test`` the provider is the
    Replay_Provider, and a live request is refused by the provider selection (Req 11.3).
    ``provider_factory`` is a test seam; it is never reachable from the CLI.
    """
    live = request.live
    settings = settings.model_copy(
        update={
            "llm_provider_backend": LLMBackend.LIVE if live else LLMBackend.REPLAY,
            "llm_record": False,
        }
    )
    if live and settings.environment is Environment.TEST:
        return EvalOutcome(
            ExitCode.USAGE,
            "refusing to start: --live cannot be used with EDU_IT_ENV=test; "
            "the test environment uses the Replay_Provider only",
        )
    fixture_dir = Path(settings.llm_fixture_dir)
    if not fixture_dir.is_absolute():
        settings = settings.model_copy(update={"llm_fixture_dir": str(repo / fixture_dir)})
    if request.cost_cap_cents is not None and request.cost_cap_cents < 1:
        return EvalOutcome(ExitCode.USAGE, "--cost-cap-usd must be at least USD 0.01")

    try:
        cases = load_suite(default_suite_path(repo, request.suite))
    except EvalSuiteError as error:
        return EvalOutcome(ExitCode.USAGE, f"cannot load the suite: {error}")

    clock = clock if clock is not None else System_Clock()
    # A replay run spends nothing, so it needs no database; a live run records real spend.
    ledger = (
        ledger
        if ledger is not None
        else (
            build_generation_ledger(settings, clock=clock)
            if live
            else In_Memory_GenerationLedger(clock)
        )
    )
    prompts = prompts if prompts is not None else PromptStore()
    renderer = store_prompt_renderer(prompts)

    def make(scope: JobScope) -> LLMProvider:
        if provider_factory is not None:
            return provider_factory(scope)
        return build_llm_provider(settings, scope, role=LLMRole.VERIFIER, prompt_renderer=renderer)

    # Provider selection against a probe scope surfaces a refusal before any job exists.
    try:
        make(JobScope(ledger=ledger, job=JobRef("preflight"), clock=clock))
    except ProviderSelectionError as error:
        return EvalOutcome(ExitCode.USAGE, f"refusing to start: {error}")

    now = clock.now()
    day_start = datetime.combine(now.date(), time.min, tzinfo=now.tzinfo)
    if live:
        decision = check_attempt(
            daily_spent_cents=ledger.spent_today(day_start),
            track_spent_cents=0,
            job_spent_cents=0,
            cost_cap_cents=request.cost_cap_cents,
            daily_limit_cents=AI_BUDGET_DAILY_CENTS,
            track_limit_cents=AI_BUDGET_PER_TRACK_CENTS,
        )
        if decision.reached is not None:
            return EvalOutcome(
                ExitCode.BUDGET,
                f"budget reached: {decision.reached.value}. No job or model call was made.",
            )

    job = ledger.open_job(
        NewJob(
            stable_id=Uuid_IdGenerator().new_id(),
            kind=JobKind.EVAL,
            cost_cap_cents=request.cost_cap_cents,
        )
    )
    scope = JobScope(ledger=ledger, job=job, clock=clock, cost_cap_cents=request.cost_cap_cents)
    try:
        run = EvalRunner(make(scope), prompts).run(request.suite, cases)
    except EvalSuiteError as error:
        ledger.close_job(job, JobStatus.FAILED)
        return EvalOutcome(ExitCode.USAGE, f"cannot run the suite: {error}")
    except Exception as error:
        _LOG.error("eval run failed: %s", type(error).__name__)
        ledger.close_job(job, JobStatus.FAILED)
        raise

    status = (
        JobStatus.BUDGET_REACHED
        if run.budget_kind
        else JobStatus.FAILED
        if run.provider_failed
        else JobStatus.COMPLETED
    )
    ledger.close_job(job, status)

    target = (report_dir if report_dir is not None else repo / "evals" / "reports") / (
        f"{request.suite}.{'live' if live else 'replay'}.json"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(run.report.to_json(), encoding="utf-8", newline="\n")

    report = run.report
    summary = (
        f"{request.suite}: {report.passed}/{report.total} passed (pass rate {report.pass_rate})"
    )
    if run.budget_kind:
        return EvalOutcome(
            ExitCode.BUDGET, f"budget reached: {run.budget_kind}. {summary}", report, target
        )
    if run.provider_failed:
        return EvalOutcome(ExitCode.FAILED, f"provider failure. {summary}", report, target)
    code = ExitCode.OK if report.passed == report.total else ExitCode.FAILED
    return EvalOutcome(code, summary, report, target)
