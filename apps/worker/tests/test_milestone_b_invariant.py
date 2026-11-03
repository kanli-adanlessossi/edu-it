"""Milestone B invariant proof, worker and static side (ai-content task 17.1).

The invariant: learners only ever see content an expert approved.

- The review and practice sets the worker builds contain approved items only, for variants
  in every lifecycle state, revision state and flag combination.
- A Rejected, retired (Withdrawn) or Flagged variant (flag set directly through the
  ``item_flag`` repository) is never returned after the change.
- Static: the serving and review modules import no ``LLMProvider`` port, so no learner action
  can trigger a model call.

**Validates: Requirements 14.3, 14.4, 14.5, 22.2, 22.5, 22.6, 22.7**
"""

from __future__ import annotations

import ast
import importlib.util
import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

import pytest
from edu_it_domain.ai.lifecycle import SERVABLE_STATES, VariantState
from edu_it_domain.ports.item_flags import FlagReason, FlagReasonKind, ItemKey, ItemKind
from edu_it_domain.ports.variants import NewRevision, NewVariant, RevisionState

T0 = datetime(2025, 1, 1, tzinfo=UTC)
_ROOT = Path(__file__).resolve().parents[3]
_HELPERS_PATH = Path(__file__).with_name("test_ai_worker_refill.py")
_QUESTIONS = ("q1", "q2")
_BODY = '{"options": [{"is_correct": true}, {"is_correct": false}]}'


def _load_helpers() -> ModuleType:
    """Reuse the worker wiring of the refill tests (tests are imported by path)."""
    name = "_milestone_b_worker_helpers"
    cached = sys.modules.get(name)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(name, _HELPERS_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _world() -> object:
    helpers = _load_helpers()
    return helpers._World(helpers._Parents(), questions=_QUESTIONS)


def _add(
    world: object,
    name: str,
    state: VariantState,
    revision_state: RevisionState = RevisionState.APPROVED,
    *,
    flagged: bool = False,
) -> None:
    """Insert straight into the repository, whatever the state."""
    variants = world.variants  # type: ignore[attr-defined]
    variants.add(NewVariant(name, "q1", "track-1", state))
    variants.add_revision(NewRevision(name, "c1", _BODY, state=revision_state))
    stored = variants.get(name)
    assert stored is not None
    variants.save(replace(stored, approved_content_version="c1"))
    if flagged:
        _flag(world, name)


def _flag(world: object, name: str) -> None:
    world.flags.flag(  # type: ignore[attr-defined]
        ItemKey(ItemKind.VARIANT, name), FlagReason(FlagReasonKind.REPORTS)
    )


def _review_refs(world: object) -> set[str]:
    """The variant references the worker's review sets contain for several learners."""
    servable = world.worker.servable  # type: ignore[attr-defined]
    refs: set[str] = set()
    for user in ("u1", "u2", "u3"):
        resolved = servable.resolve_review_question(
            concept_id="con-1", parent_question_id="q1", user_ref=user, due_at=T0
        )
        if resolved.variant_ref is not None:
            refs.add(resolved.variant_ref)
    return refs


def _practice_ids(world: object) -> list[str]:
    servable = world.worker.servable  # type: ignore[attr-defined]
    return [q.stable_id for q in servable.list_questions_for_concept("con-1")]


_ALL = [
    (state, revision_state, flagged)
    for state in VariantState
    for revision_state in RevisionState
    for flagged in (False, True)
]


def _is_approved(case: tuple[VariantState, RevisionState, bool]) -> bool:
    state, revision_state, flagged = case
    return state in SERVABLE_STATES and revision_state is RevisionState.APPROVED and not flagged


def test_review_and_practice_sets_contain_approved_items_only() -> None:
    """With a variant in every state, review sets hold only approved variants; practice sets
    hold only the authored, unflagged questions.

    **Validates: Requirements 14.3, 22.2, 22.5**
    """
    world = _world()
    allowed: set[str] = set()
    for index, case in enumerate(_ALL):
        name = f"v-{index:02d}"
        _add(world, name, case[0], case[1], flagged=case[2])
        if _is_approved(case):
            allowed.add(f"{name}@c1")

    refs = _review_refs(world)

    assert allowed
    assert refs
    assert refs <= allowed
    assert _practice_ids(world) == list(_QUESTIONS)  # variants never join a practice set


@pytest.mark.parametrize("case", [c for c in _ALL if not _is_approved(c)])
def test_a_non_approved_variant_never_enters_a_review_set(
    case: tuple[VariantState, RevisionState, bool],
) -> None:
    """Each non-approved combination, alone, leaves the review on the authored parent.

    **Validates: Requirements 14.3, 22.2**
    """
    world = _world()
    _add(world, "v-bad", case[0], case[1], flagged=case[2])

    servable = world.worker.servable  # type: ignore[attr-defined]
    resolved = servable.resolve_review_question(
        concept_id="con-1", parent_question_id="q1", user_ref="u", due_at=T0
    )

    assert resolved.variant_ref is None
    assert resolved.question.stable_id == "q1"
    assert _practice_ids(world) == list(_QUESTIONS)


@pytest.mark.parametrize(
    ("start", "target"),
    [
        (VariantState.PENDING_REVIEW, VariantState.REJECTED),
        (VariantState.SERVED, VariantState.WITHDRAWN),
    ],
)
def test_a_rejected_or_retired_variant_is_never_returned_after_the_change(
    start: VariantState, target: VariantState
) -> None:
    """After the change, the worker's review set no longer contains the variant.

    **Validates: Requirements 14.3, 14.4, 22.5, 22.6**
    """
    world = _world()
    _add(world, "v-1", start)
    assert (_review_refs(world) == {"v-1@c1"}) is (start is VariantState.SERVED)

    stored = world.variants.get("v-1")  # type: ignore[attr-defined]
    assert stored is not None
    world.variants.save(replace(stored, state=target))  # type: ignore[attr-defined]

    assert _review_refs(world) == set()


def test_a_flagged_variant_set_through_the_item_flag_repository_is_never_returned_after() -> None:
    """A flag written directly to ``item_flag`` removes the variant from the next review set.

    **Validates: Requirements 14.3, 14.4, 22.5, 22.6**
    """
    world = _world()
    _add(world, "v-1", VariantState.APPROVED)
    assert _review_refs(world) == {"v-1@c1"}

    _flag(world, "v-1")

    assert _review_refs(world) == set()


def test_a_flagged_question_is_in_no_review_or_practice_set() -> None:
    """A flagged parent is replaced by another servable question and never listed.

    **Validates: Requirements 14.3, 22.2, 22.7**
    """
    world = _world()
    _add(world, "v-1", VariantState.APPROVED)
    world.flags.flag(  # type: ignore[attr-defined]
        ItemKey(ItemKind.QUESTION, "q1"), FlagReason(FlagReasonKind.REPORTS)
    )

    servable = world.worker.servable  # type: ignore[attr-defined]
    resolved = servable.resolve_review_question(
        concept_id="con-1", parent_question_id="q1", user_ref="u", due_at=T0
    )

    assert resolved.variant_ref is None
    assert resolved.question.stable_id == "q2"
    assert _practice_ids(world) == ["q2"]


# -- static: serving and review modules import no LLM port ----------------------------------

_SERVING_AND_REVIEW_MODULES = (
    "packages/domain/edu_it_domain/ai/serving.py",
    "packages/domain/edu_it_domain/ai/lifecycle.py",
    "packages/domain/edu_it_domain/ports/item_flags.py",
    "packages/domain/edu_it_domain/ports/variants.py",
    "packages/application/edu_it_application/ai/servable_catalog.py",
    "packages/application/edu_it_application/ai/review_tasks.py",
    "packages/application/edu_it_application/learning/run_review.py",
    "apps/api/edu_it_api/review_tasks.py",
    "apps/api/edu_it_api/review_tasks_schemas.py",
    "apps/api/edu_it_api/learning.py",
)

_LLM_MODULE_MARKERS = ("llm_provider", "edu_it_infrastructure.llm")
_LLM_NAMES = frozenset({"LLMProvider", "StructuredOutput", "LLMInput", "OutputSchema"})


def _imports(path: Path) -> list[tuple[str, frozenset[str]]]:
    """Every ``(module, imported names)`` of a file, including imports inside functions."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[tuple[str, frozenset[str]]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend((alias.name, frozenset()) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = ("." * node.level) + (node.module or "")
            found.append((module, frozenset(alias.name for alias in node.names)))
    return found


@pytest.mark.parametrize("relative", _SERVING_AND_REVIEW_MODULES)
def test_serving_and_review_modules_import_no_llm_port(relative: str) -> None:
    """No serving or review module imports the LLM port, an LLM type or the LLM infrastructure.

    **Validates: Requirements 14.3, 22.2**
    """
    path = _ROOT / relative
    assert path.is_file(), relative
    for module, names in _imports(path):
        assert not any(marker in module for marker in _LLM_MODULE_MARKERS), (relative, module)
        assert not names & _LLM_NAMES, (relative, module, sorted(names & _LLM_NAMES))
