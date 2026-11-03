"""``edu-it-generate``: generate one Stage of one track (ai-content Req 5, 10, 12).

::

    edu-it-generate --track T --stage {brief,outline,lessons,quizzes,labs,project}
                    [--live | --record] [--cost-cap-usd N] [--limit-units N] [--base-ref REF]

Without ``--live`` or ``--record`` the Replay_Provider is used. ``--live`` selects the
Live_Provider and ``--record`` selects it and also writes Fixtures. ``--base-ref`` names a ref
that Stage_Markers and existing items are read from, such as the Generation_Branch of the
preceding Stage. There is no option that skips the stage gate or the budget gate.

Exit codes: 0 ok, 1 provider failure or discarded units, 2 usage or Config, 3 stage gate,
4 Budget reached, 130 interrupted.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Final

from edu_it_application.ai import VerifyItem, VerifyLab
from edu_it_domain.ai.generation import STAGES
from edu_it_domain.ports.generation_ledger import JobRef
from edu_it_infrastructure.clock import System_Clock
from edu_it_infrastructure.composition import (
    CompositionSettings,
    LLMBackend,
    LLMRole,
    ProviderSelectionError,
    build_generation_ledger,
    build_llm_provider,
    build_sandbox_provider,
)
from edu_it_infrastructure.git_workspace import SubprocessGitWorkspace
from edu_it_infrastructure.id_generator import Uuid_IdGenerator
from edu_it_infrastructure.llm.ledger_binding import JobScope

from edu_it_worker.ai.eval_runner import EvalRequest, run_eval
from edu_it_worker.ai.generator import (
    ExitCode,
    GenerationOutcome,
    GenerationRequest,
    Generator,
    GeneratorDeps,
    RunProviders,
)
from edu_it_worker.ai.prompt_renderer import store_prompt_renderer
from edu_it_worker.ai.prompt_store import PromptStore

__all__ = [
    "build_eval_parser",
    "build_generator",
    "build_parser",
    "cost_cap_cents",
    "entry",
    "eval_entry",
]

_REPO_ROOT_ENV: Final = "EDU_IT_REPO_ROOT"
_BASE_BRANCH_ENV: Final = "GENERATOR_BASE_BRANCH"
_CENTS: Final = Decimal(100)


def cost_cap_cents(text: str) -> int:
    """Return a Cost_Cap given in USD as whole cents (rounded half up)."""
    try:
        usd = Decimal(text)
    except InvalidOperation:
        msg = f"not a number: {text!r}"
        raise argparse.ArgumentTypeError(msg) from None
    if not usd.is_finite() or usd <= 0:
        msg = "the cost cap must be a positive amount in USD"
        raise argparse.ArgumentTypeError(msg)
    cents = int((usd * _CENTS).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    if cents < 1:
        msg = "the cost cap must be at least USD 0.01"
        raise argparse.ArgumentTypeError(msg)
    return cents


def build_parser() -> argparse.ArgumentParser:
    """Return the argument parser of ``edu-it-generate``."""
    parser = argparse.ArgumentParser(
        prog="edu-it-generate",
        description="Generate one Stage of one track on a new Generation_Branch.",
    )
    parser.add_argument("--track", required=True, help="track directory under content/")
    parser.add_argument("--stage", required=True, choices=STAGES)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--live", action="store_true", help="use the Live_Provider")
    mode.add_argument(
        "--record", action="store_true", help="use the Live_Provider and write Fixtures"
    )
    parser.add_argument(
        "--cost-cap-usd", type=cost_cap_cents, default=None, help="Cost_Cap for this run, in USD"
    )
    parser.add_argument(
        "--limit-units", type=int, default=None, help="generate at most this many units"
    )
    parser.add_argument(
        "--base-ref",
        default=None,
        help="ref to read Stage_Markers and existing items from; a source of files only",
    )
    return parser


def build_generator(settings: CompositionSettings, *, repo: Path) -> Generator:
    """Wire the Generator from ``settings``.

    Building the providers once against a probe scope surfaces a refused provider
    selection before any job or branch exists.
    """
    clock = System_Clock()
    ledger = build_generation_ledger(settings, clock=clock)
    git = SubprocessGitWorkspace(repo)
    base = os.environ.get(_BASE_BRANCH_ENV) or git.default_branch()
    if not base:
        msg = f"no integration branch found; set {_BASE_BRANCH_ENV}"
        raise ProviderSelectionError(msg)
    prompts = PromptStore()
    renderer = store_prompt_renderer(prompts)
    sandbox = build_sandbox_provider(settings, clock=clock)
    distinct = settings.llm_model_verifier not in (None, settings.llm_model_generation)

    def wire(scope: JobScope) -> RunProviders:
        drafting = build_llm_provider(
            settings, scope, role=LLMRole.GENERATION, prompt_renderer=renderer
        )
        answering = build_llm_provider(
            settings, scope, role=LLMRole.VERIFIER, prompt_renderer=renderer
        )
        verifier = VerifyItem(
            independent_llm=answering,
            sandbox=sandbox,
            lab_verifier=VerifyLab(sandbox=sandbox, clock=clock),
            independent_is_distinct=distinct,
        )
        return RunProviders(drafting, verifier)

    generator = Generator(
        GeneratorDeps(
            git=git,
            ledger=ledger,
            clock=clock,
            ids=Uuid_IdGenerator(),
            prompts=prompts,
            wire=wire,
            base_branch=base,
        )
    )
    wire(JobScope(ledger=ledger, job=JobRef("preflight"), clock=clock))
    return generator


def _print_outcome(outcome: GenerationOutcome) -> None:
    stream = sys.stdout if outcome.exit_code is ExitCode.OK else sys.stderr
    print(outcome.message, file=stream)
    for unit in outcome.discarded:
        print(f"  discarded {unit.stable_id}: {unit.reason}", file=stream)
    for unit_not_generated in outcome.not_generated:
        print(
            f"  not generated {unit_not_generated.stable_id}: {unit_not_generated.reason}",
            file=stream,
        )
    if outcome.run_id is not None:
        print(f"run {outcome.run_id}; findings recorded: {outcome.findings}", file=stream)


def entry(argv: Sequence[str] | None = None) -> int:
    """Run ``edu-it-generate`` and return its exit code."""
    args = build_parser().parse_args(argv)
    live = bool(args.live or args.record)
    try:
        settings = CompositionSettings.from_env().model_copy(
            update={
                "llm_provider_backend": LLMBackend.LIVE if live else LLMBackend.REPLAY,
                "llm_record": bool(args.record),
            }
        )
        repo = Path(os.environ.get(_REPO_ROOT_ENV) or Path(__file__).resolve().parents[4])
        generator = build_generator(settings, repo=repo)
    except (ProviderSelectionError, ValueError) as error:
        print(f"refusing to start: {error}", file=sys.stderr)
        return int(ExitCode.USAGE)

    outcome = generator.run(
        GenerationRequest(
            track=args.track,
            stage=args.stage,
            live=live,
            cost_cap_cents=args.cost_cap_usd,
            limit_units=args.limit_units,
            base_ref=args.base_ref,
        )
    )
    _print_outcome(outcome)
    return int(outcome.exit_code)


def build_eval_parser() -> argparse.ArgumentParser:
    """Return the argument parser of ``edu-it-eval``."""
    parser = argparse.ArgumentParser(
        prog="edu-it-eval",
        description="Run an Eval_Suite against the current Prompts and write the Eval_Report.",
    )
    parser.add_argument("--suite", default="golden", help="suite under evals/suites/")
    parser.add_argument(
        "--live",
        action="store_true",
        help="use the Live_Provider, under the Budgets (refused when EDU_IT_ENV=test)",
    )
    parser.add_argument(
        "--cost-cap-usd", type=cost_cap_cents, default=None, help="Cost_Cap for this run, in USD"
    )
    return parser


def eval_entry(argv: Sequence[str] | None = None) -> int:
    """Run ``edu-it-eval`` and return its exit code (same codes as ``edu-it-generate``)."""
    args = build_eval_parser().parse_args(argv)
    try:
        settings = CompositionSettings.from_env()
    except ValueError as error:
        print(f"refusing to start: {error}", file=sys.stderr)
        return int(ExitCode.USAGE)
    repo = Path(os.environ.get(_REPO_ROOT_ENV) or Path(__file__).resolve().parents[4])
    try:
        outcome = run_eval(
            settings,
            EvalRequest(suite=args.suite, live=bool(args.live), cost_cap_cents=args.cost_cap_usd),
            repo=repo,
        )
    except ProviderSelectionError as error:
        print(f"refusing to start: {error}", file=sys.stderr)
        return int(ExitCode.USAGE)
    stream = sys.stdout if outcome.exit_code is ExitCode.OK else sys.stderr
    print(outcome.message, file=stream)
    if outcome.report is not None:
        for case in outcome.report.outcomes:
            if not case.passed:
                print(f"  FAIL {case.case_id}: {case.detail}", file=stream)
    if outcome.report_path is not None:
        print(f"report: {outcome.report_path}", file=stream)
    return int(outcome.exit_code)


if __name__ == "__main__":
    sys.exit(entry())
