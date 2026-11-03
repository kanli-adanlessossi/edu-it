"""The ``LabCatalog`` port and lab definition value types (hands-on-labs Req 1.1).

The catalog adapter (over the content store) exposes :class:`LabDefinition`
records and ``labs_covering``. Framework-free; standard library only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from edu_it_domain.labs.models import StableId

__all__ = [
    "LabCatalog",
    "LabCheckDefinition",
    "LabDefinition",
    "LabTaskDefinition",
]


@dataclass(frozen=True, slots=True)
class LabCheckDefinition:
    """One Check of a task."""

    check_id: StableId
    task_id: StableId
    timeout_s: float
    outcomes: Mapping[str, str] = field(default_factory=dict)
    """Outcome code -> feedback key."""

    bundle_path: str = ""

    failure_exit_codes: Mapping[int, str] = field(default_factory=dict)
    """Declared failure exit code -> outcome code (e.g. ``11`` -> ``missing-docs``).

    Only codes declared here, besides ``0`` (pass) and ``1`` (generic fail), are a
    ``failed`` verdict; any other code stays ``could_not_complete``."""


@dataclass(frozen=True, slots=True)
class LabTaskDefinition:
    """One task of a lab."""

    task_id: StableId
    required: bool
    text: Mapping[str, str] = field(default_factory=dict)
    """Localized text by locale."""

    hint_count: int = 0
    """Number of progressive hint levels the task offers (text is withheld here)."""


@dataclass(frozen=True, slots=True)
class LabDefinition:
    """A lab as the catalog exposes it."""

    lab_id: StableId
    kind: str
    image: str
    concept_ids: tuple[StableId, ...]
    tasks: tuple[LabTaskDefinition, ...]
    checks: tuple[LabCheckDefinition, ...]
    bundle_path: str = ""
    archived: bool = False
    text: Mapping[str, str] = field(default_factory=dict)
    """Localized title by locale."""

    learner_files: tuple[str, ...] = ()
    """Flat file names a learner may save into the workspace (Code_Exercise)."""

    content_version: str = ""
    """The content hash of this lab revision; empty when the source has none."""

    lesson_id: StableId = ""
    """The ``stable_id`` of the owning lesson; empty when the source has none."""


@runtime_checkable
class LabCatalog(Protocol):
    """Port for reading lab definitions."""

    def get(self, lab_id: StableId) -> LabDefinition | None:
        """Return the lab, or ``None`` when unknown."""
        ...

    def list_labs(self) -> Sequence[LabDefinition]:
        """All non-archived labs."""
        ...

    def labs_covering(self, concept_id: StableId) -> Sequence[LabDefinition]:
        """Non-archived labs that list ``concept_id`` as covered."""
        ...
