"""AI worker composition: validate Config, wire the refill consumers (ai-content Req 13).

:func:`build_ai_worker` validates the AI Config first and refuses to start on an invalid one
(naming every offending key), then builds the providers once against a probe scope so that a
refused provider selection also fails at start, before any job exists. It assembles
``RefillVariantPool``, the two refill consumers and a :class:`Scheduler` for
``ai.refill.cycle``. Ports are injected, as in ``build_lab_worker``.

The worker reads questions only through :class:`ServableCatalog` (``AiWorker.servable``): the
same approved-only, unflagged filter as the API, with ``variant_ref`` carried (Req 14.1, 14.5,
20.4). No LLM is involved in serving.
"""

from __future__ import annotations

import json
import logging
import secrets
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from edu_it_application.ai import (
    EligibleItems,
    EvaluateFlags,
    FlagItem,
    FoldObservations,
    PurgeAiData,
    RefillVariantPool,
    RunPathAdvisor,
    ServableCatalog,
    VerifyItem,
    VerifyLab,
)
from edu_it_domain import config as _domain_config
from edu_it_domain.config import AiConfigError, validate_ai_config
from edu_it_domain.ports.generation_ledger import JobRef
from edu_it_infrastructure.composition import (
    CompositionSettings,
    LLMRole,
    ProviderSelectionError,
    build_llm_provider,
)
from edu_it_infrastructure.llm.ledger_binding import JobScope

from edu_it_worker.ai.advisor_jobs import (
    ADVISE_CYCLE_JOB,
    ADVISE_USER_JOB,
    AdviseUser,
    AdvisorTargets,
    ScanAdvisorLearners,
    advise_cycle_handler,
    advise_user_handler,
)
from edu_it_worker.ai.item_info import Catalog_ItemInfoSource
from edu_it_worker.ai.jobs import (
    REFILL_CYCLE_JOB,
    REFILL_PARENT_JOB,
    RefillParent,
    ScanRefillParents,
    refill_bucket,
    refill_cycle_handler,
    refill_parent_dedupe_key,
    refill_parent_handler,
)
from edu_it_worker.ai.prompt_renderer import store_prompt_renderer
from edu_it_worker.ai.prompt_store import GLOSSARY_PROMPT_ID, PromptStore, glossary_pairs
from edu_it_worker.ai.quality_jobs import (
    QUALITY_CYCLE_JOB,
    QUALITY_EVALUATE_ITEM_JOB,
    EvaluateItem,
    RunQualityCycle,
    quality_cycle_handler,
    quality_evaluate_item_handler,
)
from edu_it_worker.ai.retention_jobs import RETAIN_JOB, retain_handler
from edu_it_worker.labs.scheduler import PeriodicJob, Scheduler

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from edu_it_domain.ports.catalog import CatalogRepository
    from edu_it_domain.ports.clock import Clock
    from edu_it_domain.ports.generation_ledger import GenerationLedger
    from edu_it_domain.ports.id_generator import IdGenerator
    from edu_it_domain.ports.item_flags import ItemFlagReader
    from edu_it_domain.ports.item_quality import ItemQualityRepository
    from edu_it_domain.ports.lab_catalog import LabCatalog
    from edu_it_domain.ports.lessons import LessonRepository
    from edu_it_domain.ports.llm_provider import LLMProvider
    from edu_it_domain.ports.mastery import MasteryRepository
    from edu_it_domain.ports.parent_questions import ParentQuestionSource
    from edu_it_domain.ports.path_recommendations import PathRecommendationRepository
    from edu_it_domain.ports.problem_reports import ProblemReportRepository
    from edu_it_domain.ports.review import ReviewRepository
    from edu_it_domain.ports.review_tasks import ReviewTaskRepository
    from edu_it_domain.ports.sandbox import SandboxProvider
    from edu_it_domain.ports.task_queue import JobHandler, TaskQueue
    from edu_it_domain.ports.variants import VariantRepository

__all__ = [
    "AdvisorPorts",
    "AiWorker",
    "ProviderWiring",
    "QualityPorts",
    "RetentionPorts",
    "build_ai_worker",
]

#: Builds the ``(drafting, answering)`` providers bound to one job's scope.
type ProviderWiring = Callable[[JobScope], tuple[LLMProvider, LLMProvider]]


@dataclass(frozen=True, slots=True)
class QualityPorts:
    """The ports of the quality loop (task 21.2): statistics, reports and item lookups."""

    quality: ItemQualityRepository
    reports: ProblemReportRepository
    lessons: LessonRepository
    labs: LabCatalog


@dataclass(frozen=True, slots=True)
class AdvisorPorts:
    """The ports of the path advisor (task 22.3)."""

    targets: AdvisorTargets
    mastery: MasteryRepository
    reviews: ReviewRepository
    recommendations: PathRecommendationRepository


@dataclass(frozen=True, slots=True)
class RetentionPorts:
    """The repositories that own the AI personal-data tables (task 23.1)."""

    quality: ItemQualityRepository
    reports: ProblemReportRepository
    recommendations: PathRecommendationRepository


@dataclass(frozen=True, slots=True)
class AiWorker:
    """The assembled AI consumers and scheduler."""

    queue: TaskQueue
    scheduler: Scheduler
    consumers: Mapping[str, JobHandler]
    servable: ServableCatalog
    """The catalog the worker builds review and practice sets from (approved, unflagged)."""

    threads: list[threading.Thread] = field(default_factory=list)

    def start(self) -> None:
        """Start one consumer thread per job and the scheduler thread."""
        for job_name, handler in self.consumers.items():
            thread = threading.Thread(
                target=self.queue.consume,
                args=(job_name, handler),
                name=f"consumer-{job_name}",
                daemon=True,
            )
            thread.start()
            self.threads.append(thread)
        self.scheduler.start()

    def stop_consuming(self) -> None:
        """Stop the scheduler and the queue's delivery loops; running handlers finish."""
        self.scheduler.stop()
        stop = getattr(self.queue, "stop", None)
        if callable(stop):
            stop()

    def in_flight(self) -> bool:
        """Whether a consumer thread is still inside a handler."""
        return any(thread.is_alive() for thread in self.threads)

    def stop(self) -> None:
        """Stop the scheduler and ask the queue (when it supports it) to stop consuming."""
        self.scheduler.stop()
        stop = getattr(self.queue, "stop", None)
        if callable(stop):
            stop()
        for thread in self.threads:
            thread.join(timeout=5.0)
        self.threads.clear()


def _glossary_text(prompts: PromptStore) -> str:
    versions = [int(v) for (pid, v) in prompts.available() if pid == GLOSSARY_PROMPT_ID]
    if not versions:
        raise LookupError(f"no prompt file for {GLOSSARY_PROMPT_ID!r}")
    pairs = glossary_pairs(prompts.load(GLOSSARY_PROMPT_ID, str(max(versions))))
    return json.dumps([list(pair) for pair in pairs], ensure_ascii=False)


def _advisor_handle_key(settings: CompositionSettings) -> str:
    """The key of the advisor handles; production refuses to start without it.

    Outside production a missing key is replaced by a random per-process one: handles differ
    between runs anyway, and nothing needs to re-derive them.
    """
    secret = settings.advisor_handle_key
    key = secret.get_secret_value() if secret is not None else ""
    if key:
        return key
    if settings.environment.is_production:
        logging.getLogger(__name__).critical("refusing to start: ADVISOR_HANDLE_KEY is not set")
        raise ProviderSelectionError(
            "refusing to start: ADVISOR_HANDLE_KEY is required with EDU_IT_ENV=production"
        )
    return secrets.token_hex(32)


def _advisor_consumers(
    ports: AdvisorPorts,
    *,
    queue: TaskQueue,
    clock: Clock,
    ids: IdGenerator,
    ledger: GenerationLedger,
    servable: ServableCatalog,
    providers: ProviderWiring,
    handle_key: str,
    ai_config: object,
) -> dict[str, JobHandler]:
    """The advisor consumers; the advisor reads concepts through the ``ServableCatalog``."""

    def build_advisor(scope: JobScope) -> RunPathAdvisor:
        llm, _answering = providers(scope)
        return RunPathAdvisor(
            llm=llm,
            eligible=EligibleItems(servable, ports.mastery),
            catalog=servable,
            mastery=ports.mastery,
            reviews=ports.reviews,
            recommendations=ports.recommendations,
            clock=clock,
            ids=ids,
            handle_key=handle_key,
            config=ai_config,
        )

    return {
        ADVISE_CYCLE_JOB: advise_cycle_handler(
            ScanAdvisorLearners(targets=ports.targets, queue=queue, clock=clock, config=ai_config)
        ),
        ADVISE_USER_JOB: advise_user_handler(
            AdviseUser(
                targets=ports.targets,
                ledger=ledger,
                clock=clock,
                ids=ids,
                build_advisor=build_advisor,
            )
        ),
    }


def _quality_consumers(
    ports: QualityPorts,
    *,
    queue: TaskQueue,
    clock: Clock,
    ids: IdGenerator,
    variants: VariantRepository,
    review_tasks: ReviewTaskRepository,
    catalog: CatalogRepository,
    parents: ParentQuestionSource,
    ai_config: object,
) -> dict[str, JobHandler]:
    """The quality-loop consumers: no LLM provider is built or reachable from here."""
    items = Catalog_ItemInfoSource(
        catalog=catalog,
        lessons=ports.lessons,
        labs=ports.labs,
        parents=parents,
        variants=variants,
    )

    def request_refill(parent: str) -> None:
        queue.enqueue(
            REFILL_PARENT_JOB,
            {"parent_stable_id": parent},
            dedupe_key=refill_parent_dedupe_key(parent, refill_bucket(clock, ai_config)),
        )

    evaluate = EvaluateFlags(
        quality=ports.quality,
        reports=ports.reports,
        items=items,
        flag_item=FlagItem(
            flags=ports.quality,
            tasks=review_tasks,
            variants=variants,
            items=items,
            ids=ids,
            request_refill=request_refill,
        ),
    )
    return {
        QUALITY_CYCLE_JOB: quality_cycle_handler(
            RunQualityCycle(fold=FoldObservations(quality=ports.quality), evaluate=evaluate)
        ),
        QUALITY_EVALUATE_ITEM_JOB: quality_evaluate_item_handler(EvaluateItem(evaluate=evaluate)),
    }


def build_ai_worker(
    *,
    queue: TaskQueue,
    clock: Clock,
    ids: IdGenerator,
    ledger: GenerationLedger,
    parents: ParentQuestionSource,
    variants: VariantRepository,
    review_tasks: ReviewTaskRepository,
    flags: ItemFlagReader,
    catalog: CatalogRepository,
    sandbox: SandboxProvider,
    quality_ports: QualityPorts | None = None,
    advisor_ports: AdvisorPorts | None = None,
    retention_ports: RetentionPorts | None = None,
    settings: CompositionSettings | None = None,
    prompts: PromptStore | None = None,
    wire: ProviderWiring | None = None,
    ai_config: object = _domain_config,
    poll_interval_s: float = 1.0,
) -> AiWorker:
    """Validate Config, check the providers and register the refill consumers.

    Raises :class:`AiConfigError` for an invalid Config and ``ProviderSelectionError`` for a
    refused provider selection, both before any job or model call. ``wire`` overrides provider
    selection (tests).
    """
    try:
        validate_ai_config(ai_config)
    except AiConfigError as exc:
        logging.getLogger(__name__).critical(
            "refusing to start: invalid AI Config", extra={"keys": list(exc.keys)}
        )
        raise
    settings = settings if settings is not None else CompositionSettings.from_env()
    handle_key = _advisor_handle_key(settings) if advisor_ports is not None else ""
    prompts = prompts if prompts is not None else PromptStore()

    if wire is None:
        renderer = store_prompt_renderer(prompts)

        def wire(scope: JobScope) -> tuple[LLMProvider, LLMProvider]:
            return (
                build_llm_provider(
                    settings, scope, role=LLMRole.GENERATION, prompt_renderer=renderer
                ),
                build_llm_provider(
                    settings, scope, role=LLMRole.VERIFIER, prompt_renderer=renderer
                ),
            )

    distinct = settings.llm_model_verifier not in (None, settings.llm_model_generation)
    glossary = _glossary_text(prompts)
    providers = wire

    def build_pool(scope: JobScope) -> RefillVariantPool:
        drafting, answering = providers(scope)
        return RefillVariantPool(
            llm=drafting,
            verifier=VerifyItem(
                independent_llm=answering,
                sandbox=sandbox,
                lab_verifier=VerifyLab(sandbox=sandbox, clock=clock),
                independent_is_distinct=distinct,
                config=ai_config,
            ),
            parents=parents,
            variants=variants,
            tasks=review_tasks,
            flags=flags,
            clock=clock,
            ids=ids,
            glossary=glossary,
            config=ai_config,
        )

    providers(JobScope(ledger=ledger, job=JobRef("preflight"), clock=clock))

    consumers: dict[str, JobHandler] = {
        REFILL_CYCLE_JOB: refill_cycle_handler(
            ScanRefillParents(
                parents=parents,
                variants=variants,
                flags=flags,
                queue=queue,
                clock=clock,
                config=ai_config,
            )
        ),
        REFILL_PARENT_JOB: refill_parent_handler(
            RefillParent(
                parents=parents, ledger=ledger, clock=clock, ids=ids, build_pool=build_pool
            )
        ),
    }
    periodic = [PeriodicJob(REFILL_CYCLE_JOB, ai_config.REFILL_CYCLE_INTERVAL)]  # type: ignore[attr-defined]
    if quality_ports is not None:
        consumers.update(
            _quality_consumers(
                quality_ports,
                queue=queue,
                clock=clock,
                ids=ids,
                variants=variants,
                review_tasks=review_tasks,
                catalog=catalog,
                parents=parents,
                ai_config=ai_config,
            )
        )
        periodic.append(PeriodicJob(QUALITY_CYCLE_JOB, ai_config.QUALITY_CYCLE_INTERVAL))  # type: ignore[attr-defined]
    servable = ServableCatalog(catalog, flags, variants)
    if advisor_ports is not None:
        consumers.update(
            _advisor_consumers(
                advisor_ports,
                queue=queue,
                clock=clock,
                ids=ids,
                ledger=ledger,
                servable=servable,
                providers=providers,
                handle_key=handle_key,
                ai_config=ai_config,
            )
        )
        periodic.append(PeriodicJob(ADVISE_CYCLE_JOB, ai_config.ADVISOR_CYCLE_INTERVAL))  # type: ignore[attr-defined]
    if retention_ports is not None:
        consumers[RETAIN_JOB] = retain_handler(
            PurgeAiData(
                quality=retention_ports.quality,
                reports=retention_ports.reports,
                recommendations=retention_ports.recommendations,
                clock=clock,
                config=ai_config,
            )
        )
        periodic.append(PeriodicJob(RETAIN_JOB, ai_config.AI_RETENTION_INTERVAL))  # type: ignore[attr-defined]
    scheduler = Scheduler(
        queue=queue,
        clock=clock,
        jobs=periodic,
        poll_interval_s=poll_interval_s,
    )
    return AiWorker(
        queue=queue,
        scheduler=scheduler,
        consumers=consumers,
        servable=servable,
    )
