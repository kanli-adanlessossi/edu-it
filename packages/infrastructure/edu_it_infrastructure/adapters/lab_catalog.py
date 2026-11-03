"""``Content_LabCatalog``: the ``LabCatalog`` adapter over the content store.

Reads the synced ``lab``, ``lab_task`` and ``lab_check`` items (hands-on-labs
Req 19.3, 20.10) and assembles :class:`~edu_it_domain.ports.lab_catalog.LabDefinition`
records: tasks with their ``required`` flag, Checks with timeout and outcomes
(outcome code -> Feedback_Key), bundle paths, and localized text by locale.

The store is any object with ``list_items(content_type)`` returning items that expose
``stable_id``, ``status`` and the canonical JSON ``payload`` (the shape of
``edu_it_content.content_store.ContentStore``). It is typed structurally here so the
infrastructure layer does not import the content package. Hint text is withheld: only
``hint_count`` is exposed. Archived labs are returned by :meth:`get` (flagged) but are
excluded from :meth:`list_labs` and :meth:`labs_covering`; archived tasks and Checks
are dropped.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Protocol

from edu_it_domain.ports.lab_catalog import (
    LabCheckDefinition,
    LabDefinition,
    LabTaskDefinition,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from edu_it_domain.labs.models import StableId

__all__ = ["Content_LabCatalog", "LabContentItem", "LabContentStore"]

_ARCHIVED = "archived"


class LabContentItem(Protocol):
    """The slice of a stored content item the catalog reads."""

    @property
    def stable_id(self) -> str: ...

    @property
    def status(self) -> object: ...

    @property
    def payload(self) -> str: ...


class LabContentStore(Protocol):
    """The slice of the content store the catalog reads."""

    def list_items(self, content_type: str) -> Sequence[LabContentItem]: ...


def _localized(value: object) -> dict[str, str]:
    """Return a ``{locale: text}`` mapping from a ``Localized`` payload object."""
    if not isinstance(value, dict):
        return {}
    return {str(k): str(v) for k, v in value.items()}


def _is_archived(item: LabContentItem) -> bool:
    return str(getattr(item.status, "value", item.status)) == _ARCHIVED


class Content_LabCatalog:
    """A :class:`LabCatalog` that reads lab definitions from the content store."""

    def __init__(self, store: LabContentStore) -> None:
        self._store = store

    def get(self, lab_id: StableId) -> LabDefinition | None:
        return self._build().get(lab_id)

    def list_labs(self) -> Sequence[LabDefinition]:
        return tuple(lab for lab in self._build().values() if not lab.archived)

    def labs_covering(self, concept_id: StableId) -> Sequence[LabDefinition]:
        return tuple(lab for lab in self.list_labs() if concept_id in lab.concept_ids)

    # -- assembly -----------------------------------------------------------

    def _build(self) -> dict[StableId, LabDefinition]:
        tasks_by_lab: dict[str, list[tuple[int, str, dict[str, Any]]]] = {}
        for item in self._store.list_items("lab_task"):
            if _is_archived(item):
                continue
            payload: dict[str, Any] = json.loads(item.payload)
            tasks_by_lab.setdefault(str(payload["lab_stable_id"]), []).append(
                (int(payload.get("ordinal", 0)), item.stable_id, payload)
            )

        checks_by_task: dict[str, list[tuple[str, dict[str, Any]]]] = {}
        for item in self._store.list_items("lab_check"):
            if _is_archived(item):
                continue
            payload = json.loads(item.payload)
            checks_by_task.setdefault(str(payload["lab_task_stable_id"]), []).append(
                (item.stable_id, payload)
            )

        labs: dict[StableId, LabDefinition] = {}
        for item in self._store.list_items("lab"):
            payload = json.loads(item.payload)
            lab_id = item.stable_id
            ordered = sorted(tasks_by_lab.get(lab_id, ()), key=lambda t: (t[0], t[1]))
            tasks = tuple(_task(task_id, p) for _, task_id, p in ordered)
            checks = tuple(
                _check(check_id, task_id, p)
                for _, task_id, _p in ordered
                for check_id, p in checks_by_task.get(task_id, ())
            )
            labs[lab_id] = LabDefinition(
                lab_id=lab_id,
                kind=str(payload["kind"]),
                image=str(payload.get("image") or ""),
                concept_ids=tuple(str(c) for c in payload.get("concept_stable_ids", ())),
                tasks=tasks,
                checks=checks,
                bundle_path=str(payload.get("check_bundle") or ""),
                archived=_is_archived(item),
                text=_localized(payload.get("title")),
                learner_files=tuple(str(f) for f in payload.get("learner_files", ())),
                content_version=str(getattr(item, "content_version", "") or ""),
                lesson_id=str(payload.get("lesson_stable_id") or ""),
            )
        return labs


def _task(task_id: str, payload: Mapping[str, Any]) -> LabTaskDefinition:
    return LabTaskDefinition(
        task_id=task_id,
        required=bool(payload.get("required", True)),
        text=_localized(payload.get("description")),
        hint_count=len(payload.get("hints", ())),
    )


def _check(check_id: str, task_id: str, payload: Mapping[str, Any]) -> LabCheckDefinition:
    outcomes = {str(o["code"]): str(o["feedback_key"]) for o in payload.get("outcomes", ())}
    return LabCheckDefinition(
        check_id=check_id,
        task_id=task_id,
        timeout_s=float(payload["timeout_seconds"]),
        outcomes=outcomes,
        bundle_path=str(payload.get("script") or ""),
        failure_exit_codes={
            int(o["exit_code"]): str(o["code"])
            for o in payload.get("outcomes", ())
            if o.get("exit_code") is not None and str(o["code"]) != "pass"
        },
    )
