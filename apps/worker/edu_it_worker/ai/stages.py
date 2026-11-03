"""Stage markers and committed units (ai-content Req 5.1, 5.14, 5.16, 12.6).

A Stage counts as done for a ref when all its marker files exist at that ref.
Markers are found with ``git ls-tree`` through the :class:`GitWorkspace` port;
the only file content read is the outline's ``lessons.json`` (to know which
lesson bodies the lessons Stage must have) and the unit files used to skip
already committed units on a rerun.

The ref is either the base the new branch starts from, or an explicitly named
Base_Ref. A Base_Ref is a source of markers and nothing else: this module has
no way to change any ref (Req 5.14, 5.16).
"""

from __future__ import annotations

import json
from typing import Final

from edu_it_domain.ai.generation import STAGES, UNIT_LOCALES
from edu_it_domain.ports.git_workspace import GitWorkspace

__all__ = [
    "STAGE_UNIT_FILES",
    "committed_units",
    "content_dir",
    "marker_files",
    "mdx_path",
    "stages_with_markers",
]

_OUTLINE_FILES: Final = (
    "tracks.json",
    "modules.json",
    "lessons.json",
    "concepts.json",
    "prerequisites.json",
)

#: JSON marker files per Stage, relative to ``content/<track>/``. The lessons
#: Stage has no JSON markers: its markers are the FR and EN bodies under ``mdx/``.
_JSON_MARKERS: Final[dict[str, tuple[str, ...]]] = {
    "brief": ("brief.json",),
    "outline": _OUTLINE_FILES,
    "lessons": (),
    "quizzes": ("quizzes.json", "questions.json", "question_options.json"),
    "labs": ("labs.json", "lab_tasks.json", "lab_checks.json"),
    "project": ("projects.json", "rubrics.json", "rubric_criteria.json"),
}

#: File holding the items that are the Generation_Units of a Stage.
STAGE_UNIT_FILES: Final[dict[str, str]] = {
    "quizzes": "quizzes.json",
    "labs": "labs.json",
    "project": "projects.json",
}


def content_dir(track: str) -> str:
    """Return ``content/<track>`` after rejecting unsafe track names."""
    if not track or "/" in track or "\\" in track or track.startswith(".") or ".." in track:
        msg = f"invalid track directory: {track!r}"
        raise ValueError(msg)
    return f"content/{track}"


def mdx_path(track: str, lesson_stable_id: str, locale: str) -> str:
    """Return the repository path of a lesson body in ``locale``."""
    if "/" in lesson_stable_id or ".." in lesson_stable_id:
        msg = f"invalid lesson id: {lesson_stable_id!r}"
        raise ValueError(msg)
    return f"{content_dir(track)}/mdx/{lesson_stable_id}.{locale}.mdx"


def _item_ids(text: str | None) -> tuple[str, ...]:
    """Return the ``provenance.stable_id`` (or ``stable_id``) of each item in a JSON list."""
    if text is None:
        return ()
    try:
        data = json.loads(text)
    except ValueError:
        return ()
    ids: list[str] = []
    for item in data if isinstance(data, list) else ():
        if not isinstance(item, dict):
            continue
        prov = item.get("provenance")
        raw = prov.get("stable_id") if isinstance(prov, dict) else item.get("stable_id")
        if isinstance(raw, str) and raw:
            ids.append(raw)
    return tuple(ids)


def _outline_lessons(git: GitWorkspace, ref: str, track: str) -> tuple[str, ...]:
    return _item_ids(git.read_text(ref, f"{content_dir(track)}/lessons.json"))


def marker_files(git: GitWorkspace, ref: str, track: str, stage: str) -> tuple[str, ...]:
    """Return every marker path ``stage`` needs at ``ref`` (Req 5.1)."""
    if stage not in STAGES:
        msg = f"unknown stage: {stage!r}"
        raise ValueError(msg)
    base = content_dir(track)
    paths = [f"{base}/{name}" for name in _JSON_MARKERS[stage]]
    if stage == "lessons":
        paths += [
            mdx_path(track, lesson, locale)
            for lesson in _outline_lessons(git, ref, track)
            for locale in UNIT_LOCALES
        ]
    return tuple(paths)


def stages_with_markers(git: GitWorkspace, ref: str, track: str) -> frozenset[str]:
    """Return the Stages whose markers all exist at ``ref`` (``git ls-tree`` only).

    The lessons Stage needs at least one outline lesson; an outline with no
    lessons gives no lessons markers, so the gate cannot pass on an empty set.
    """
    present = git.list_files(ref, f"{content_dir(track)}/")
    done: set[str] = set()
    for stage in STAGES:
        paths = marker_files(git, ref, track, stage)
        if paths and all(path in present for path in paths):
            done.add(stage)
    return frozenset(done)


def committed_units(git: GitWorkspace, ref: str, track: str, stage: str) -> frozenset[str]:
    """Return the ``stable_id`` of units already present at ``ref`` (Req 12.6).

    Used for resume: a rerun skips these. A lesson counts once both its FR and
    EN bodies exist; other units count by the item id in the Stage's unit file.
    The brief and the outline are single units named ``brief.<track>`` and
    ``outline.<track>``.
    """
    if stage not in STAGES:
        msg = f"unknown stage: {stage!r}"
        raise ValueError(msg)
    base = content_dir(track)
    present = git.list_files(ref, f"{base}/")
    if stage in {"brief", "outline"}:
        return (
            frozenset({f"{stage}.{track}"})
            if all(f"{base}/{name}" in present for name in _JSON_MARKERS[stage])
            else frozenset()
        )
    if stage == "lessons":
        return frozenset(
            lesson
            for lesson in _outline_lessons(git, ref, track)
            if all(mdx_path(track, lesson, loc) in present for loc in UNIT_LOCALES)
        )
    return frozenset(_item_ids(git.read_text(ref, f"{base}/{STAGE_UNIT_FILES[stage]}")))
