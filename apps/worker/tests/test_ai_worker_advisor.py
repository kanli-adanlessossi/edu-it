"""Tests for the advisor jobs of the AI worker (ai-content task 22.3).

Run against the in-memory ports and the fake provider; no network. ``ai.advise.cycle`` enqueues
one ``ai.advise.user`` per learner with activity under a bucket dedupe key, ``ai.advise.user``
persists the recommendation and its audit row, and production refuses to start without
``ADVISOR_HANDLE_KEY``.

**Validates: Requirements 17.2**
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from edu_it_domain.config import ADVISOR_CYCLE_INTERVAL
from edu_it_domain.learning.models import Mastery, MasteryState, QuestionOption
from edu_it_domain.ports.catalog import ConceptRecord, QuestionRecord, QuizRecord
from edu_it_domain.ports.llm_provider import ProviderUnavailableError
from edu_it_infrastructure.composition import (
    CompositionSettings,
    Environment,
    ProviderSelectionError,
)
from edu_it_infrastructure.fakes import (
    Fake_LLMProvider,
    Fake_SandboxProvider,
    In_Memory_CatalogRepository,
    In_Memory_GenerationLedger,
    In_Memory_ItemFlagRepository,
    In_Memory_MasteryRepository,
    In_Memory_PathRecommendationRepository,
    In_Memory_ReviewRepository,
    In_Memory_ReviewTaskRepository,
    In_Memory_VariantRepository,
    Sequential_IdGenerator,
    Test_Clock,
)
from edu_it_infrastructure.fakes.task_queue import In_Memory_TaskQueue
from edu_it_worker.ai.advisor_jobs import (
    ADVISE_CYCLE_JOB,
    ADVISE_USER_JOB,
    advise_bucket,
    advise_user_dedupe_key,
)
from edu_it_worker.ai.composition import AdvisorPorts, AiWorker, build_ai_worker
from pydantic import SecretStr

T0 = datetime(2025, 1, 1, tzinfo=UTC)
USER = "usr_0123456789abcdef"
OTHER = "usr_fedcba9876543210"


class _Parents:
    def get(self, stable_id: str) -> None:
        return None

    def approved_ids(self) -> list[str]:
        return []


class _Targets:
    def active_learners(self) -> tuple[str, ...]:
        return (USER, OTHER)

    def candidate_concepts(self) -> tuple[str, ...]:
        return ("basics", "perms")


class _Failing:
    def complete(self, *args: object, **kwargs: object) -> None:
        raise ProviderUnavailableError("down")


class _World:
    def __init__(
        self, llm: object | None = None, settings: CompositionSettings | None = None
    ) -> None:
        self.clock = Test_Clock(T0)
        self.queue = In_Memory_TaskQueue()
        self.variants = In_Memory_VariantRepository(self.clock)
        catalog = In_Memory_CatalogRepository()
        catalog.add_concept(ConceptRecord("basics"))
        catalog.add_concept(ConceptRecord("perms", requires=("basics",)))
        options = (QuestionOption(0, True), QuestionOption(1, False))
        catalog.add_quiz(
            QuizRecord(
                "quiz",
                "lesson",
                (
                    QuestionRecord("q1", options, ("basics",)),
                    QuestionRecord("q2", options, ("perms",)),
                ),
            )
        )
        mastery = In_Memory_MasteryRepository()
        mastery.save(Mastery(USER, "basics", 40, MasteryState.PRACTICED))
        self.recommendations = In_Memory_PathRecommendationRepository()
        provider = (
            llm
            if llm is not None
            else Fake_LLMProvider({"advisor": json.dumps({"ordering": ["perms", "basics"]})})
        )
        self.worker: AiWorker = build_ai_worker(
            queue=self.queue,
            clock=self.clock,
            ids=Sequential_IdGenerator("id"),
            ledger=In_Memory_GenerationLedger(self.clock),
            parents=_Parents(),
            variants=self.variants,
            review_tasks=In_Memory_ReviewTaskRepository(self.clock, self.variants),
            flags=In_Memory_ItemFlagRepository(),
            catalog=catalog,
            sandbox=Fake_SandboxProvider(self.clock),
            advisor_ports=AdvisorPorts(
                targets=_Targets(),
                mastery=mastery,
                reviews=In_Memory_ReviewRepository(),
                recommendations=self.recommendations,
            ),
            settings=settings if settings is not None else CompositionSettings(),
            wire=lambda _scope: (provider, provider),  # type: ignore[arg-type,return-value]
        )


def test_worker_registers_the_advisor_jobs_and_schedules_the_cycle() -> None:
    world = _World()
    assert {ADVISE_CYCLE_JOB, ADVISE_USER_JOB} <= set(world.worker.consumers)
    assert ADVISE_CYCLE_JOB in world.worker.scheduler.tick()
    assert world.worker.scheduler.tick() == []  # one fire per interval bucket
    (job,) = world.queue.pending_for(ADVISE_CYCLE_JOB)
    assert job.dedupe_key is not None
    assert job.dedupe_key.startswith("ai.advise.cycle:tick:")


def test_cycle_enqueues_one_deduped_job_per_learner_and_a_new_one_next_bucket() -> None:
    world = _World()
    cycle = world.worker.consumers[ADVISE_CYCLE_JOB]
    cycle({})
    cycle({})  # same bucket: nothing new while pending
    jobs = world.queue.pending_for(ADVISE_USER_JOB)
    bucket = advise_bucket(world.clock)
    assert sorted(j.dedupe_key or "" for j in jobs) == sorted(
        advise_user_dedupe_key(u, bucket) for u in (USER, OTHER)
    )
    assert {j.payload["user_stable_id"] for j in jobs} == {USER, OTHER}

    world.clock.advance(ADVISOR_CYCLE_INTERVAL + timedelta(seconds=1))
    cycle({})
    assert len(world.queue.pending_for(ADVISE_USER_JOB)) == 4


def test_user_job_persists_the_recommendation_and_its_audit_row() -> None:
    world = _World()
    world.worker.consumers[ADVISE_USER_JOB]({"user_stable_id": USER})
    latest = world.recommendations.latest_undismissed(USER)
    assert latest is not None
    assert (latest.source, latest.ordering) == ("llm", ("perms", "basics"))
    (audit,) = world.recommendations.audit_entries(USER)
    assert audit.outcome == "accepted"


def test_a_failing_provider_still_persists_the_rule_based_order() -> None:
    world = _World(llm=_Failing())
    world.worker.consumers[ADVISE_USER_JOB]({"user_stable_id": USER})
    latest = world.recommendations.latest_undismissed(USER)
    assert latest is not None
    assert (latest.source, latest.ordering) == ("rule-based", ("basics", "perms"))
    (audit,) = world.recommendations.audit_entries(USER)
    assert audit.outcome == "failed"


@pytest.mark.parametrize("payload", [{}, {"user_stable_id": ""}, {"user_stable_id": 3}])
def test_a_malformed_user_payload_is_swallowed(payload: dict[str, object]) -> None:
    world = _World()
    world.worker.consumers[ADVISE_USER_JOB](payload)
    assert world.recommendations.audit_entries(USER) == ()


def test_production_refuses_to_start_without_the_handle_key() -> None:
    with pytest.raises(ProviderSelectionError, match="ADVISOR_HANDLE_KEY"):
        _World(settings=CompositionSettings(environment=Environment.PRODUCTION))
    with pytest.raises(ProviderSelectionError, match="ADVISOR_HANDLE_KEY"):
        _World(
            settings=CompositionSettings(
                environment=Environment.PRODUCTION, advisor_handle_key=SecretStr("")
            )
        )


def test_production_starts_with_the_handle_key_and_dev_starts_without() -> None:
    prod = CompositionSettings(
        environment=Environment.PRODUCTION, advisor_handle_key=SecretStr("k" * 32)
    )
    assert ADVISE_USER_JOB in _World(settings=prod).worker.consumers
    assert ADVISE_USER_JOB in _World().worker.consumers
