"""Stage order, unit planning and resume rules (Requirements 5.1, 5.3, 5.14, 5.15, 12.6)."""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping
from typing import Final

#: Stages in generation order (Requirement 5.1).
STAGES: Final[tuple[str, ...]] = ("brief", "outline", "lessons", "quizzes", "labs", "project")

#: Locales every Generation_Unit must be produced in (Requirement 5.10).
UNIT_LOCALES: Final[tuple[str, ...]] = ("fr", "en")


def preceding_stage(stage: str) -> str | None:
    """Return the Stage before ``stage``, or ``None`` for the first one."""
    if stage not in STAGES:
        raise ValueError(f"unknown stage: {stage!r}")
    index = STAGES.index(stage)
    return None if index == 0 else STAGES[index - 1]


def missing_stage(
    stage: str,
    merged_stages: Collection[str],
    base_ref_stages: Collection[str] = (),
) -> str | None:
    """Return the preceding Stage lacking markers, or ``None`` when the gate passes.

    Markers count when found in merged output or on a named Base_Ref. The
    Base_Ref only widens where markers may be found; it never skips the gate.
    """
    previous = preceding_stage(stage)
    if previous is None:
        return None
    if previous in merged_stages or previous in base_ref_stages:
        return None
    return previous


def stage_allowed(
    stage: str,
    merged_stages: Collection[str],
    base_ref_stages: Collection[str] = (),
) -> bool:
    """True when ``stage`` may run (Requirements 5.3, 5.14, 5.15)."""
    return missing_stage(stage, merged_stages, base_ref_stages) is None


def plan_units(stable_ids: Iterable[str]) -> tuple[str, ...]:
    """Return the Generation_Units to produce: unique ids, first-seen order."""
    return tuple(dict.fromkeys(stable_ids))


def remaining_units(planned: Iterable[str], existing: Collection[str]) -> tuple[str, ...]:
    """Return planned units not yet committed or merged, keyed by ``stable_id`` (12.6)."""
    return tuple(unit for unit in plan_units(planned) if unit not in existing)


def commit_set(
    planned: Iterable[str],
    produced: Mapping[str, Collection[str]],
    locales: Collection[str] = UNIT_LOCALES,
) -> tuple[str, ...]:
    """Return the planned units fully completed, i.e. produced in every locale.

    Partially produced units are discarded (Requirement 12.7); order follows
    the plan.
    """
    return tuple(
        unit
        for unit in plan_units(planned)
        if all(locale in produced.get(unit, ()) for locale in locales)
    )


__all__ = [
    "STAGES",
    "UNIT_LOCALES",
    "commit_set",
    "missing_stage",
    "plan_units",
    "preceding_stage",
    "remaining_units",
    "stage_allowed",
]
