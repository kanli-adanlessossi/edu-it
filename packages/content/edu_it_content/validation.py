"""The ordered content-validation checks behind ``edu-it content validate`` (Req 13).

This module is the framework-free core of the :mod:`edu_it_content.cli`
``content validate`` command. It loads authored content from a content root and
runs the six ordered checks the design mandates (learning-core design, "``edu-it
content validate``" table), each reporting failures that name the offending
item's ``stable_id`` and the rule that failed:

1. **Schema validation** — every authored item parses against its
   :mod:`edu_it_content.schemas` model (Req 13.1).
2. **Bilingual completeness** — every learner-facing field carries both ``fr``
   and ``en`` (Req 13.2). The schemas already enforce this at parse time via
   :class:`~edu_it_content.schemas.Localized`, so a bilingual gap surfaces as a
   schema error; this check re-states the rule by name for any residual gap.
3. **Reference resolution** — every ``*_stable_id`` reference resolves to an
   item that exists in the loaded set (Req 13.3).
4. **Prerequisite acyclicity** — the ``prerequisite`` graph over concepts has no
   cycle; a cycle is reported with the concepts that form it (Req 13.4).
5. **MDX allowlist** — every component referenced by an authored MDX body is on
   the allowlist (Req 13.5, ADR A5).
6. **AI reviewer present** — every item whose ``origin`` is ``ai`` has a
   recorded ``reviewed_by`` in provenance (Req 13.6).

The checks run **in order**; :func:`validate_content` collects every failure
across all checks so a single run reports as many problems as it can find rather
than stopping at the first. The CLI maps a non-empty failure list to a non-zero
exit and an all-clear to exit zero (Req 13.1).

On-disk format (documented design choice). Authored content is loaded from a
*content root* directory:

- ``items.json`` — a single JSON object mapping each content type to a list of
  authored items, e.g. ``{"track": [...], "module": [...], "concept": [...],
  "prerequisite": [...], ...}``. Each item is the JSON form of its
  :mod:`edu_it_content.schemas` model. This keeps the authored catalog in one
  validated place that mirrors the schema field set, which is the simplest
  representation consistent with the Pydantic schemas (the design leaves the
  concrete on-disk encoding to the implementation).
- ``mdx/`` — optional per-locale MDX bodies for lessons, referenced by
  ``{stable_id}.{locale}.mdx``. Bodies live outside the catalog because ADR A5
  stores MDX as the content payload keyed by ``stable_id`` and renders it
  through the ``apps/web`` allowlist; the validate command scans those bodies
  for component references and checks them against the same allowlist.

The MDX allowlist mirrors the vetted component set ADR A5 describes; it lives
here as the validation contract until ``apps/web`` owns the render-time list.
"""

from __future__ import annotations

import json
import posixpath
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ValidationError

from edu_it_content.schemas import (
    Concept,
    Lab,
    LabCheck,
    LabTask,
    Lesson,
    Module,
    Origin,
    Prerequisite,
    Project,
    Question,
    QuestionOption,
    Quiz,
    Rubric,
    RubricCriterion,
    Track,
)

__all__ = [
    "MDX_COMPONENT_ALLOWLIST",
    "ContentLoadError",
    "LoadedContent",
    "ValidationFailure",
    "load_content",
    "validate_content",
]

#: The rule names reported on failure, used verbatim in failure messages so CI
#: output and tests can key on a stable, human-readable rule identifier.
_RULE_SCHEMA = "schema-validation"
_RULE_BILINGUAL = "bilingual-completeness"
_RULE_REFERENCE = "reference-resolution"
_RULE_ACYCLIC = "prerequisite-acyclicity"
_RULE_MDX = "mdx-allowlist"
_RULE_REVIEWER = "ai-reviewer-present"
_RULE_LAB_REFERENCE = "lab-reference"
_RULE_LAB_TIMEOUT = "lab-check-timeout"
_RULE_TIMEOUTS_FIT = "check-timeouts-fit-budget"
_RULE_LAB_FEEDBACK = "lab-feedback-complete"
_RULE_LAB_BUNDLE_LOCATION = "lab-bundle-location"

#: Sandbox locations a learner can write to; a Check_Bundle must never live
#: under them (Req 20.6).
_LEARNER_WRITABLE_ROOTS: tuple[str, ...] = ("/workspace", "/home", "/tmp", "/var/tmp", "/dev/shm")

#: Fallback Usable_Run_Budget in seconds (deadline 15 - freeze bound 1 - reserve 2),
#: used only when the domain Config cannot be imported.
_FALLBACK_USABLE_RUN_BUDGET_SECONDS = 12.0

#: The vetted MDX components content bodies may reference (Req 13.5, ADR A5).
#: This is the validation-time mirror of the ``apps/web`` render allowlist: a
#: component outside this set is an unknown-component error. Kept small and
#: explicit so an authored body that reaches for an un-vetted widget fails
#: loudly rather than silently rendering nothing.
MDX_COMPONENT_ALLOWLIST: frozenset[str] = frozenset(
    {
        "Callout",
        "CodeBlock",
        "CommandOutput",
        "Figure",
        "Note",
        "Quiz",
        "Terminal",
        "Warning",
    }
)

#: Maps each content-type key in ``items.json`` to the schema model that parses
#: it. The order here is the order schema validation walks the types; it does
#: not affect correctness because every item is validated.
_SCHEMA_BY_TYPE: Mapping[str, type[BaseModel]] = {
    "track": Track,
    "module": Module,
    "lesson": Lesson,
    "lab": Lab,
    "lab_task": LabTask,
    "lab_check": LabCheck,
    "project": Project,
    "rubric": Rubric,
    "rubric_criterion": RubricCriterion,
    "quiz": Quiz,
    "question": Question,
    "question_option": QuestionOption,
    "concept": Concept,
    "prerequisite": Prerequisite,
}

#: The reference fields on each content type and the content type each one must
#: resolve to. Every value is a ``stable_id`` that must name an item present in
#: the loaded set (Req 13.3).
_REFERENCES: Mapping[str, Mapping[str, str]] = {
    "module": {"track_stable_id": "track"},
    "lesson": {"module_stable_id": "module"},
    "lab": {"lesson_stable_id": "lesson"},
    "lab_task": {"lab_stable_id": "lab"},
    "lab_check": {"lab_task_stable_id": "lab_task"},
    "rubric": {"project_stable_id": "project"},
    "rubric_criterion": {"rubric_stable_id": "rubric"},
    "quiz": {"lesson_stable_id": "lesson"},
    "question": {"quiz_stable_id": "quiz"},
    "question_option": {"question_stable_id": "question"},
    "prerequisite": {
        "concept_stable_id": "concept",
        "requires_concept_stable_id": "concept",
    },
}

#: Matches a capitalized JSX component reference in an MDX body — an opening tag
#: whose name starts with an uppercase letter (plain HTML tags are lowercase and
#: are not components). Self-closing and normal opening tags both match.
_MDX_COMPONENT_RE = re.compile(r"<([A-Z][A-Za-z0-9]*)\b")


@dataclass(frozen=True)
class ValidationFailure:
    """A single validation failure naming the rule and the offending item.

    ``rule`` is one of the stable rule names (e.g. ``"reference-resolution"``);
    ``stable_id`` is the offending item's immutable id (``"<unknown>"`` only when
    an item is so malformed its id cannot be read); ``detail`` is a short,
    human-readable explanation. :meth:`message` renders the three into the
    single-line form the CLI prints and CI surfaces.
    """

    rule: str
    stable_id: str
    detail: str

    def message(self) -> str:
        """Render the failure as ``[rule] stable_id: detail`` for CLI output."""
        return f"[{self.rule}] {self.stable_id}: {self.detail}"


class ContentLoadError(RuntimeError):
    """The content root could not be read (missing dir or unparseable JSON).

    Raised by :func:`load_content` when the content root does not exist, its
    ``items.json`` is missing, or ``items.json`` is not valid JSON of the
    expected shape. This is distinct from a :class:`ValidationFailure`: a load
    error means validation could not even begin, so the CLI reports it and exits
    non-zero without claiming the content is "invalid" per a specific rule.
    """


@dataclass(frozen=True)
class LoadedContent:
    """Authored content loaded from a content root, grouped for validation.

    ``raw_items`` maps each content type to the list of raw (pre-schema) item
    dicts as read from ``items.json``; schema validation parses these. ``root``
    is the content root the items were loaded from, used to resolve MDX bodies.
    """

    raw_items: Mapping[str, Sequence[Mapping[str, object]]]
    root: Path


#: Maps a per-type file stem (``tracks.json`` -> ``tracks``) in a track directory
#: to its content-type key. Both singular and plural stems are accepted.
_TYPE_BY_FILE_STEM: Mapping[str, str] = {
    **{key: key for key in _SCHEMA_BY_TYPE},
    **{f"{key}s": key for key in _SCHEMA_BY_TYPE},
    "rubric_criteria": "rubric_criterion",
    "quizzes": "quiz",
}

#: Subdirectories of the content root that never hold per-type catalog files.
_NON_TRACK_DIRS: frozenset[str] = frozenset({"mdx"})


def _read_item_list(path: Path, label: str) -> list[dict[str, object]]:
    """Read ``path`` as a JSON list of item objects, or raise ContentLoadError."""
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ContentLoadError(f"{label} is not valid JSON: {exc}") from exc
    if not isinstance(parsed, list) or not all(isinstance(item, dict) for item in parsed):
        raise ContentLoadError(f"{label} must be a list of item objects")
    return parsed


def load_content(root: str | Path) -> LoadedContent:
    """Load authored content from ``root``.

    Two layouts are supported and merged when both are present:

    - ``items.json`` at the root: an object mapping content-type keys to lists
      of item dicts.
    - Per-type files under track directories: ``<root>/<track>/<type>.json``
      (e.g. ``tracks.json``, ``lessons.json``), each a JSON list of items of
      that type. Items are merged by type across every file and track
      directory. A ``.json`` file whose stem is not a known content type is a
      load error, as is malformed JSON or a non-list payload.

    Raises :class:`ContentLoadError` if the root is missing, no content is
    found, or any file is malformed. Unknown top-level keys in ``items.json``
    are kept so schema validation reports them as unknown content types.
    """
    root_path = Path(root)
    if not root_path.is_dir():
        raise ContentLoadError(f"content root does not exist or is not a directory: {root_path}")

    merged: dict[str, list[Mapping[str, object]]] = {}
    found_any = False

    items_file = root_path / "items.json"
    if items_file.is_file():
        found_any = True
        try:
            parsed = json.loads(items_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ContentLoadError(f"items.json is not valid JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise ContentLoadError(
                "items.json must be a JSON object mapping content types to lists"
            )
        for type_key, items in parsed.items():
            if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
                raise ContentLoadError(
                    f"items.json entry {type_key!r} must be a list of item objects"
                )
            merged.setdefault(type_key, []).extend(items)

    for track_dir in sorted(p for p in root_path.iterdir() if p.is_dir()):
        if track_dir.name in _NON_TRACK_DIRS:
            continue
        for json_file in sorted(track_dir.glob("*.json")):
            if json_file.name == PENDING_REVIEW_FILE:
                continue  # review bookkeeping, not a content type (Req 20.13)
            found_any = True
            type_key = _TYPE_BY_FILE_STEM.get(json_file.stem)
            if type_key is None:
                raise ContentLoadError(
                    f"unknown content type file {json_file.name!r} in {track_dir}: "
                    f"expected one of {sorted(set(_TYPE_BY_FILE_STEM.values()))} "
                    "(singular or plural .json)"
                )
            label = f"{track_dir.name}/{json_file.name}"
            merged.setdefault(type_key, []).extend(_read_item_list(json_file, label))

    if not found_any:
        raise ContentLoadError(
            f"content root has no items.json or per-type track files: {root_path}"
        )

    return LoadedContent(raw_items=merged, root=root_path)


#: Per-track file listing Seed_Lab ``stable_id``s awaiting expert review.
PENDING_REVIEW_FILE = "pending-review.json"


def load_pending_review_list(root: str | Path) -> frozenset[str]:
    """Load the Pending_Review_List from ``<root>/<track>/pending-review.json``.

    Each file is ``{"stable_ids": [...]}``; ids from every track directory are
    unioned. A missing file means an empty list. A malformed file raises
    :class:`ContentLoadError` (the list is never silently ignored).
    """
    root_path = Path(root)
    ids: set[str] = set()
    if not root_path.is_dir():
        return frozenset()
    for track_dir in sorted(p for p in root_path.iterdir() if p.is_dir()):
        path = track_dir / PENDING_REVIEW_FILE
        if not path.is_file():
            continue
        label = f"{track_dir.name}/{PENDING_REVIEW_FILE}"
        try:
            parsed = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ContentLoadError(f"{label} is not valid JSON: {exc}") from exc
        listed = parsed.get("stable_ids") if isinstance(parsed, dict) else None
        if not isinstance(listed, list) or not all(isinstance(i, str) for i in listed):
            raise ContentLoadError(f'{label} must be an object {{"stable_ids": [strings]}}')
        ids.update(listed)
    return frozenset(ids)


def apply_pending_allowance(
    failures: Sequence[ValidationFailure],
    content: LoadedContent,
    pending_ids: frozenset[str],
) -> list[ValidationFailure]:
    """Drop only ``ai-reviewer-present`` failures for listed labs (Req 20.13).

    Every other failure, and any unreviewed item that is not a listed lab, is
    kept.
    """
    lab_ids = {_raw_stable_id(lab) for lab in content.raw_items.get("lab", ())}
    allowed = pending_ids & lab_ids
    return [f for f in failures if not (f.rule == _RULE_REVIEWER and f.stable_id in allowed)]


def validate_content(
    content: LoadedContent,
    *,
    repo_root: Path | None = None,
    usable_run_budget_seconds: float | None = None,
) -> list[ValidationFailure]:
    """Run the six ordered checks over ``content`` and return every failure.

    Checks run in the design's order — schema, bilingual, reference, acyclicity,
    MDX allowlist, reviewer — accumulating failures across all of them so one run
    reports as many problems as possible. An empty result means every item is
    valid (the CLI exits zero); a non-empty result names each offending item's
    ``stable_id`` and the failing rule (the CLI exits non-zero). Reference,
    acyclicity, MDX, and reviewer checks run only over the items that parsed, so
    a schema failure on one item never masks problems on the rest.
    """
    failures: list[ValidationFailure] = []

    # 1. Schema validation (Req 13.1). Parse every item; keep the parsed models
    #    (grouped by type) to feed the later structural checks.
    parsed_by_type, schema_failures = _check_schema(content.raw_items)
    failures.extend(schema_failures)

    # 2. Bilingual completeness (Req 13.2). The schemas enforce fr+en at parse
    #    time, so a residual gap is only reachable on an item that parsed; this
    #    re-states the rule by name defensively.
    failures.extend(_check_bilingual(parsed_by_type))

    # 3. Reference resolution (Req 13.3).
    failures.extend(_check_references(parsed_by_type))

    # 4. Prerequisite acyclicity (Req 13.4).
    failures.extend(_check_acyclicity(parsed_by_type))

    # 5. MDX allowlist (Req 13.5, ADR A5).
    failures.extend(_check_mdx(parsed_by_type, content.root))

    # 6. AI reviewer present (Req 13.6).
    failures.extend(_check_reviewer(parsed_by_type))

    # 7. Lab rules (Req 18.4, 20.1-20.8). The bilingual and reviewer rules
    #    above already cover labs.
    budget = (
        usable_run_budget_seconds
        if usable_run_budget_seconds is not None
        else _usable_run_budget_seconds()
    )
    resolved_root = repo_root if repo_root is not None else _find_repo_root(content.root)
    failures.extend(_check_lab_reference(parsed_by_type, resolved_root))
    failures.extend(_check_lab_timeouts(content.raw_items, parsed_by_type, budget))
    failures.extend(_check_lab_feedback(content.raw_items, parsed_by_type))
    failures.extend(_check_lab_bundle_location(parsed_by_type))

    return failures


def _check_schema(
    raw_items: Mapping[str, Sequence[Mapping[str, object]]],
) -> tuple[dict[str, list[BaseModel]], list[ValidationFailure]]:
    """Parse every raw item against its schema (Req 13.1).

    Returns the parsed models grouped by content type and a failure for every
    item that did not parse — an unknown content type, or a Pydantic validation
    error. Each failure names the item's ``stable_id`` when it can be read from
    the raw dict (so even an item that fails to parse is still identified).
    """
    parsed_by_type: dict[str, list[BaseModel]] = {}
    failures: list[ValidationFailure] = []

    for type_key, items in raw_items.items():
        model = _SCHEMA_BY_TYPE.get(type_key)
        if model is None:
            for item in items:
                failures.append(
                    ValidationFailure(
                        rule=_RULE_SCHEMA,
                        stable_id=_raw_stable_id(item),
                        detail=f"unknown content type {type_key!r}",
                    )
                )
            continue

        parsed_by_type.setdefault(type_key, [])
        for item in items:
            try:
                parsed_by_type[type_key].append(model.model_validate(item))
            except ValidationError as exc:
                failures.append(
                    ValidationFailure(
                        rule=_RULE_SCHEMA,
                        stable_id=_raw_stable_id(item),
                        detail=_summarize_validation_error(exc),
                    )
                )

    return parsed_by_type, failures


def _check_bilingual(parsed_by_type: Mapping[str, Sequence[BaseModel]]) -> list[ValidationFailure]:
    """Report any learner-facing field missing an ``fr`` or ``en`` value (Req 13.2).

    Walks every parsed model's :class:`~edu_it_content.schemas.Localized` fields
    and flags one whose ``fr`` or ``en`` is empty. The schemas already reject a
    missing/empty locale at parse time, so a surviving gap here is defensive; the
    check exists so the bilingual rule is named explicitly in validation output.
    """
    failures: list[ValidationFailure] = []
    for items in parsed_by_type.values():
        for item in items:
            stable_id = _model_stable_id(item)
            for field_name, value in item:
                fr = getattr(value, "fr", None)
                en = getattr(value, "en", None)
                if fr is None or en is None:
                    continue
                if not str(fr).strip() or not str(en).strip():
                    failures.append(
                        ValidationFailure(
                            rule=_RULE_BILINGUAL,
                            stable_id=stable_id,
                            detail=f"field {field_name!r} is missing a French or English value",
                        )
                    )
    return failures


def _check_references(parsed_by_type: Mapping[str, Sequence[BaseModel]]) -> list[ValidationFailure]:
    """Report any ``*_stable_id`` reference that does not resolve (Req 13.3)."""
    known = _stable_ids_by_type(parsed_by_type)
    failures: list[ValidationFailure] = []

    for type_key, refs in _REFERENCES.items():
        for item in parsed_by_type.get(type_key, ()):
            stable_id = _model_stable_id(item)
            for ref_field, target_type in refs.items():
                target_id = getattr(item, ref_field, None)
                if target_id is None:
                    continue
                if str(target_id) not in known.get(target_type, frozenset()):
                    failures.append(
                        ValidationFailure(
                            rule=_RULE_REFERENCE,
                            stable_id=stable_id,
                            detail=(
                                f"{ref_field} {target_id!r} does not resolve "
                                f"to an existing {target_type}"
                            ),
                        )
                    )
    return failures


def _check_acyclicity(parsed_by_type: Mapping[str, Sequence[BaseModel]]) -> list[ValidationFailure]:
    """Report a cycle in the concept ``prerequisite`` graph, if any (Req 13.4).

    Builds the directed graph where an edge ``concept -> requires`` means the
    concept depends on ``requires`` being mastered first, then runs an iterative
    depth-first search. On the first cycle found, the failure names the concepts
    that form it so an author can see exactly which edges close the loop.
    """
    edges = parsed_by_type.get("prerequisite", ())
    if not edges:
        return []

    graph: dict[str, list[str]] = {}
    for edge in edges:
        assert isinstance(edge, Prerequisite)  # narrows the parsed type for mypy
        source = edge.concept_stable_id
        target = edge.requires_concept_stable_id
        graph.setdefault(source, []).append(target)
        graph.setdefault(target, [])

    cycle = _find_cycle(graph)
    if cycle is None:
        return []

    cycle_text = " -> ".join([*cycle, cycle[0]])
    # Report against the first concept in the cycle so the failure is tied to a
    # concrete stable_id, with the full cycle in the detail.
    return [
        ValidationFailure(
            rule=_RULE_ACYCLIC,
            stable_id=cycle[0],
            detail=f"prerequisite graph contains a cycle: {cycle_text}",
        )
    ]


def _check_mdx(
    parsed_by_type: Mapping[str, Sequence[BaseModel]],
    root: Path,
) -> list[ValidationFailure]:
    """Report any MDX component outside the allowlist (Req 13.5, ADR A5).

    For each lesson, looks for ``mdx/{stable_id}.{locale}.mdx`` bodies under the
    content root and scans each for capitalized JSX component references. A
    reference to a component not in :data:`MDX_COMPONENT_ALLOWLIST` is an
    unknown-component failure naming the lesson's ``stable_id``. Lessons with no
    MDX body on disk are skipped (bodies are authored separately per ADR A5).
    """
    failures: list[ValidationFailure] = []
    mdx_dir = root / "mdx"
    if not mdx_dir.is_dir():
        return failures

    for lesson in parsed_by_type.get("lesson", ()):
        stable_id = _model_stable_id(lesson)
        for body_path in sorted(mdx_dir.glob(f"{stable_id}.*.mdx")):
            text = body_path.read_text(encoding="utf-8")
            for component in _mdx_components(text):
                if component not in MDX_COMPONENT_ALLOWLIST:
                    failures.append(
                        ValidationFailure(
                            rule=_RULE_MDX,
                            stable_id=stable_id,
                            detail=(
                                f"MDX body {body_path.name!r} references "
                                f"non-allowlisted component <{component}>"
                            ),
                        )
                    )
    return failures


def _check_reviewer(parsed_by_type: Mapping[str, Sequence[BaseModel]]) -> list[ValidationFailure]:
    """Report any ``origin == ai`` item without a recorded reviewer (Req 13.6)."""
    failures: list[ValidationFailure] = []
    for items in parsed_by_type.values():
        for item in items:
            provenance = getattr(item, "provenance", None)
            if provenance is None:
                continue
            if provenance.origin is Origin.AI and not provenance.reviewed_by:
                failures.append(
                    ValidationFailure(
                        rule=_RULE_REVIEWER,
                        stable_id=provenance.stable_id,
                        detail="origin is 'ai' but no reviewer is recorded in provenance",
                    )
                )
    return failures


def _usable_run_budget_seconds() -> float:
    """Usable_Run_Budget from Config: deadline - freeze bound - reserve (12 s)."""
    try:
        from edu_it_domain.config import usable_run_budget
    except ImportError:  # pragma: no cover - domain package is in the workspace
        return _FALLBACK_USABLE_RUN_BUDGET_SECONDS
    return usable_run_budget().total_seconds()


def _find_repo_root(content_root: Path) -> Path:
    """Nearest ancestor of the content root holding ``lab-images/`` (else its parent)."""
    resolved = content_root.resolve()
    for candidate in (resolved, *resolved.parents):
        if (candidate / "lab-images").is_dir():
            return candidate
    return resolved.parent


def _labs_with_checks(
    parsed_by_type: Mapping[str, Sequence[BaseModel]],
) -> dict[str, list[LabCheck]]:
    """Group parsed Checks by the stable_id of the lab they belong to."""
    lab_of_task = {
        task.stable_id: task.lab_stable_id
        for task in parsed_by_type.get("lab_task", ())
        if isinstance(task, LabTask)
    }
    by_lab: dict[str, list[LabCheck]] = {}
    for check in parsed_by_type.get("lab_check", ()):
        if isinstance(check, LabCheck) and check.lab_task_stable_id in lab_of_task:
            by_lab.setdefault(lab_of_task[check.lab_task_stable_id], []).append(check)
    return by_lab


def _check_lab_reference(
    parsed_by_type: Mapping[str, Sequence[BaseModel]],
    repo_root: Path,
) -> list[ValidationFailure]:
    """Lab image, concepts, Check_Bundle and setup script must exist (Req 20.3)."""
    concept_ids = {_model_stable_id(c) for c in parsed_by_type.get("concept", ())}
    failures: list[ValidationFailure] = []
    for lab in parsed_by_type.get("lab", ()):
        if not isinstance(lab, Lab):
            continue
        lab_id = _model_stable_id(lab)
        problems: list[str] = []
        if not (repo_root / "lab-images" / lab.image).is_dir():
            problems.append(f"image {lab.image!r} does not exist under lab-images/")
        problems.extend(
            f"concept {cid!r} does not exist"
            for cid in lab.concept_stable_ids
            if cid not in concept_ids
        )
        if lab.check_bundle is not None and not (repo_root / lab.check_bundle).exists():
            problems.append(f"check_bundle {lab.check_bundle!r} does not exist")
        if lab.setup_script is not None and not (repo_root / lab.setup_script).is_file():
            problems.append(f"setup_script {lab.setup_script!r} does not exist")
        failures.extend(ValidationFailure(_RULE_LAB_REFERENCE, lab_id, p) for p in problems)
    return failures


def _check_lab_timeouts(
    raw_items: Mapping[str, Sequence[Mapping[str, object]]],
    parsed_by_type: Mapping[str, Sequence[BaseModel]],
    budget: float,
) -> list[ValidationFailure]:
    """Per-Check timeout present and within budget; per-lab sum within budget (Req 20.4, 20.8)."""
    failures: list[ValidationFailure] = []
    for raw in raw_items.get("lab_check", ()):
        if raw.get("timeout_seconds") is None:
            failures.append(
                ValidationFailure(_RULE_LAB_TIMEOUT, _raw_stable_id(raw), "no per-Check timeout")
            )
    for lab_id, checks in _labs_with_checks(parsed_by_type).items():
        for check in checks:
            if check.timeout_seconds > budget:
                failures.append(
                    ValidationFailure(
                        _RULE_TIMEOUTS_FIT,
                        check.stable_id,
                        f"timeout {check.timeout_seconds} s exceeds the "
                        f"Usable_Run_Budget of {budget:g} s",
                    )
                )
        total = sum(check.timeout_seconds for check in checks)
        if total > budget:
            failures.append(
                ValidationFailure(
                    _RULE_TIMEOUTS_FIT,
                    lab_id,
                    f"sum of Check timeouts {total} s exceeds the "
                    f"Usable_Run_Budget of {budget:g} s",
                )
            )
    return failures


def _check_lab_feedback(
    raw_items: Mapping[str, Sequence[Mapping[str, object]]],
    parsed_by_type: Mapping[str, Sequence[BaseModel]],
) -> list[ValidationFailure]:
    """Every outcome has a bilingual Feedback_Key, unique within the lab (Req 20.5)."""
    failures: list[ValidationFailure] = []
    for raw in raw_items.get("lab_check", ()):
        outcomes = raw.get("outcomes")
        if not isinstance(outcomes, list):
            continue
        for outcome in outcomes:
            if isinstance(outcome, Mapping) and not outcome.get("feedback_key"):
                failures.append(
                    ValidationFailure(
                        _RULE_LAB_FEEDBACK,
                        _raw_stable_id(raw),
                        f"outcome {outcome.get('code')!r} has no feedback_key",
                    )
                )
    for lab_id, checks in _labs_with_checks(parsed_by_type).items():
        seen: set[str] = set()
        for check in checks:
            for outcome in check.outcomes:
                if not outcome.message.fr.strip() or not outcome.message.en.strip():
                    failures.append(
                        ValidationFailure(
                            _RULE_LAB_FEEDBACK,
                            check.stable_id,
                            f"feedback {outcome.feedback_key!r} lacks a French or English text",
                        )
                    )
                if outcome.feedback_key in seen:
                    failures.append(
                        ValidationFailure(
                            _RULE_LAB_FEEDBACK,
                            lab_id,
                            f"feedback_key {outcome.feedback_key!r} is not unique within the lab",
                        )
                    )
                seen.add(outcome.feedback_key)
    return failures


def _check_lab_bundle_location(
    parsed_by_type: Mapping[str, Sequence[BaseModel]],
) -> list[ValidationFailure]:
    """The Check_Bundle must not live in a learner-writable location (Req 20.6)."""
    failures: list[ValidationFailure] = []
    for lab in parsed_by_type.get("lab", ()):
        if not isinstance(lab, Lab):
            continue
        for path in (lab.check_bundle_dest, lab.check_bundle):
            if path is None:
                continue
            normalized = posixpath.normpath(path)
            if any(
                normalized == root or normalized.startswith(root + "/")
                for root in _LEARNER_WRITABLE_ROOTS
            ):
                failures.append(
                    ValidationFailure(
                        _RULE_LAB_BUNDLE_LOCATION,
                        _model_stable_id(lab),
                        f"Check_Bundle path {path!r} is inside a learner-writable location",
                    )
                )
    return failures


def _find_cycle(graph: Mapping[str, list[str]]) -> list[str] | None:
    """Return the nodes forming a cycle in ``graph``, or ``None`` if acyclic.

    Iterative DFS with a three-color marking (unvisited / on the current path /
    done). When an edge reaches a node already on the current path, the slice of
    the path from that node onward is the cycle.
    """
    WHITE, GREY, BLACK = 0, 1, 2
    color: dict[str, int] = {node: WHITE for node in graph}

    for start in graph:
        if color[start] != WHITE:
            continue
        # Stack holds (node, index-of-next-neighbor-to-visit); ``path`` is the
        # current DFS path used to extract the cycle slice.
        stack: list[tuple[str, int]] = [(start, 0)]
        path: list[str] = []
        color[start] = GREY
        path.append(start)
        while stack:
            node, next_index = stack[-1]
            neighbors = graph.get(node, [])
            if next_index < len(neighbors):
                stack[-1] = (node, next_index + 1)
                neighbor = neighbors[next_index]
                if color.get(neighbor, WHITE) == GREY:
                    return path[path.index(neighbor) :]
                if color.get(neighbor, WHITE) == WHITE:
                    color[neighbor] = GREY
                    path.append(neighbor)
                    stack.append((neighbor, 0))
            else:
                color[node] = BLACK
                stack.pop()
                path.pop()
    return None


def _mdx_components(text: str) -> Iterable[str]:
    """Yield each distinct capitalized component name referenced in an MDX body."""
    seen: set[str] = set()
    for match in _MDX_COMPONENT_RE.finditer(text):
        name = match.group(1)
        if name not in seen:
            seen.add(name)
            yield name


def _stable_ids_by_type(
    parsed_by_type: Mapping[str, Sequence[BaseModel]],
) -> dict[str, frozenset[str]]:
    """Index the parsed items' ``stable_id`` values by content type."""
    index: dict[str, frozenset[str]] = {}
    for type_key, items in parsed_by_type.items():
        index[type_key] = frozenset(_model_stable_id(item) for item in items)
    return index


def _model_stable_id(item: BaseModel) -> str:
    """Read an item's ``stable_id``, whether it is top-level or on provenance.

    Sub-items (``lab_task``, ``rubric_criterion`` …) carry ``stable_id``
    directly; provenanced items (``track``, ``lesson`` …) carry it on their
    ``provenance`` record. This returns whichever is present.
    """
    provenance = getattr(item, "provenance", None)
    if provenance is not None:
        return str(provenance.stable_id)
    stable_id = getattr(item, "stable_id", None)
    return str(stable_id) if stable_id is not None else "<unknown>"


def _raw_stable_id(item: Mapping[str, object]) -> str:
    """Best-effort read of a ``stable_id`` from a raw (unparsed) item dict.

    Used when schema validation fails before a model exists, so even a malformed
    item is named in its failure. Looks at a top-level ``stable_id`` first, then
    a ``provenance.stable_id``; falls back to ``"<unknown>"``.
    """
    top = item.get("stable_id")
    if isinstance(top, str) and top:
        return top
    provenance = item.get("provenance")
    if isinstance(provenance, Mapping):
        nested = provenance.get("stable_id")
        if isinstance(nested, str) and nested:
            return nested
    return "<unknown>"


def _summarize_validation_error(exc: ValidationError) -> str:
    """Render a Pydantic validation error as a compact one-line summary."""
    parts: list[str] = []
    for error in exc.errors():
        location = ".".join(str(piece) for piece in error.get("loc", ()))
        parts.append(f"{location or '<root>'}: {error.get('msg', 'invalid')}")
    return "; ".join(parts)
