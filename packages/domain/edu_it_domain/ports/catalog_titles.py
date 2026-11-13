"""Port for reading the learner-facing titles of catalog items (tracks, modules).

The structural catalog (:mod:`edu_it_domain.ports.catalog`) carries ordering and
prerequisites but no text. Titles are authored per locale and read through this
port, so the progress and track-listing use cases can name what they return without
the domain depending on the content store.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, runtime_checkable

from edu_it_domain.learning.models import StableId

__all__ = ["CatalogTitleReader"]


@runtime_checkable
class CatalogTitleReader(Protocol):
    """Port for the localized titles of non-archived catalog items."""

    def titles(self, kind: str, stable_id: StableId) -> Mapping[str, str]:
        """Return locale -> title for the item, or an empty mapping when unknown.

        ``kind`` is the content type (``"track"``, ``"module"``). A locale with no
        translation is simply absent.
        """
        ...

    def list_ids(self, kind: str) -> tuple[StableId, ...]:
        """Return the ``stable_id`` of every non-archived item of ``kind``, sorted."""
        ...
