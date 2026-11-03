#!/usr/bin/env python3
"""Capped vendor smoke call for the Live_Provider (ai-content task 4.12; Req 1.7, 1.11, 10.2).

The OWNER runs this by hand to prove the configured vendor works end to end. It is never
imported by a test run and never part of CI: it makes one real, paid network call.

It makes exactly one tiny call (no retries) through the same ``build_llm_provider``
composition the Generator uses, so the provider binding (allowed hosts, credential
fingerprint), the budget gate (daily, per-track, job Cost_Cap) and the usage ledger all apply.
The Cost_Cap is given on the command line in whole cents and is limited to a few cents.

Gate (ADR 0007, Addendum 1): the vendor addendum must be Accepted. It is, for Amazon Bedrock.

Usage::

    LLM_PROVIDER_BACKEND=live EDU_IT_ENV=development ...
    python tools/llm_smoke.py --cost-cap-cents 3

Settings come from the environment, as for the worker (see ``.env.example``). The spend is
recorded in the ``budget_ledger`` of ``DATABASE_URL`` so the daily Budget sees it; pass
``--in-memory-ledger`` only when no database is available (the daily and per-track Budgets
then cannot see earlier spend). The API key is never printed.

Exit status: 0 on success, 1 when the call failed or a Budget stopped it, 2 on a refusal or
bad usage before any call.
"""

from __future__ import annotations

import argparse
import sys
import uuid
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Final

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
    PromptRef,
    ProviderBindingError,
    ProviderUnavailableError,
)
from edu_it_infrastructure.clock import System_Clock
from edu_it_infrastructure.composition import (
    CompositionSettings,
    LLMBackend,
    ProviderSelectionError,
    build_llm_provider,
)
from edu_it_infrastructure.fakes.generation_ledger import In_Memory_GenerationLedger
from edu_it_infrastructure.llm.ledger_binding import JobScope
from edu_it_infrastructure.llm.pydantic_schema import PydanticOutputSchema
from edu_it_infrastructure.llm.transport import RenderedPrompt
from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from edu_it_domain.ports.clock import Clock

__all__ = ["MAX_COST_CAP_CENTS", "main", "parse_cost_cap"]

#: "A few cents": the largest Cost_Cap this script accepts.
MAX_COST_CAP_CENTS: Final = 10

#: Prompt pin of the smoke call. It is not a file under ``prompts/``; the call carries one line.
SMOKE_PROMPT: Final = PromptRef(prompt_id="smoke", prompt_version="1")


def _render_smoke_prompt(prompt: PromptRef) -> RenderedPrompt:
    """The smoke call's one-line system prompt (it has no file under ``prompts/``)."""
    return RenderedPrompt(
        text=(
            f"You are a connectivity check for prompt {prompt.prompt_id} "
            f"version {prompt.prompt_version}. Reply with a single JSON object and nothing else."
        ),
        output_schema="smoke_reply",
    )


_PLACEHOLDER_KEYS: Final = frozenset({"change-me", "changeme"})

EXIT_OK: Final = 0
EXIT_FAILED: Final = 1
EXIT_REFUSED: Final = 2


class _SmokeReply(BaseModel):
    """The only shape the smoke call accepts back."""

    model_config = ConfigDict(extra="forbid")

    ok: bool


def parse_cost_cap(value: str) -> int:
    """Return the Cost_Cap in whole cents; raise ``argparse.ArgumentTypeError`` if unusable."""
    try:
        cents = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("the Cost_Cap must be a whole number of cents") from None
    if not 1 <= cents <= MAX_COST_CAP_CENTS:
        raise argparse.ArgumentTypeError(
            f"the Cost_Cap must be between 1 and {MAX_COST_CAP_CENTS} cents for a smoke call"
        )
    return cents


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="llm_smoke.py",
        description="One tiny, capped call through the Live_Provider (run by hand, never in CI).",
    )
    parser.add_argument(
        "--cost-cap-cents",
        type=parse_cost_cap,
        required=True,
        metavar="CENTS",
        help=f"Cost_Cap of the call in whole cents (1 to {MAX_COST_CAP_CENTS}).",
    )
    parser.add_argument(
        "--in-memory-ledger",
        action="store_true",
        help="Record spend in memory instead of the database (daily Budget cannot see history).",
    )
    return parser


def _postgres_ledger(database_url: str, clock: Clock) -> GenerationLedger:
    from edu_it_infrastructure.persistence.generation_ledger import Postgres_GenerationLedger
    from sqlalchemy import create_engine

    url = database_url
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://") :]
    return Postgres_GenerationLedger(create_engine(url), clock)


def _refusal(settings: CompositionSettings) -> str | None:
    """Return why the call must not be made, or ``None`` when the settings are usable."""
    if settings.llm_provider_backend is not LLMBackend.LIVE:
        return "refusing: set LLM_PROVIDER_BACKEND=live (this script only calls the live vendor)"
    key = settings.llm_provider_api_key
    if key is not None and key.get_secret_value().strip().casefold() in _PLACEHOLDER_KEYS:
        return "refusing: LLM_PROVIDER_API_KEY is still the .env.example placeholder"
    return None


def _run_call(provider: LLMProvider) -> tuple[bool, str | None, JobStatus]:
    """Make the single call; return ``(ok, failure, job status if failed)``. Never retries."""
    request = LLMInput(
        kind="eval",
        fields={
            "case_id": "smoke",
            "locale": "en",
            "prompt_input": 'Reply with exactly this JSON object and nothing else: {"ok": true}',
        },
    )
    try:
        result = provider.complete(SMOKE_PROMPT, request, PydanticOutputSchema(_SmokeReply), 0)
    except BudgetReachedError as err:
        return False, f"stopped by the {err.kind} Budget before the call", JobStatus.BUDGET_REACHED
    except ProviderBindingError as err:
        return False, f"provider binding failed: {err}", JobStatus.FAILED
    except ProviderUnavailableError as err:
        return False, f"provider unavailable: {err}", JobStatus.FAILED
    except MalformedOutputError as err:
        return False, f"the reply did not match the expected shape: {err}", JobStatus.FAILED
    return bool(result.output.get("ok")), None, JobStatus.FAILED


def main(
    argv: Sequence[str] | None = None,
    *,
    env: dict[str, str] | None = None,
    ledger_factory: Callable[[CompositionSettings, Clock], GenerationLedger] | None = None,
    provider_factory: Callable[[CompositionSettings, JobScope], LLMProvider] | None = None,
) -> int:
    """Run the smoke call. ``env``, ``ledger_factory`` and ``provider_factory`` are test seams."""
    args = _build_parser().parse_args(argv)
    try:
        settings = CompositionSettings.from_env(env)
    except ValueError as err:
        print(f"refusing: invalid settings: {err}", file=sys.stderr)
        return EXIT_REFUSED
    reason = _refusal(settings)
    if reason is not None:
        print(reason, file=sys.stderr)
        return EXIT_REFUSED

    clock = System_Clock()
    ledger: GenerationLedger
    if ledger_factory is not None:
        ledger = ledger_factory(settings, clock)
    elif args.in_memory_ledger:
        print("note: in-memory ledger; the daily and per-track Budgets see no earlier spend")
        ledger = In_Memory_GenerationLedger(clock)
    else:
        ledger = _postgres_ledger(settings.database_url, clock)

    job = ledger.open_job(
        NewJob(
            stable_id=uuid.uuid4().hex,
            kind=JobKind.EVAL,
            prompt_id=SMOKE_PROMPT.prompt_id,
            prompt_version=SMOKE_PROMPT.prompt_version,
            cost_cap_cents=args.cost_cap_cents,
        )
    )
    scope = JobScope(ledger=ledger, job=job, clock=clock, cost_cap_cents=args.cost_cap_cents)
    build = provider_factory or (
        lambda s, sc: build_llm_provider(s, sc, prompt_renderer=_render_smoke_prompt)
    )
    try:
        provider = build(settings, scope)
    except ProviderSelectionError as err:
        ledger.close_job(job, JobStatus.FAILED)
        print(f"refusing: {err}", file=sys.stderr)
        return EXIT_REFUSED

    ok, failure, status = _run_call(provider)
    return _finish(
        ledger, job, args.cost_cap_cents, ok=ok, failure=failure, status_if_failed=status
    )


def _finish(
    ledger: GenerationLedger,
    job: JobRef,
    cap: int,
    *,
    ok: bool,
    failure: str | None,
    status_if_failed: JobStatus,
) -> int:
    spent = ledger.spent_for_job(job)
    if failure is None and not ok:
        failure = "the reply parsed but did not say ok"
    ledger.close_job(job, JobStatus.COMPLETED if failure is None else status_if_failed)
    print(f"cost: {spent} cent(s) of a {cap} cent Cost_Cap (rounded up per attempt)")
    if spent > cap:
        print("warning: the single attempt cost more than the Cost_Cap", file=sys.stderr)
    if failure is not None:
        print(f"smoke call failed: {failure}", file=sys.stderr)
        return EXIT_FAILED
    print("smoke call ok")
    return EXIT_OK


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    raise SystemExit(main())
