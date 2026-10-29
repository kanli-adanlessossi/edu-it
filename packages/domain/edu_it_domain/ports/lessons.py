"""The lesson repository port (Req 17.3, D3 §6).

A read contract for authored lesson content, addressed by the lesson's immutable
``stable_id`` (D3 §1.2). A lesson carries one MDX body per locale; the learner
API serves the requested locale and falls back to English when no translation
exists (Req 17.3). Framework-free like the sibling learning ports.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from edu_it_domain.learning.models import StableId

__all__ = [
    "LessonRecord",
    "LessonRepository",
]


@dataclass(frozen=True, slots=True)
class LessonRecord:
    """A lesson and its per-locale content (D3 §6)."""

    stable_id: StableId
    """The lesson's immutable public identifier."""

    module_id: StableId
    """The ``stable_id`` of the module containing the lesson."""

    titles: Mapping[str, str] = field(default_factory=dict)
    """Locale -> learner-facing title."""

    bodies: Mapping[str, str] = field(default_factory=dict)
    """Locale -> MDX body. A locale with no translation is simply absent."""

    quiz_id: StableId | None = None
    """The ``stable_id`` of the quiz assessing the lesson, if any."""

    content_version: str = ""
    """The content hash of this lesson revision."""


@runtime_checkable
class LessonRepository(Protocol):
    """Port for reading authored lessons by ``stable_id``."""

    def get_lesson(self, stable_id: StableId) -> LessonRecord | None:
        """Return the lesson with ``stable_id``, or ``None`` when absent."""
        ...
