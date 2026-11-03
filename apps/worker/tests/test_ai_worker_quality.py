"""Tests for the quality jobs of the AI worker (ai-content task 21.2).

Run against the in-memory ports; no model, no network. The quality loop is wired without any
LLM provider.

**Validates: Requirements 18.4, 19.1, 19.2**
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from edu_it_application.ai import (
    QUALITY_EVALUATE_ITEM_JOB,
    SubmitProblemReport,
    SubmitProblemReportRequest,
    enqueue_item_evaluation,
    evaluate_item_dedupe_key,
)
from edu_it_domain.ai.lifecycle import VariantState
from edu_it_domain.config import ITEM_STAT_MIN_SAMPLES, REPORT_THRESHOLD
from edu_it_domain.learning.models import QuestionOption
from edu_it_domain.ports.catalog import ModuleRecord, QuestionRecord, QuizRecord
from edu_it_domain.ports.item_flags import ItemKey, ItemKind
from edu_it_domain.ports.item_quality import NewObservation
from edu_it_domain.ports.lab_catalog import LabDefinition
from edu_it_domain.ports.lessons import LessonRecord
from edu_it_domain.ports.parent_questions import ParentQuestion
from edu_it_domain.ports.problem_reports import ProblemReport
from edu_it_domain.ports.variants import NewRevision, NewVariant
from edu_it_infrastructure.composition import CompositionSettings
from edu_it_infrastructure.fakes import (
    Fake_SandboxProvider,
    In_Memory_GenerationLedger,
    In_Memory_IdempotencyStore,
    In_Memory_ItemFlagRepository,
    In_Memory_ItemQualityRepository,
    In_Memory_ProblemReportRepository,
    In_Memory_ReviewTaskRepository,
    In_Memory_VariantRepository,
    Sequential_IdGenerator,
    Test_Clock,
)
from edu_it_infrastructure.fakes.task_queue import In_Memory_TaskQueue
from edu_it_worker.ai.composition import AiWorker, QualityPorts, build_ai_worker
from edu_it_worker.ai.item_info import Catalog_ItemInfoSource
from edu_it_worker.ai.jobs import REFILL_PARENT_JOB
from edu_it_worker.ai.quality_jobs import QUALITY_CYCLE_JOB

T0 = datetime(2025, 1, 1, tzinfo=UTC)
TRACK = "track-1"
QUESTION = ItemKey(ItemKind.QUESTION, "q1")
LESSON = ItemKey(ItemKind.LESSON, "les1")
LAB = ItemKey(ItemKind.LAB, "lab1")


class _Parents:
    def __init__(self) -> None:
        self.parent = ParentQuestion(
            stable_id="q1",
            track_stable_id=TRACK,
            concept_stable_id="concept.files",
            concept_title="Files",
            prompts={"en": "Q?", "fr": "Q ?"},
            options=("a", "b"),
            approved=True,
        )

    def get(self, stable_id: str) -> ParentQuestion | None:
        return self.parent if stable_id == "q1" else None

    def approved_ids(self) -> list[str]:
        return ["q1"]


class _Catalog:
    def get_module(self, stable_id: str) -> ModuleRecord | None:
        return ModuleRecord("mod1", TRACK, 1) if stable_id == "mod1" else None

    def list_modules_for_track(self, track_id: str) -> tuple[()]:
        return ()

    def get_concept(self, stable_id: str) -> None:
        return None

    def get_quiz(self, stable_id: str) -> QuizRecord | None:
        if stable_id != "quiz.files":
            return None
        question = QuestionRecord("q1", (QuestionOption(1, True),), ("concept.files",))
        return QuizRecord(stable_id, "les1", (question,), content_version="quiz-v1")

    def get_quiz_at_version(self, stable_id: str, content_version: str) -> QuizRecord | None:
        return self.get_quiz(stable_id)

    def list_questions_for_concept(self, concept_id: str) -> tuple[()]:
        return ()

    def get_rubric_for_project(self, project_id: str) -> None:
        return None


class _Lessons:
    def get_lesson(self, stable_id: str) -> LessonRecord | None:
        if stable_id != "les1":
            return None
        return LessonRecord("les1", "mod1", content_version="lesson-v1")


class _Labs:
    def get(self, lab_id: str) -> LabDefinition | None:
        if lab_id != "lab1":
            return None
        return LabDefinition(
            "lab1", "terminal", "img", (), (), (), content_version="lab-v1", lesson_id="les1"
        )

    def list_labs(self) -> tuple[()]:
        return ()

    def labs_covering(self, concept_id: str) -> tuple[()]:
        return ()


class _World:
    def __init__(self) -> None:
        self.clock = Test_Clock(T0)
        self.queue = In_Memory_TaskQueue()
        self.variants = In_Memory_VariantRepository(self.clock)
        self.tasks = In_Memory_ReviewTaskRepository(self.clock, self.variants)
        self.quality = In_Memory_ItemQualityRepository()
        self.reports = In_Memory_ProblemReportRepository()
        self.parents = _Parents()
        self.worker: AiWorker = build_ai_worker(
            queue=self.queue,
            clock=self.clock,
            ids=Sequential_IdGenerator("id"),
            ledger=In_Memory_GenerationLedger(self.clock),
            parents=self.parents,
            variants=self.variants,
            review_tasks=self.tasks,
            flags=In_Memory_ItemFlagRepository(),
            catalog=_Catalog(),
            sandbox=Fake_SandboxProvider(self.clock),
            quality_ports=QualityPorts(self.quality, self.reports, _Lessons(), _Labs()),
            settings=CompositionSettings(),
            wire=lambda _scope: (_Unused(), _Unused()),  # type: ignore[arg-type,return-value]
        )
        self.items = Catalog_ItemInfoSource(
            catalog=_Catalog(),
            lessons=_Lessons(),
            labs=_Labs(),
            parents=self.parents,
            variants=self.variants,
        )

    def open_tasks(self) -> list[object]:
        return list(self.tasks.page([TRACK], None, 100).items)

    def report(self, user: str, key: ItemKey, version: str) -> None:
        self.reports.add(ProblemReport(f"r-{user}", user, key, version, "incorrect", T0))


class _Unused:
    """A provider stand-in: the quality jobs must never call a model."""

    def complete(self, *args: object, **kwargs: object) -> None:
        raise AssertionError("the quality loop must not call a model")


def test_worker_registers_the_quality_jobs_and_schedules_the_cycle() -> None:
    world = _World()
    assert {QUALITY_CYCLE_JOB, QUALITY_EVALUATE_ITEM_JOB} <= set(world.worker.consumers)
    assert QUALITY_CYCLE_JOB in world.worker.scheduler.tick()
    assert world.worker.scheduler.tick() == []  # one fire per interval bucket
    (job,) = world.queue.pending_for(QUALITY_CYCLE_JOB)
    assert job.dedupe_key is not None
    assert job.dedupe_key.startswith("ai.quality.cycle:tick:")


# -- ItemInfoSource ---------------------------------------------------------------------------


def test_item_info_resolves_each_kind_to_its_track_and_current_version() -> None:
    world = _World()
    world.variants.add(NewVariant("var1", "q1", TRACK, VariantState.PENDING_REVIEW))
    info = world.items.describe
    assert info(QUESTION) is not None
    assert (info(QUESTION).track_stable_id, info(QUESTION).content_version) == (  # type: ignore[union-attr]
        TRACK,
        "quiz-v1",
    )
    assert (info(LESSON).track_stable_id, info(LESSON).content_version) == (  # type: ignore[union-attr]
        TRACK,
        "lesson-v1",
    )
    assert (info(LAB).track_stable_id, info(LAB).content_version) == (  # type: ignore[union-attr]
        TRACK,
        "lab-v1",
    )
    variant = info(ItemKey(ItemKind.VARIANT, "var1"))
    assert variant is not None
    assert (variant.track_stable_id, variant.content_version) == (TRACK, None)


@pytest.mark.parametrize(
    "key",
    [
        ItemKey(ItemKind.QUESTION, "nope"),
        ItemKey(ItemKind.LESSON, "nope"),
        ItemKey(ItemKind.LAB, "nope"),
        ItemKey(ItemKind.VARIANT, "nope"),
    ],
)
def test_item_info_is_none_for_unknown_items(key: ItemKey) -> None:
    assert _World().items.describe(key) is None


# -- ai.quality.evaluate_item -----------------------------------------------------------------


def test_report_threshold_flags_the_item_through_the_evaluate_job() -> None:
    world = _World()
    for n in range(REPORT_THRESHOLD):
        world.report(f"u{n}", QUESTION, "quiz-v1")
    assert enqueue_item_evaluation(world.queue, QUESTION)
    assert enqueue_item_evaluation(world.queue, QUESTION)  # deduped while pending
    (job,) = world.queue.pending_for(QUALITY_EVALUATE_ITEM_JOB)
    assert job.dedupe_key == evaluate_item_dedupe_key(QUESTION)
    world.worker.consumers[QUALITY_EVALUATE_ITEM_JOB](job.payload)
    assert world.quality.active_flags([QUESTION]) == {QUESTION}
    assert len(world.open_tasks()) == 1


def test_a_malformed_evaluate_payload_is_swallowed() -> None:
    world = _World()
    world.worker.consumers[QUALITY_EVALUATE_ITEM_JOB]({"item_kind": "question"})
    assert world.open_tasks() == []


# -- ai.quality.cycle -------------------------------------------------------------------------


def test_cycle_folds_observations_and_flags_an_anomalous_item() -> None:
    world = _World()
    for _ in range(ITEM_STAT_MIN_SAMPLES):
        world.quality.record(NewObservation("u", QUESTION, False, T0, 5000))
    world.worker.consumers[QUALITY_CYCLE_JOB]({})
    stat = world.quality.get(QUESTION)
    assert stat is not None
    assert stat.samples == ITEM_STAT_MIN_SAMPLES
    assert world.quality.active_flags([QUESTION]) == {QUESTION}
    assert len(world.open_tasks()) == 1


def test_flagging_the_last_variant_enqueues_one_deduped_refill() -> None:
    world = _World()
    world.variants.add(NewVariant("var1", "q1", TRACK, VariantState.PENDING_REVIEW))
    world.variants.add_revision(NewRevision("var1", "c1", "{}", origin="ai"))
    current = world.variants.get("var1")
    assert current is not None
    world.variants.save(
        replace(current, state=VariantState.APPROVED, approved_content_version="c1")
    )
    key = ItemKey(ItemKind.VARIANT, "var1")
    for _ in range(ITEM_STAT_MIN_SAMPLES):
        world.quality.record(NewObservation("u", key, False, T0, 5000))
    world.worker.consumers[QUALITY_CYCLE_JOB]({})
    (refill,) = world.queue.pending_for(REFILL_PARENT_JOB)
    assert refill.payload == {"parent_stable_id": "q1"}
    assert refill.dedupe_key is not None
    assert refill.dedupe_key.startswith("ai.refill.parent:q1:")


# -- SubmitProblemReport ----------------------------------------------------------------------


def _submit(
    queue: object, *, key: str = "k1"
) -> tuple[SubmitProblemReport, SubmitProblemReportRequest]:
    reports = In_Memory_ProblemReportRepository()
    use_case = SubmitProblemReport(
        reports=reports,
        idempotency=In_Memory_IdempotencyStore(),
        clock=Test_Clock(T0),
        ids=Sequential_IdGenerator("r"),
        on_stored=lambda item: enqueue_item_evaluation(queue, item),  # type: ignore[arg-type]
    )
    request = SubmitProblemReportRequest(
        user_id="u1",
        item=QUESTION,
        content_version="quiz-v1",
        category="incorrect",
        idempotency_key=key,
    )
    return use_case, request


def test_submitting_a_report_enqueues_one_evaluation_and_a_replay_adds_none() -> None:
    queue = In_Memory_TaskQueue()
    use_case, request = _submit(queue)
    use_case.execute(request)
    use_case.execute(request)  # replay
    (job,) = queue.pending_for(QUALITY_EVALUATE_ITEM_JOB)
    assert job.payload == {"item_kind": "question", "item_id": "q1"}
    assert json.dumps(job.payload)  # JSON-serializable for the Redis queue


def test_a_failing_queue_never_fails_the_report() -> None:
    class _Broken:
        def enqueue(self, *args: object, **kwargs: object) -> None:
            raise ConnectionError("redis is down")

    use_case, request = _submit(_Broken())
    result = use_case.execute(request)
    assert result.report.stable_id
    assert not result.replayed


# -- 18.6: the quality code never reaches a model ---------------------------------------------


_LLM_PORT_MODULE = "edu_it_domain.ports.llm_provider"
_LLM_PORT_NAMES = {"LLMProvider", "LLMInput", "PromptRef", "MalformedOutputError"}


def _quality_sources() -> list[object]:
    from pathlib import Path

    import edu_it_application.ai as app_pkg
    import edu_it_worker.ai as worker_pkg

    worker_root = Path(worker_pkg.__file__).parent
    app_root = Path(app_pkg.__file__).parent
    return [
        worker_root / "quality_jobs.py",
        worker_root / "item_info.py",
        app_root / "quality_loop.py",
        app_root / "quality_jobs.py",
    ]


def test_quality_modules_do_not_import_the_llm_port() -> None:
    """The quality code (worker and application) never imports the LLM port.

    **Validates: Requirements 18.6, 18.8**
    """
    import ast
    from pathlib import Path

    for path in _quality_sources():
        assert isinstance(path, Path)
        assert path.is_file(), path
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                assert module != _LLM_PORT_MODULE, f"{path.name}: {ast.unparse(node)}"
                assert not module.endswith("llm_provider"), f"{path.name}: {ast.unparse(node)}"
                imported = {alias.name for alias in node.names}
                assert not imported & _LLM_PORT_NAMES, f"{path.name}: {ast.unparse(node)}"
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.endswith("llm_provider"), f"{path.name}: {alias.name}"


def test_flagging_opens_a_review_task_and_no_other_notification() -> None:
    """Flagging an item produces one open Review_Task and no mail or other job.

    **Validates: Requirements 18.6, 18.8**
    """
    world = _World()
    for _ in range(ITEM_STAT_MIN_SAMPLES):
        world.quality.record(NewObservation("u", QUESTION, False, T0, 5000))
    world.worker.consumers[QUALITY_CYCLE_JOB]({})
    assert world.quality.active_flags([QUESTION]) == {QUESTION}
    assert len(world.open_tasks()) == 1
    # Nothing was enqueued: no mail, no push, no refill for a question flag.
    assert world.queue.pending == ()

    # A second cycle is idempotent: still exactly one task, still no other side effect.
    world.worker.consumers[QUALITY_CYCLE_JOB]({})
    assert len(world.open_tasks()) == 1
    assert world.queue.pending == ()


def test_a_report_driven_flag_enqueues_nothing_but_the_review_task() -> None:
    """The report path also notifies only through the Review_Task.

    **Validates: Requirements 18.6, 18.8**
    """
    world = _World()
    for n in range(REPORT_THRESHOLD):
        world.report(f"u{n}", QUESTION, "quiz-v1")
    enqueue_item_evaluation(world.queue, QUESTION)
    (job,) = world.queue.pending_for(QUALITY_EVALUATE_ITEM_JOB)
    world.worker.consumers[QUALITY_EVALUATE_ITEM_JOB](job.payload)
    assert len(world.open_tasks()) == 1
    # Only the evaluate job we enqueued ourselves remains pending.
    assert {j.job_name for j in world.queue.pending} == {QUALITY_EVALUATE_ITEM_JOB}
