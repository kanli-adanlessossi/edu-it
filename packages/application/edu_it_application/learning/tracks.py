"""The track listing use case: the tracks a learner can enroll in, with their titles.

Tracks are listed in ``stable_id`` order and paged with the same opaque, expiring
cursor as the other learning lists (``api-conventions`` pagination). The catalog has
no per-track instant to sort on, so the keyset uses a fixed instant and the
``stable_id`` alone orders the pages.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

from edu_it_application.learning.get_lesson import FALLBACK_LOCALE
from edu_it_application.learning.pagination import CursorCodec, Page, _build_page, _clamp

if TYPE_CHECKING:
    from edu_it_domain.learning.models import StableId
    from edu_it_domain.ports.catalog_titles import CatalogTitleReader

__all__ = ["ListTracksUseCase", "TrackView"]

_SCOPE: Final = "tracks"
_SORT_INSTANT: Final = datetime(1970, 1, 1, tzinfo=UTC)
"""The constant keyset instant: tracks sort by ``stable_id`` only."""


@dataclass(frozen=True, slots=True)
class TrackView:
    """A track a learner can enroll in."""

    track_id: StableId
    title: str
    """The title in the requested locale, falling back to English; empty when untitled."""


class ListTracksUseCase:
    """List the catalog's non-archived tracks, one keyset page at a time."""

    def __init__(self, *, titles: CatalogTitleReader, codec: CursorCodec) -> None:
        """Wire the title port and the cursor codec shared by the learning lists."""
        self._titles = titles
        self._codec = codec

    def execute(
        self, locale: str, *, cursor: str | None = None, limit: int | None = None
    ) -> Page[TrackView]:
        """Return the page of tracks after ``cursor``.

        Raises:
            InvalidCursorError: ``cursor`` is malformed, expired or from another list.
        """
        size = _clamp(limit)
        after = self._codec.decode(_SCOPE, cursor)[1] if cursor else None
        ids = [i for i in self._titles.list_ids("track") if after is None or i > after]
        rows = tuple(self._view(track_id, locale) for track_id in ids[: size + 1])
        return _build_page(rows, size, self._codec, _SCOPE, lambda v: (_SORT_INSTANT, v.track_id))

    def _view(self, track_id: StableId, locale: str) -> TrackView:
        titles = self._titles.titles("track", track_id)
        return TrackView(
            track_id=track_id, title=titles.get(locale) or titles.get(FALLBACK_LOCALE, "")
        )
