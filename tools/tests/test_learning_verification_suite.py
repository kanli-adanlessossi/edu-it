"""Learning-core verification suite checks that need the real Seed_Content (task 24.1).

These tests complement the per-feature suites by exercising, against the committed
``content/`` Seed_Content, the Requirement 30 checks that are about the whole phase
rather than one service:

- **30.1** every rule in ``.kiro/steering/domain-rules.md`` maps to at least one EARS
  requirement and at least one existing test, and the ≥90% domain coverage gate is
  configured and wired into the runners;
- **30.2** ``edu-it content validate`` fails on a missing translation, a broken
  reference, a prerequisite cycle, an unknown MDX component, and an AI item without a
  reviewer (mutating the Seed_Content one way at a time);
- **30.3** syncing the Seed_Content twice changes nothing the second time and a content
  edit never resets completions;
- **30.4** both Seed_Content lessons can be completed in ``fr`` and in ``en`` and the
  dependent module unlocks only through prerequisite mastery;
- **30.10** approval records the reviewer and marks the Seed_Content as the Golden_Set;
  without approval nothing is synced, attributed, or marked.

The remaining checks (shuffle/matching/cooldown/anti-farming/hints, the Clock-driven
review lifecycle, duplicate submission, export/deletion coverage) are covered by the
existing suites; ``tools/run_verification.py`` aggregates them with these.

**Validates: Requirements 30.1, 30.2, 30.3, 30.4, 30.10**
"""

from __future__ import annotations

import copy
import json
import re
import tomllib
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from edu_it_application.learning import GetLessonUseCase
from edu_it_content.content_store import In_Memory_ContentStore
from edu_it_content.sync import Approval, sync_content
from edu_it_content.validation import (
    LoadedContent,
    ValidationFailure,
    load_content,
    validate_content,
)
from edu_it_domain.config import MASTERY_THRESHOLDS
from edu_it_domain.learning.mastery import apply_delta
from edu_it_domain.learning.models import Mastery, MasteryState
from edu_it_domain.learning.unlocking import is_module_unlocked, record_lesson_completion
from edu_it_domain.ports.catalog import ModuleRecord
from edu_it_domain.ports.lessons import LessonRecord
from edu_it_infrastructure.fakes import In_Memory_ProgressRepository, Test_Clock

REPO = Path(__file__).resolve().parents[2]
CONTENT_ROOT = REPO / "content"
SEED_DIR = CONTENT_ROOT / "linux-fundamentals"
DOMAIN_RULES = REPO / ".kiro" / "steering" / "domain-rules.md"
REQUIREMENTS = REPO / ".kiro" / "specs" / "learning-core" / "requirements.md"

_INFRA = "packages/infrastructure/tests"
_DOMAIN_TEST = "packages/domain/tests"
_CONTENT = "packages/content/tests"
_API = "apps/api/tests"
_APP = "packages/application/tests"

_UNLOCK = f"{_INFRA}/test_unlocking_completion_properties.py"
_MASTERY = f"{_INFRA}/test_mastery_properties.py"
_SCORING = f"{_INFRA}/test_scoring_properties.py"
_ANTIFARM = f"{_INFRA}/test_antifarming_properties.py"
_HINTS = f"{_INFRA}/test_hints_properties.py"
_DECAY = f"{_INFRA}/test_decay_properties.py"
_REVIEW = f"{_INFRA}/test_review_properties.py"
_RUBRIC = f"{_INFRA}/test_rubric_properties.py"
_IDEMP = f"{_INFRA}/test_property_idempotency_key.py"

#: Sentinel for a rule with no requirement in this phase (labs are out of scope).
OUT_OF_SCOPE = "out-of-scope"

# (substring identifying a rule bullet, requirement ids, (test file, needle) pairs).
# The needle must occur in the test file, so a renamed or deleted test breaks the mapping.
RULE_MAP: list[tuple[str, list[str] | str, list[tuple[str, str]]]] = [
    ("Learners rebuild skills", ["2.1", "2.2", "2.4"], [(_UNLOCK, "Property 1:")]),
    (
        "three mechanisms",
        ["3.1", "10.1", "1.4"],
        [(_SCORING, "Property 3:"), (_RUBRIC, "Property 18:")],
    ),
    ("Decay never relocks", ["2.5", "8.3"], [(_UNLOCK, "Property 2:"), (_DECAY, "Property 14:")]),
    ("Mastery is `mastery`", ["5.1", "5.8"], [(_MASTERY, "Property 7:")]),
    ("first encounter", ["5.2"], [(_MASTERY, "Property 8:")]),
    ("30-59", ["5.3"], [(_MASTERY, "Property 8:")]),
    ("60-84", ["5.5"], [(_MASTERY, "Property 8:")]),
    (
        "passed review",
        ["5.6", "1.4"],
        [
            (_MASTERY, "Property 8:"),
            (_MASTERY, "test_mastered_requires_passed_lab_check_when_a_lab_covers_the_concept"),
        ],
    ),
    ("decay (never below 30 once Practiced)", ["5.9", "8.2"], [(_MASTERY, "Property 11:")]),
    ("decay (never below 30).", ["5.10", "8.2"], [(_MASTERY, "Property 11:")]),
    ("spaced review schedule", ["5.4", "9.1", "9.2"], [(_REVIEW, "Property 10:")]),
    ("Decay = -1/day", ["8.1", "8.4"], [(_DECAY, "Property 14:")]),
    ("Hint reveals are recorded", ["7.1", "7.2", "7.4"], [(_HINTS, "Property 13:")]),
    (
        "Duplicate quiz submissions",
        ["23.1", "6.1", "3.6"],
        [(_IDEMP, "Property 22:"), (_ANTIFARM, "Property 12:"), (_SCORING, "Property 5:")],
    ),
    (
        "Labs are hands-on",
        ["1.4"],
        [(_MASTERY, "test_mastered_requires_passed_lab_check_when_a_lab_covers_the_concept")],
    ),
    ("Grading is bounded", OUT_OF_SCOPE, []),
    (
        "A lab check contributes",
        ["1.4", "5.6"],
        [(_MASTERY, "test_mastered_requires_passed_lab_check_when_a_lab_covers_the_concept")],
    ),
    ("Projects are one of the three", ["10.1", "10.2"], [(_RUBRIC, "Property 18:")]),
    (
        "reviewed through the `ProjectReviewer`",
        ["10.4", "20.3"],
        [(_RUBRIC, "Property 18:"), (f"{_API}/test_project_submission_route.py", "def test_")],
    ),
    (
        "Placement is supported",
        ["21.1", "21.2"],
        [(f"{_API}/test_assessment_schemas.py", "def test_")],
    ),
    (
        "records `tested_out`",
        ["21.3"],
        [(f"{_INFRA}/test_record_tested_out.py", "def test_")],
    ),
]


def _rule_bullets() -> list[str]:
    """Each rule bullet of the steering file, with its ``Reason:`` continuation lines joined."""
    bullets: list[str] = []
    for line in DOMAIN_RULES.read_text(encoding="utf-8").splitlines():
        # A bullet ending in ":" only introduces a nested list (e.g. the state table).
        if re.match(r"^\s*- ", line) and not line.rstrip().endswith(":"):
            bullets.append(line.strip()[2:])
    return bullets


def _criteria_ids() -> set[str]:
    """Every ``N.k`` acceptance-criterion id declared in requirements.md."""
    ids: set[str] = set()
    current: str | None = None
    for line in REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        heading = re.match(r"^### Requirement (\d+):", line)
        if heading:
            current = heading.group(1)
            continue
        item = re.match(r"^(\d+)\. ", line)
        if item and current:
            ids.add(f"{current}.{item.group(1)}")
    return ids


# --- 30.1 rule -> requirement -> test mapping and coverage gate ------------------------


def test_every_domain_rule_maps_to_a_requirement_and_a_test() -> None:
    bullets = _rule_bullets()
    assert bullets, "no rules parsed from domain-rules.md"
    known = _criteria_ids()

    for bullet in bullets:
        entries = [entry for entry in RULE_MAP if entry[0] in bullet]
        assert entries, f"domain rule has no requirement/test mapping: {bullet!r}"
        for _, requirements, tests in entries:
            if requirements == OUT_OF_SCOPE:
                continue  # labs are out of scope (requirements.md "Out of scope")
            assert tests, f"rule {bullet!r} maps to requirements but no test"
            for requirement in requirements:
                assert requirement in known, f"unknown requirement {requirement} for {bullet!r}"
            for test_file, needle in tests:
                path = REPO / test_file
                assert path.is_file(), f"missing test file {test_file}"
                assert needle in path.read_text(encoding="utf-8"), f"{needle!r} not in {test_file}"


def test_every_mapping_entry_matches_a_real_rule() -> None:
    bullets = _rule_bullets()
    for key, _, _ in RULE_MAP:
        assert any(key in bullet for bullet in bullets), f"stale mapping for {key!r}"


def test_domain_coverage_gate_is_at_least_90_percent_and_wired_into_runners() -> None:
    config = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    assert config["tool"]["coverage"]["report"]["fail_under"] >= 90
    assert config["tool"]["coverage"]["run"]["source"] == ["edu_it_domain"]
    for runner in ("Makefile", "tasks.sh", "tasks.ps1"):
        assert "--cov=edu_it_domain" in (REPO / runner).read_text(encoding="utf-8"), runner


# --- Seed_Content helpers --------------------------------------------------------------


def _approved_raw(content: LoadedContent) -> dict[str, list[dict[str, Any]]]:
    """A deep copy of the seed's raw items with a reviewer recorded on every provenance."""
    raw = {
        key: copy.deepcopy([dict(item) for item in items])
        for key, items in content.raw_items.items()
    }
    for items in raw.values():
        for item in items:
            provenance = item.get("provenance")
            if isinstance(provenance, dict):
                provenance["reviewed_by"] = "usr_reviewer"
                provenance["reviewed_at"] = "2024-01-03T00:00:00+00:00"
    return raw


def _failures(raw: Mapping[str, Any], root: Path) -> list[ValidationFailure]:
    return validate_content(LoadedContent(raw_items=raw, root=root))


def _has(failures: list[ValidationFailure], rule: str, stable_id: str) -> bool:
    return any(f.rule == rule and f.stable_id == stable_id for f in failures)


@pytest.fixture(scope="module")
def seed() -> LoadedContent:
    return load_content(CONTENT_ROOT)


# --- 30.2 the five content validate failures ---------------------------------------------


def test_seed_content_with_reviewers_is_valid(seed: LoadedContent, tmp_path: Path) -> None:
    assert _failures(_approved_raw(seed), tmp_path) == []


def test_validate_fails_on_missing_translation(seed: LoadedContent, tmp_path: Path) -> None:
    raw = _approved_raw(seed)
    raw["lesson"][0]["title"] = {"en": "Reading permissions"}
    failures = _failures(raw, tmp_path)
    assert _has(failures, "schema-validation", "lesson.reading-permissions")
    assert any("fr" in f.detail for f in failures)


def test_validate_fails_on_broken_reference(seed: LoadedContent, tmp_path: Path) -> None:
    raw = _approved_raw(seed)
    raw["module"][0]["track_stable_id"] = "track.does-not-exist"
    assert _has(_failures(raw, tmp_path), "reference-resolution", "module.files-permissions")


def test_validate_fails_on_prerequisite_cycle(seed: LoadedContent, tmp_path: Path) -> None:
    raw = _approved_raw(seed)
    # concept.processes already requires concept.reading-permissions; close the loop.
    raw["prerequisite"].append(
        {
            "stable_id": "prerequisite.cycle",
            "concept_stable_id": "concept.reading-permissions",
            "requires_concept_stable_id": "concept.processes",
        }
    )
    assert any(f.rule == "prerequisite-acyclicity" for f in _failures(raw, tmp_path))


def test_validate_fails_on_unknown_mdx_component(seed: LoadedContent, tmp_path: Path) -> None:
    (tmp_path / "mdx").mkdir()
    (tmp_path / "mdx" / "lesson.reading-permissions.en.mdx").write_text(
        "# Lesson\n<TotallyUnknownWidget />\n", encoding="utf-8"
    )
    assert _has(
        _failures(_approved_raw(seed), tmp_path), "mdx-allowlist", "lesson.reading-permissions"
    )


def test_validate_fails_on_ai_item_without_reviewer(seed: LoadedContent, tmp_path: Path) -> None:
    raw = _approved_raw(seed)
    raw["track"][0]["provenance"]["reviewed_by"] = None
    failures = _failures(raw, tmp_path)
    assert _has(failures, "ai-reviewer-present", "track.linux-fundamentals")
    assert {f.rule for f in failures} == {"ai-reviewer-present"}


# --- 30.3 sync idempotence and progress preservation --------------------------------------

_APPROVAL = Approval(reviewer="usr_reviewer", reviewed_at=datetime(2024, 1, 3, tzinfo=UTC))


def test_second_sync_of_seed_makes_no_changes(seed: LoadedContent) -> None:
    store = In_Memory_ContentStore()
    first = sync_content(store, seed, approval=_APPROVAL)
    assert first.changed and first.inserted
    second = sync_content(store, seed, approval=_APPROVAL)
    assert not second.changed
    assert second.inserted == () and second.updated == () and second.archived == ()
    assert set(second.unchanged) == set(first.inserted)


def test_content_edit_updates_item_but_never_resets_completions(seed: LoadedContent) -> None:
    store = In_Memory_ContentStore()
    sync_content(store, seed, approval=_APPROVAL)
    store.seed_completion("lesson.reading-permissions", "completion-1")
    store.seed_completion("lesson.changing-permissions", "completion-2")
    before = store.completion_keys()

    raw = _approved_raw(seed)
    raw["lesson"][0]["title"] = {"fr": "Lire les droits", "en": "Reading permissions, revised"}
    raw["lesson"][0]["provenance"]["content_version"] = "sha256:edited"
    edited = replace(seed, raw_items=raw)
    report = sync_content(store, edited, approval=_APPROVAL)

    assert "lesson.reading-permissions" in report.updated
    assert report.archived == ()
    assert store.completion_keys() == before


# --- 30.4 both-language lesson completion and lock lifting --------------------------------


class _SeedLessons:
    """A LessonRepository built from the Seed_Content (bodies mirror the titles)."""

    def __init__(self, lessons: dict[str, LessonRecord]) -> None:
        self._lessons = lessons

    def get_lesson(self, stable_id: str) -> LessonRecord | None:
        return self._lessons.get(stable_id)


def _seed_json(name: str) -> list[dict[str, Any]]:
    data = json.loads((SEED_DIR / name).read_text(encoding="utf-8"))
    assert isinstance(data, list)
    return data


@pytest.mark.parametrize("locale", ["fr", "en"])
def test_both_seed_lessons_complete_in_each_language_and_locks_lift_by_mastery(
    locale: str,
) -> None:
    lessons = {
        item["provenance"]["stable_id"]: LessonRecord(
            stable_id=item["provenance"]["stable_id"],
            module_id=item["module_stable_id"],
            titles=item["title"],
            bodies={code: f"# {text}" for code, text in item["title"].items()},
        )
        for item in _seed_json("lessons.json")
    }
    first_module = "module.files-permissions"
    required_lessons = [lid for lid, rec in lessons.items() if rec.module_id == first_module]
    assert len(required_lessons) == 2

    clock = Test_Clock(start=datetime(2024, 6, 1, 9, tzinfo=UTC))
    progress = In_Memory_ProgressRepository()
    read = GetLessonUseCase(lessons=_SeedLessons(lessons))
    for lesson_id in required_lessons:
        view = read.execute(lesson_id, locale)
        assert view.locale == locale  # served natively, no English fallback
        assert view.title == lessons[lesson_id].titles[locale]
        record_lesson_completion(progress, clock, module_id=first_module, lesson_id=lesson_id)
    assert {c.lesson_id for c in progress.list_lesson_completions(first_module)} == set(
        required_lessons
    )

    prereqs = tuple(
        p["requires_concept_stable_id"]
        for p in _seed_json("prerequisites.json")
        if p["concept_stable_id"] == "concept.processes"
    )
    assert len(prereqs) == 2
    processes = ModuleRecord(
        stable_id="module.processes",
        track_id="track.linux-fundamentals",
        ordinal=1,
        prerequisite_concept_ids=prereqs,
    )

    def practiced(concept: str) -> Mastery:
        start = Mastery("usr-1", concept, 0, MasteryState.INTRODUCED)
        return apply_delta(start, MASTERY_THRESHOLDS.practiced, clock=clock, source="quiz")

    # Completing lessons alone never unlocks: advancement is by mastery only (Req 2.4).
    assert not is_module_unlocked(processes, {})
    # One prerequisite practiced is not enough; both lift the lock (Req 2.1, 2.2).
    partial = {prereqs[0]: practiced(prereqs[0])}
    assert not is_module_unlocked(processes, partial)
    full = {c: practiced(c) for c in prereqs}
    assert all(m.state is MasteryState.PRACTICED for m in full.values())
    assert is_module_unlocked(processes, full)


# --- 30.10 approval attribution and Golden_Set marking ------------------------------------


def test_approval_records_reviewer_and_marks_seed_as_golden_set(seed: LoadedContent) -> None:
    store = In_Memory_ContentStore()
    report = sync_content(store, seed, approval=_APPROVAL)
    assert store.golden_ids() == frozenset(report.inserted)
    for stable_id in (
        "track.linux-fundamentals",
        "lesson.reading-permissions",
        "quiz.reading-permissions",
    ):
        review = store.review_of(stable_id)
        assert review is not None and review[0] == "usr_reviewer"


def test_without_approval_nothing_is_synced_attributed_or_marked(seed: LoadedContent) -> None:
    # The committed seed now carries its recorded approval; strip it in memory to
    # exercise the unapproved path (Req 16.4).
    raw = copy.deepcopy({k: list(v) for k, v in seed.raw_items.items()})
    for items in raw.values():
        for item in items:
            provenance = item.get("provenance")
            if isinstance(provenance, dict):
                provenance["reviewed_by"] = None
                provenance["reviewed_at"] = None
    unapproved = replace(seed, raw_items=raw)
    store = In_Memory_ContentStore()
    with pytest.raises(ValueError, match="ai-reviewer-present"):
        sync_content(store, unapproved)
    assert store.golden_ids() == frozenset()
    assert store.list_active_ids() == frozenset()
    assert store.review_of("track.linux-fundamentals") is None
