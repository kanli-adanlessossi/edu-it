"""Runtime view of the synced lab content for the api and worker compositions.

The persistent PostgreSQL content store is not part of this phase, so the api and
the worker each load the authored content root into an :class:`In_Memory_ContentStore`
at start-up through the same validated :func:`~edu_it_content.sync.sync_content` the
``edu-it-content sync`` command uses. ``sync_content`` refuses an unreviewed ``ai``
lab, so only approved labs are ever served.

The two helpers below read the localized hint text and Feedback_Key messages that the
labs router needs; they never return anything the learner-facing schemas withhold
(hint text is only returned for a level the caller has already revealed, which the
use case enforces before the router calls :func:`hint_text`).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from pathlib import Path

from edu_it_content.content_store import In_Memory_ContentStore
from edu_it_content.sync import SyncReport, sync_content
from edu_it_content.validation import load_content

__all__ = [
    "feedback_messages_for",
    "hint_text_for",
    "load_synced_store",
    "setup_script_resolver",
]

_DEFAULT_LOCALE = "en"


def load_synced_store(content_root: str | Path) -> tuple[In_Memory_ContentStore, SyncReport]:
    """Load, validate and sync ``content_root`` into a fresh in-memory store."""
    store = In_Memory_ContentStore()
    report = sync_content(store, load_content(Path(content_root)))
    return store, report


def _text(texts: object, locale: str) -> str | None:
    if not isinstance(texts, Mapping):
        return None
    if locale in texts:
        return str(texts[locale])
    if _DEFAULT_LOCALE in texts:
        return str(texts[_DEFAULT_LOCALE])
    return next((str(v) for v in texts.values()), None)


def setup_script_resolver(
    store: In_Memory_ContentStore, repo_root: str | Path
) -> Callable[[object], Path | None]:
    """``lab -> host path of its setup script`` (``None`` when the lab has none).

    The authored ``setup_script`` is relative to the repository root; a path that
    escapes it is ignored rather than followed.
    """
    root = Path(repo_root).resolve()

    def resolve(lab: object) -> Path | None:
        item = store.get(str(getattr(lab, "lab_id", "")))
        if item is None or item.content_type != "lab":
            return None
        relative = json.loads(item.payload).get("setup_script")
        if not relative:
            return None
        path = (root / str(relative)).resolve()
        if root not in path.parents or not path.is_file():
            return None
        return path

    return resolve


def hint_text_for(store: In_Memory_ContentStore) -> Callable[[str, str, int, str], str | None]:
    """``(lab_id, task_id, level, locale) -> hint text`` over the synced lab tasks."""

    def hint_text(lab_id: str, task_id: str, level: int, locale: str) -> str | None:
        item = store.get(task_id)
        if item is None or item.content_type != "lab_task":
            return None
        payload = json.loads(item.payload)
        if payload.get("lab_stable_id") != lab_id:
            return None
        hints = payload.get("hints", [])
        if not 1 <= level <= len(hints):
            return None
        return _text(hints[level - 1], locale)

    return hint_text


def feedback_messages_for(
    store: In_Memory_ContentStore,
) -> Callable[[str, str], Mapping[str, str]]:
    """``(lab_id, locale) -> {feedback_key: message}`` over the synced lab Checks."""

    def feedback(lab_id: str, locale: str) -> Mapping[str, str]:
        task_ids = {
            item.stable_id
            for item in store.list_items("lab_task")
            if json.loads(item.payload).get("lab_stable_id") == lab_id
        }
        messages: dict[str, str] = {}
        for item in store.list_items("lab_check"):
            payload = json.loads(item.payload)
            if payload.get("lab_task_stable_id") not in task_ids:
                continue
            for outcome in payload.get("outcomes", ()):
                text = _text(outcome.get("message"), locale)
                if text is not None:
                    messages[str(outcome["feedback_key"])] = text
        return messages

    return feedback
