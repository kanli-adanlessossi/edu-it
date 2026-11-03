"""The ``ParentQuestionSource`` port (ai-content Req 13.6).

Variants are generated only from a Parent_Question whose state is Approved. The
catalog records carry structure and the text lives in the content store, so the
refill job reads the parent through this port: its text, its track and concept,
and whether it is Approved (design rule 14.1 for authored items: ``human``, or
``ai`` with both reviewer fields, not archived).

Everything is addressed by ``stable_id``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

__all__ = ["ParentQuestion", "ParentQuestionSource"]


@dataclass(frozen=True, slots=True)
class ParentQuestion:
    """An authored question as the refill job needs it."""

    stable_id: str
    track_stable_id: str
    concept_stable_id: str
    concept_title: str
    """English concept title, sent to the model."""

    prompts: Mapping[str, str]
    """The question prompt per locale (``en`` and ``fr``)."""

    options: tuple[str, ...]
    """English option texts in option order. The key is never part of this record."""

    approved: bool
    """True when the question is Approved: reviewed, not archived."""


@runtime_checkable
class ParentQuestionSource(Protocol):
    """Port reading a parent question."""

    def get(self, stable_id: str) -> ParentQuestion | None:
        """Return the question, or ``None`` when unknown."""
        ...

    def approved_ids(self) -> Sequence[str]:
        """``stable_id`` of every Approved question, sorted. The refill cycle scans these."""
        ...
