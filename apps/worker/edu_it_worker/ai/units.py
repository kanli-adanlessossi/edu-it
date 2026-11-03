"""Per-Stage Generation_Unit builders (ai-content Req 5.1, 5.8, 5.10, 7.5, 8.1).

A builder knows, for one Stage, which Generation_Units to produce, what to send to the model,
which items the Verifier must look at, and how to turn the validated draft (plus the real
command outputs) into repository files. Builders are pure: they read through a
:class:`SourceView` and return ``{path: bytes}``. They touch no ref, no clock and no model, so
the Generator stays the only place that orders the run.

A unit is complete only when every file of it exists in both locales; the content writer
raises ``IncompleteUnitError`` for a body that lacks one.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final, Protocol

from edu_it_application.ai import (
    ItemKind,
    ItemUnderVerification,
    QuestionUnderVerification,
    VerificationContext,
)
from edu_it_content.schemas import (
    BriefDraft,
    LabDraft,
    LessonDraft,
    OutlineDraft,
    ProjectDraft,
    QuizDraft,
)
from edu_it_domain.ai.generation import UNIT_LOCALES
from edu_it_domain.ai.inputs import InputKind
from edu_it_domain.ai.verification import (
    CommandOutput,
    LabCheckUnderVerification,
    LabUnderVerification,
)
from edu_it_domain.ports.llm_provider import LLMInput
from pydantic import BaseModel

from edu_it_worker.ai.content_writer import ContentWriter, GenerationInfo, content_version, slugify
from edu_it_worker.ai.stages import content_dir

__all__ = [
    "BUILDERS",
    "BuildEnv",
    "BuiltUnit",
    "SourceView",
    "StageBuilder",
    "UnitPlan",
    "stage_builder",
]

#: Sandbox image and lab kind sent to the labs prompt.
LAB_IMAGE: Final = "linux-base"
LAB_KIND: Final = "terminal"
_QUIZ_DIFFICULTY: Final = "beginner"
_BUNDLE_DEST: Final = "/grader"
_NO_OUTPUT_NOTE: Final = "(this command could not be run, so there is no output to show)"
_BACKTICKS: Final = re.compile(r"`+")


class SourceView(Protocol):
    """Read-only view of the content as the run sees it (base, Base_Ref, files written so far)."""

    def read(self, path: str) -> str | None:
        """Return the UTF-8 text of ``path``, or ``None`` when absent."""
        ...

    def files(self, prefix: str) -> frozenset[str]:
        """Return the repository paths under ``prefix``."""
        ...


@dataclass(frozen=True, slots=True)
class BuildEnv:
    """What a builder may read."""

    track: str
    src: SourceView
    writer: ContentWriter
    glossary: str
    """JSON text of the ``[term_en, term_fr]`` pairs sent to the model."""


@dataclass(frozen=True, slots=True)
class UnitPlan:
    """One Generation_Unit to produce."""

    unit_id: str
    kind: str
    prompt_id: str
    input: LLMInput
    data: Mapping[str, Any] = field(default_factory=dict)
    """Builder-specific facts (ids and titles) kept out of the model input."""


@dataclass(frozen=True, slots=True)
class BuiltUnit:
    """The files of one complete unit and the version of its content."""

    files: Mapping[str, bytes]
    content_version: str


class StageBuilder(Protocol):
    """The contract the Generator drives for every Stage."""

    stage: str
    prompt_id: str
    model: type[BaseModel]

    def plan(self, env: BuildEnv) -> tuple[UnitPlan, ...]:
        """Return every unit of the Stage that the inputs allow, in a stable order."""
        ...

    def subjects(
        self, plan: UnitPlan, draft: BaseModel, env: BuildEnv
    ) -> tuple[ItemUnderVerification, ...]:
        """Return the items the Verifier must check for this unit."""
        ...

    def verification_context(
        self,
        plan: UnitPlan,
        env: BuildEnv,
        preceding: Sequence[ItemUnderVerification],
    ) -> VerificationContext:
        """Return the corpora a subject is compared with."""
        ...

    def build(
        self,
        plan: UnitPlan,
        draft: BaseModel,
        info: GenerationInfo,
        outputs: Mapping[str, tuple[CommandOutput, ...]],
        env: BuildEnv,
    ) -> BuiltUnit:
        """Return the files of the complete unit."""
        ...


# -- shared helpers ---------------------------------------------------------------------------


def _base(env: BuildEnv) -> str:
    return content_dir(env.track)


def _read_list(env: BuildEnv, name: str) -> list[dict[str, Any]]:
    text = env.src.read(f"{_base(env)}/{name}")
    if not text:
        return []
    try:
        loaded = json.loads(text)
    except ValueError:
        return []
    return [item for item in loaded if isinstance(item, dict)] if isinstance(loaded, list) else []


def _sid(item: Mapping[str, Any]) -> str:
    prov = item.get("provenance")
    raw = prov.get("stable_id") if isinstance(prov, Mapping) else item.get("stable_id")
    return raw if isinstance(raw, str) else ""


def _slug_of(stable_id: str) -> str:
    return stable_id.split(".", 1)[1] if "." in stable_id else stable_id


def _title(item: Mapping[str, Any], locale: str = "en") -> str:
    title = item.get("title")
    value = title.get(locale) if isinstance(title, Mapping) else None
    return value if isinstance(value, str) else ""


def _existing(env: BuildEnv, *names: str) -> dict[str, str]:
    present: dict[str, str] = {}
    for name in names:
        path = f"{_base(env)}/{name}"
        text = env.src.read(path)
        if text is not None:
            present[path] = text
    return present


def _taken(env: BuildEnv, *names: str) -> set[str]:
    return {_sid(item) for name in names for item in _read_list(env, name)}


def _dump(draft: BaseModel) -> dict[str, Any]:
    return draft.model_dump(mode="json")


def _json_field(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _texts(*parts: Mapping[str, str]) -> dict[str, str]:
    """Join localized parts into one text per locale."""
    return {loc: "\n".join(p[loc] for p in parts if p.get(loc)) for loc in UNIT_LOCALES}


# -- brief ------------------------------------------------------------------------------------


class BriefBuilder:
    """One unit: ``brief.json``. It is not a catalog list, so it carries no item Provenance."""

    stage: str = "brief"
    prompt_id: str = "brief"
    model: type[BaseModel] = BriefDraft

    def plan(self, env: BuildEnv) -> tuple[UnitPlan, ...]:
        fields = {"track": env.track, "brief": "", "locale": "en", "glossary": env.glossary}
        return (
            UnitPlan(
                f"brief.{env.track}",
                "brief",
                self.prompt_id,
                LLMInput(InputKind.BRIEF.value, fields),
            ),
        )

    def subjects(
        self, plan: UnitPlan, draft: BaseModel, env: BuildEnv
    ) -> tuple[ItemUnderVerification, ...]:
        assert isinstance(draft, BriefDraft)
        parts = [draft.title.model_dump(), draft.description.model_dump()]
        parts += [o.model_dump() for o in draft.learning_outcomes]
        return (ItemUnderVerification(plan.unit_id, ItemKind.OTHER, texts=_texts(*parts)),)

    def verification_context(
        self, plan: UnitPlan, env: BuildEnv, preceding: Sequence[ItemUnderVerification]
    ) -> VerificationContext:
        return VerificationContext()

    def build(
        self,
        plan: UnitPlan,
        draft: BaseModel,
        info: GenerationInfo,
        outputs: Mapping[str, tuple[CommandOutput, ...]],
        env: BuildEnv,
    ) -> BuiltUnit:
        document = _dump(draft)
        files = env.writer.render(env.track, documents={"brief.json": document})
        return BuiltUnit(files, content_version(document))


# -- outline ----------------------------------------------------------------------------------


class OutlineBuilder:
    """One unit: tracks, modules, lessons, concepts and prerequisites."""

    stage: str = "outline"
    prompt_id: str = "outline"
    model: type[BaseModel] = OutlineDraft

    def plan(self, env: BuildEnv) -> tuple[UnitPlan, ...]:
        brief = env.src.read(f"{_base(env)}/brief.json")
        if brief is None:
            return ()
        concepts = [_title(c) for c in _read_list(env, "concepts.json")]
        fields = {
            "track": env.track,
            "brief": brief,
            "locale": "en",
            "glossary": env.glossary,
            "concepts": _json_field([c for c in concepts if c]),
        }
        data = {"brief": brief}
        return (
            UnitPlan(
                f"outline.{env.track}",
                "outline",
                self.prompt_id,
                LLMInput(InputKind.OUTLINE.value, fields),
                data,
            ),
        )

    def subjects(
        self, plan: UnitPlan, draft: BaseModel, env: BuildEnv
    ) -> tuple[ItemUnderVerification, ...]:
        assert isinstance(draft, OutlineDraft)
        parts: list[Mapping[str, str]] = []
        for module in draft.modules:
            parts.append(module.title.model_dump())
            for lesson in module.lessons:
                parts += [lesson.title.model_dump(), lesson.summary.model_dump()]
        return (ItemUnderVerification(plan.unit_id, ItemKind.OTHER, texts=_texts(*parts)),)

    def verification_context(
        self, plan: UnitPlan, env: BuildEnv, preceding: Sequence[ItemUnderVerification]
    ) -> VerificationContext:
        return VerificationContext()

    def build(
        self,
        plan: UnitPlan,
        draft: BaseModel,
        info: GenerationInfo,
        outputs: Mapping[str, tuple[CommandOutput, ...]],
        env: BuildEnv,
    ) -> BuiltUnit:
        assert isinstance(draft, OutlineDraft)
        writer = env.writer
        track_id = f"track.{env.track}"
        taken = _taken(env, "tracks.json", "modules.json", "lessons.json", "concepts.json")
        concept_ids = {
            _title(c).casefold(): _sid(c) for c in _read_list(env, "concepts.json") if _title(c)
        }

        tracks: list[dict[str, Any]] = []
        if track_id not in taken:
            brief = json.loads(str(plan.data["brief"]))
            tracks.append(
                writer.stamp(
                    {"title": brief["title"], "status": "draft"}, stable_id=track_id, info=info
                )
            )
            taken.add(track_id)

        modules: list[dict[str, Any]] = []
        lessons: list[dict[str, Any]] = []
        concepts: list[dict[str, Any]] = []
        for m_index, module in enumerate(draft.modules):
            module_id = writer.stable_id("module", module.title.en, taken)
            taken.add(module_id)
            modules.append(
                writer.stamp(
                    {
                        "track_stable_id": track_id,
                        "ordinal": m_index,
                        "title": module.title.model_dump(),
                    },
                    stable_id=module_id,
                    info=info,
                )
            )
            for l_index, lesson in enumerate(module.lessons):
                lesson_id = writer.stable_id("lesson", lesson.title.en, taken)
                taken.add(lesson_id)
                lessons.append(
                    writer.stamp(
                        {
                            "module_stable_id": module_id,
                            "ordinal": l_index,
                            "title": lesson.title.model_dump(),
                        },
                        stable_id=lesson_id,
                        info=info,
                    )
                )
                for concept in lesson.concept_titles:
                    key = concept.en.casefold()
                    if key in concept_ids:
                        continue
                    concept_id = writer.stable_id("concept", concept.en, taken)
                    taken.add(concept_id)
                    concept_ids[key] = concept_id
                    concepts.append(
                        writer.stamp(
                            {"title": concept.model_dump()}, stable_id=concept_id, info=info
                        )
                    )

        files = writer.render(
            env.track,
            lists={
                "tracks.json": tracks,
                "modules.json": modules,
                "lessons.json": lessons,
                "concepts.json": concepts,
                # The model proposes no dependency edges; authors add them during review.
                "prerequisites.json": [],
            },
            existing=_existing(
                env,
                "tracks.json",
                "modules.json",
                "lessons.json",
                "concepts.json",
                "prerequisites.json",
            ),
        )
        return BuiltUnit(files, content_version(_dump(draft)))


# -- lessons ----------------------------------------------------------------------------------


def _fence(text: str, language: str) -> str:
    longest = max((len(run) for run in _BACKTICKS.findall(text)), default=0)
    ticks = "`" * max(3, longest + 1)
    return f"{ticks}{language}\n{text.rstrip()}\n{ticks}"


def _lesson_mdx(draft: LessonDraft, locale: str, outputs: Sequence[CommandOutput]) -> str:
    out: list[str] = [f"# {getattr(draft.title, locale)}", ""]
    produced = list(outputs)
    for section in draft.sections:
        out += [f"## {getattr(section.heading, locale)}", "", getattr(section.body, locale), ""]
        for command in section.commands:
            out += [_fence(f"$ {command}", "bash"), ""]
            result = produced.pop(0) if produced and produced[0].command == command else None
            if result is None:
                out += [_NO_OUTPUT_NOTE, ""]
                continue
            shown = (result.stdout + result.stderr).rstrip("\n")
            if result.timed_out:
                shown = (shown + "\n" if shown else "") + "(the command timed out)"
            out += [_fence(shown or "(no output)", "text"), ""]
    return "\n".join(out).rstrip("\n") + "\n"


class LessonBuilder:
    """One unit per outline lesson: the FR and EN bodies with sandbox-produced outputs."""

    stage: str = "lessons"
    prompt_id: str = "lesson"
    model: type[BaseModel] = LessonDraft

    def plan(self, env: BuildEnv) -> tuple[UnitPlan, ...]:
        modules = {_sid(m): m for m in _read_list(env, "modules.json")}
        plans: list[UnitPlan] = []
        for lesson in _read_list(env, "lessons.json"):
            lesson_id = _sid(lesson)
            module = modules.get(str(lesson.get("module_stable_id")), {})
            outline = {"lesson": lesson.get("title"), "module": module.get("title")}
            fields = {
                "track": env.track,
                "module": _title(module),
                "concept": _title(lesson),
                "outline": _json_field(outline),
                "locale": "en",
                "glossary": env.glossary,
                "sources": "",
            }
            plans.append(
                UnitPlan(
                    lesson_id,
                    "lesson",
                    self.prompt_id,
                    LLMInput(InputKind.LESSON.value, fields),
                    {"lesson": lesson},
                )
            )
        return tuple(plans)

    def subjects(
        self, plan: UnitPlan, draft: BaseModel, env: BuildEnv
    ) -> tuple[ItemUnderVerification, ...]:
        assert isinstance(draft, LessonDraft)
        prose = _texts(
            *(
                part
                for s in draft.sections
                for part in (s.heading.model_dump(), s.body.model_dump())
            )
        )
        commands = tuple(c for s in draft.sections for c in s.commands)
        return (
            ItemUnderVerification(
                plan.unit_id, ItemKind.LESSON, texts=prose, commands=commands, image=LAB_IMAGE
            ),
        )

    def verification_context(
        self, plan: UnitPlan, env: BuildEnv, preceding: Sequence[ItemUnderVerification]
    ) -> VerificationContext:
        return VerificationContext()

    def build(
        self,
        plan: UnitPlan,
        draft: BaseModel,
        info: GenerationInfo,
        outputs: Mapping[str, tuple[CommandOutput, ...]],
        env: BuildEnv,
    ) -> BuiltUnit:
        assert isinstance(draft, LessonDraft)
        produced = outputs.get(plan.unit_id, ())
        bodies = {loc: _lesson_mdx(draft, loc, produced) for loc in UNIT_LOCALES}
        raw = {k: v for k, v in dict(plan.data["lesson"]).items() if k != "provenance"}
        lesson = env.writer.stamp(raw, stable_id=plan.unit_id, info=info, bodies=bodies)
        files = env.writer.render(
            env.track,
            lists={"lessons.json": [lesson]},
            bodies={plan.unit_id: bodies},
            existing=_existing(env, "lessons.json"),
        )
        return BuiltUnit(files, lesson["provenance"]["content_version"])


# -- quizzes ----------------------------------------------------------------------------------


class QuizBuilder:
    """One unit per lesson: a quiz with its questions and options."""

    stage: str = "quizzes"
    prompt_id: str = "quiz"
    model: type[BaseModel] = QuizDraft

    def plan(self, env: BuildEnv) -> tuple[UnitPlan, ...]:
        modules = {_sid(m): m for m in _read_list(env, "modules.json")}
        plans: list[UnitPlan] = []
        for lesson in _read_list(env, "lessons.json"):
            lesson_id = _sid(lesson)
            module = modules.get(str(lesson.get("module_stable_id")), {})
            body = env.src.read(f"{_base(env)}/mdx/{lesson_id}.en.mdx") or _title(lesson)
            fields = {
                "track": env.track,
                "module": _title(module),
                "concept": _title(lesson),
                "lesson": body,
                "locale": "en",
                "glossary": env.glossary,
                "difficulty": _QUIZ_DIFFICULTY,
            }
            plans.append(
                UnitPlan(
                    f"quiz.{_slug_of(lesson_id)}",
                    "quiz",
                    self.prompt_id,
                    LLMInput(InputKind.QUIZ.value, fields),
                    {"lesson_id": lesson_id},
                )
            )
        return tuple(plans)

    def subjects(
        self, plan: UnitPlan, draft: BaseModel, env: BuildEnv
    ) -> tuple[ItemUnderVerification, ...]:
        assert isinstance(draft, QuizDraft)
        subjects: list[ItemUnderVerification] = []
        for index, question in enumerate(draft.questions, start=1):
            subjects.append(
                ItemUnderVerification(
                    f"question.{_slug_of(plan.unit_id)}-{index}",
                    ItemKind.QUESTION,
                    texts=_texts(question.prompt_text.model_dump()),
                    question=QuestionUnderVerification(
                        prompt=question.prompt_text.en,
                        options=tuple(o.text.en for o in question.options),
                        key=tuple(i for i, o in enumerate(question.options) if o.is_correct),
                    ),
                )
            )
        return tuple(subjects)

    def verification_context(
        self, plan: UnitPlan, env: BuildEnv, preceding: Sequence[ItemUnderVerification]
    ) -> VerificationContext:
        known = [
            text
            for question in _read_list(env, "questions.json")
            for text in (
                _title_of(question, "prompt_text", "en"),
                _title_of(question, "prompt_text", "fr"),
            )
            if text
        ]
        for subject in preceding:
            known.extend(subject.texts.values())
        return VerificationContext(existing_texts=tuple(known))

    def build(
        self,
        plan: UnitPlan,
        draft: BaseModel,
        info: GenerationInfo,
        outputs: Mapping[str, tuple[CommandOutput, ...]],
        env: BuildEnv,
    ) -> BuiltUnit:
        assert isinstance(draft, QuizDraft)
        writer = env.writer
        slug = _slug_of(plan.unit_id)
        quiz = writer.stamp(
            {"lesson_stable_id": plan.data["lesson_id"], "title": draft.title.model_dump()},
            stable_id=plan.unit_id,
            info=info,
        )
        questions: list[dict[str, Any]] = []
        options: list[dict[str, Any]] = []
        for q_index, question in enumerate(draft.questions, start=1):
            question_id = f"question.{slug}-{q_index}"
            questions.append(
                writer.stamp(
                    {
                        "quiz_stable_id": plan.unit_id,
                        "prompt_text": question.prompt_text.model_dump(),
                    },
                    stable_id=question_id,
                    info=info,
                )
            )
            for o_index, option in enumerate(question.options, start=1):
                options.append(
                    {
                        "stable_id": f"question_option.{slug}-{q_index}-{o_index}",
                        "question_stable_id": question_id,
                        "is_correct": option.is_correct,
                        "text": option.text.model_dump(),
                    }
                )
        files = writer.render(
            env.track,
            lists={
                "quizzes.json": [quiz],
                "questions.json": questions,
                "question_options.json": options,
            },
            existing=_existing(env, "quizzes.json", "questions.json", "question_options.json"),
        )
        return BuiltUnit(files, content_version(_dump(draft)))


def _title_of(item: Mapping[str, Any], field_name: str, locale: str) -> str:
    value = item.get(field_name)
    text = value.get(locale) if isinstance(value, Mapping) else None
    return text if isinstance(text, str) else ""


# -- labs -------------------------------------------------------------------------------------


class LabBuilder:
    """One unit per lesson: a lab, its tasks and checks, and its Check_Bundle."""

    stage: str = "labs"
    prompt_id: str = "lab"
    model: type[BaseModel] = LabDraft

    def plan(self, env: BuildEnv) -> tuple[UnitPlan, ...]:
        modules = {_sid(m): m for m in _read_list(env, "modules.json")}
        plans: list[UnitPlan] = []
        for lesson in _read_list(env, "lessons.json"):
            lesson_id = _sid(lesson)
            module = modules.get(str(lesson.get("module_stable_id")), {})
            fields = {
                "track": env.track,
                "module": _title(module),
                "concept": _title(lesson),
                "lesson": _title(lesson),
                "locale": "en",
                "glossary": env.glossary,
                "image": LAB_IMAGE,
                "kind": LAB_KIND,
            }
            plans.append(
                UnitPlan(
                    f"lab.{_slug_of(lesson_id)}",
                    "lab",
                    self.prompt_id,
                    LLMInput(InputKind.LAB.value, fields),
                    {"lesson_id": lesson_id},
                )
            )
        return tuple(plans)

    @staticmethod
    def _check_ids(plan: UnitPlan, draft: LabDraft) -> list[list[str]]:
        slug = _slug_of(plan.unit_id)
        return [
            [f"lab-check.{slug}.{t}-{c}" for c in range(1, len(task.checks) + 1)]
            for t, task in enumerate(draft.tasks, start=1)
        ]

    def subjects(
        self, plan: UnitPlan, draft: BaseModel, env: BuildEnv
    ) -> tuple[ItemUnderVerification, ...]:
        assert isinstance(draft, LabDraft)
        ids = self._check_ids(plan, draft)
        checks = tuple(
            LabCheckUnderVerification(ids[t][c], check.script, float(check.timeout_seconds))
            for t, task in enumerate(draft.tasks)
            for c, check in enumerate(task.checks)
        )
        lab = LabUnderVerification(
            lab_id=plan.unit_id,
            image=LAB_IMAGE,
            checks=checks,
            reference_solution=draft.reference_solution,
            setup_script=draft.setup_script,
        )
        return (
            ItemUnderVerification(
                plan.unit_id,
                ItemKind.LAB,
                texts=_texts(draft.title.model_dump(), draft.instructions.model_dump()),
                lab=lab,
            ),
        )

    def verification_context(
        self, plan: UnitPlan, env: BuildEnv, preceding: Sequence[ItemUnderVerification]
    ) -> VerificationContext:
        return VerificationContext()

    def build(
        self,
        plan: UnitPlan,
        draft: BaseModel,
        info: GenerationInfo,
        outputs: Mapping[str, tuple[CommandOutput, ...]],
        env: BuildEnv,
    ) -> BuiltUnit:
        assert isinstance(draft, LabDraft)
        writer = env.writer
        slug = _slug_of(plan.unit_id)
        bundle = f"{_base(env)}/lab-bundles/{slug}"
        ids = self._check_ids(plan, draft)

        lab = writer.stamp(
            {
                "lesson_stable_id": plan.data["lesson_id"],
                "title": draft.title.model_dump(),
                "kind": draft.kind.value,
                "image": LAB_IMAGE,
                "concept_stable_ids": [],
                "instructions": draft.instructions.model_dump(),
                "setup_script": f"{bundle}/setup/setup.sh" if draft.setup_script else None,
                "check_bundle": bundle,
                "check_bundle_dest": _BUNDLE_DEST,
            },
            stable_id=plan.unit_id,
            info=info,
        )
        tasks: list[dict[str, Any]] = []
        checks: list[dict[str, Any]] = []
        extra: dict[str, bytes] = {
            f"{bundle}/expected/.gitkeep": b"",
            f"{bundle}/reference/solution.sh": _script(draft.reference_solution),
        }
        if draft.setup_script:
            extra[f"{bundle}/setup/setup.sh"] = _script(draft.setup_script)
        for t_index, task in enumerate(draft.tasks):
            task_id = f"lab-task.{slug}.{t_index + 1}"
            tasks.append(
                {
                    "stable_id": task_id,
                    "lab_stable_id": plan.unit_id,
                    "ordinal": t_index,
                    "title": task.title.model_dump(),
                    "description": task.description.model_dump(),
                    "required": True,
                    "hints": [h.model_dump() for h in task.hints],
                }
            )
            for c_index, check in enumerate(task.checks):
                check_id = ids[t_index][c_index]
                key = f"lab.{slug}.{t_index + 1}-{c_index + 1}"
                checks.append(
                    {
                        "stable_id": check_id,
                        "lab_task_stable_id": task_id,
                        "timeout_seconds": check.timeout_seconds,
                        "script": f"checks/{check_id}.sh",
                        "outcomes": [
                            {
                                "code": "pass",
                                "exit_code": 0,
                                "feedback_key": f"{key}.pass",
                                "message": check.pass_message.model_dump(),
                            },
                            {
                                "code": "not-met",
                                "exit_code": 1,
                                "feedback_key": f"{key}.not-met",
                                "message": check.fail_message.model_dump(),
                            },
                        ],
                    }
                )
                extra[f"{bundle}/sandbox/checks/{check_id}.sh"] = _script(check.script)
        files = dict(
            writer.render(
                env.track,
                lists={"labs.json": [lab], "lab_tasks.json": tasks, "lab_checks.json": checks},
                existing=_existing(env, "labs.json", "lab_tasks.json", "lab_checks.json"),
            )
        )
        files.update(extra)
        return BuiltUnit(files, content_version(_dump(draft)))


def criterion_slug(title: str) -> str:
    return slugify(title) or "criterion"


def _script(text: str) -> bytes:
    return (text.replace("\r\n", "\n").rstrip("\n") + "\n").encode("utf-8")


# -- project ----------------------------------------------------------------------------------


class ProjectBuilder:
    """One unit per module: a project with its rubric and criteria."""

    stage: str = "project"
    prompt_id: str = "project"
    model: type[BaseModel] = ProjectDraft

    def plan(self, env: BuildEnv) -> tuple[UnitPlan, ...]:
        lessons = _read_list(env, "lessons.json")
        concepts = [t for t in (_title(c) for c in _read_list(env, "concepts.json")) if t]
        plans: list[UnitPlan] = []
        for module in _read_list(env, "modules.json"):
            module_id = _sid(module)
            titles = [
                _title(lesson) for lesson in lessons if lesson.get("module_stable_id") == module_id
            ]
            fields = {
                "track": env.track,
                "module": _title(module),
                "concepts": _json_field(concepts),
                "outline": _json_field(titles),
                "locale": "en",
                "glossary": env.glossary,
                "rubric": "",
            }
            plans.append(
                UnitPlan(
                    f"project.{_slug_of(module_id)}",
                    "project",
                    self.prompt_id,
                    LLMInput(InputKind.PROJECT.value, fields),
                )
            )
        return tuple(plans)

    def subjects(
        self, plan: UnitPlan, draft: BaseModel, env: BuildEnv
    ) -> tuple[ItemUnderVerification, ...]:
        assert isinstance(draft, ProjectDraft)
        parts = [draft.title.model_dump(), draft.description.model_dump()]
        parts += [c.title.model_dump() for c in draft.criteria]
        return (ItemUnderVerification(plan.unit_id, ItemKind.OTHER, texts=_texts(*parts)),)

    def verification_context(
        self, plan: UnitPlan, env: BuildEnv, preceding: Sequence[ItemUnderVerification]
    ) -> VerificationContext:
        return VerificationContext()

    def build(
        self,
        plan: UnitPlan,
        draft: BaseModel,
        info: GenerationInfo,
        outputs: Mapping[str, tuple[CommandOutput, ...]],
        env: BuildEnv,
    ) -> BuiltUnit:
        assert isinstance(draft, ProjectDraft)
        writer = env.writer
        slug = _slug_of(plan.unit_id)
        project = writer.stamp(
            {"title": draft.title.model_dump(), "description": draft.description.model_dump()},
            stable_id=plan.unit_id,
            info=info,
        )
        rubric_id = f"rubric.{slug}"
        rubrics = [{"stable_id": rubric_id, "project_stable_id": plan.unit_id}]
        criteria = [
            {
                "stable_id": (
                    f"rubric_criterion.{slug}.{index}-{criterion_slug(criterion.title.en)}"
                ),
                "rubric_stable_id": rubric_id,
                "max_points": criterion.max_points,
                "title": criterion.title.model_dump(),
            }
            for index, criterion in enumerate(draft.criteria, start=1)
        ]
        files = writer.render(
            env.track,
            lists={
                "projects.json": [project],
                "rubrics.json": rubrics,
                "rubric_criteria.json": criteria,
            },
            existing=_existing(env, "projects.json", "rubrics.json", "rubric_criteria.json"),
        )
        return BuiltUnit(files, content_version(_dump(draft)))


#: The builder of each Stage.
BUILDERS: Final[Mapping[str, StageBuilder]] = {
    "brief": BriefBuilder(),
    "outline": OutlineBuilder(),
    "lessons": LessonBuilder(),
    "quizzes": QuizBuilder(),
    "labs": LabBuilder(),
    "project": ProjectBuilder(),
}


def stage_builder(stage: str) -> StageBuilder:
    """Return the builder of ``stage``; raise ``ValueError`` for an unknown Stage."""
    try:
        return BUILDERS[stage]
    except KeyError:
        msg = f"unknown stage: {stage!r}"
        raise ValueError(msg) from None
