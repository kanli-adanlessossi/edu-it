"""Cursor-based keyset pagination for learning list endpoints (task 18.2, Req 17.5).

A list is paged by an opaque cursor naming the last item of the previous page:
its sort-key instant and ``stable_id`` (the tie-breaker). The cursor also carries
its issue instant, read from the injected ``Clock``, so one older than
:data:`~edu_it_domain.config.CURSOR_TTL` is rejected as expired. A cursor that
cannot be decoded, or that was issued for a different list, is rejected as
malformed. Both raise :class:`InvalidCursorError`, which the API boundary renders
as a 400 problem.

Each use case fetches ``limit + 1`` rows to learn whether another page exists
without a count query, and never uses an offset.
"""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from edu_it_domain.config import CURSOR_TTL, DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE

if TYPE_CHECKING:
    from collections.abc import Callable

    from edu_it_domain.learning.models import QuizAttempt, StableId
    from edu_it_domain.ports.clock import Clock
    from edu_it_domain.ports.enrollment import EnrollmentRecord
    from edu_it_domain.ports.pagination import AttemptPageReader, EnrollmentPageReader, Keyset

__all__ = [
    "CursorCodec",
    "InvalidCursorError",
    "ListEnrollmentsUseCase",
    "ListQuizAttemptsUseCase",
    "Page",
]


class InvalidCursorError(Exception):
    """The pagination cursor is malformed or expired (400)."""

    MALFORMED = "malformed"
    EXPIRED = "expired"

    def __init__(self, reason: str) -> None:
        """Record the machine-readable ``reason`` (log-only)."""
        super().__init__(f"invalid pagination cursor: {reason}")
        self.reason = reason


@dataclass(frozen=True, slots=True)
class Page[T]:
    """One page of results and the cursor for the next, or ``None`` on the last page."""

    items: tuple[T, ...]
    next_cursor: str | None


class CursorCodec:
    """Encode and decode opaque, scope-bound, expiring cursors."""

    def __init__(self, clock: Clock) -> None:
        """Wire the clock that stamps and ages cursors."""
        self._clock = clock

    def encode(self, scope: str, key: Keyset) -> str:
        """Return the opaque cursor for ``key`` within ``scope``."""
        payload = {
            "c": scope,
            "t": key[0].isoformat(),
            "s": key[1],
            "i": self._clock.now().isoformat(),
        }
        raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    def decode(self, scope: str, cursor: str) -> Keyset:
        """Return the keyset in ``cursor``; raise :class:`InvalidCursorError` if unusable."""
        try:
            padded = cursor + "=" * (-len(cursor) % 4)
            data = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
            if not isinstance(data, dict) or data["c"] != scope:
                raise InvalidCursorError(InvalidCursorError.MALFORMED)
            sort_key = datetime.fromisoformat(data["t"])
            stable_id = data["s"]
            issued_at = datetime.fromisoformat(data["i"])
            if not isinstance(stable_id, str) or not stable_id:
                raise InvalidCursorError(InvalidCursorError.MALFORMED)
        except InvalidCursorError:
            raise
        except (KeyError, TypeError, ValueError, binascii.Error):
            raise InvalidCursorError(InvalidCursorError.MALFORMED) from None
        if issued_at.tzinfo is None or self._clock.now() - issued_at > CURSOR_TTL:
            raise InvalidCursorError(InvalidCursorError.EXPIRED)
        return sort_key, stable_id


def _clamp(limit: int | None) -> int:
    if limit is None:
        return DEFAULT_PAGE_SIZE
    return max(1, min(limit, MAX_PAGE_SIZE))


def _build_page[T](
    rows: tuple[T, ...],
    limit: int,
    codec: CursorCodec,
    scope: str,
    key_of: Callable[[T], Keyset],
) -> Page[T]:
    items = rows[:limit]
    more = len(rows) > limit
    next_cursor = codec.encode(scope, key_of(items[-1])) if more and items else None
    return Page(items=items, next_cursor=next_cursor)


class ListEnrollmentsUseCase:
    """Page through a learner's enrollments, oldest first."""

    def __init__(self, *, enrollments: EnrollmentPageReader, codec: CursorCodec) -> None:
        """Wire the page reader and cursor codec."""
        self._enrollments = enrollments
        self._codec = codec

    def execute(
        self, user_id: StableId, *, cursor: str | None = None, limit: int | None = None
    ) -> Page[EnrollmentRecord]:
        """Return the page after ``cursor`` (the first page when ``None``)."""
        scope = "enrollments"
        after = None if cursor is None else self._codec.decode(scope, cursor)
        size = _clamp(limit)
        rows = self._enrollments.list_for_user_page(user_id, after=after, limit=size + 1)
        return _build_page(rows, size, self._codec, scope, lambda e: (e.enrolled_at, e.stable_id))


class ListQuizAttemptsUseCase:
    """Page through a learner's attempts at one quiz, oldest first."""

    def __init__(self, *, attempts: AttemptPageReader, codec: CursorCodec) -> None:
        """Wire the page reader and cursor codec."""
        self._attempts = attempts
        self._codec = codec

    def execute(
        self,
        user_id: StableId,
        quiz_id: StableId,
        *,
        cursor: str | None = None,
        limit: int | None = None,
    ) -> Page[QuizAttempt]:
        """Return the page after ``cursor`` (the first page when ``None``)."""
        scope = f"quiz-attempts:{quiz_id}"
        after = None if cursor is None else self._codec.decode(scope, cursor)
        size = _clamp(limit)
        rows = self._attempts.list_quiz_attempts_page(user_id, quiz_id, after=after, limit=size + 1)
        return _build_page(rows, size, self._codec, scope, lambda a: (a.attempted_at, a.stable_id))
