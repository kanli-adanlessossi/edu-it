"""Tests for cursor-based keyset pagination (task 18.2, Req 17.5).

Runs the list use cases against the in-memory repositories and ``Test_Clock``.

**Validates: Requirements 17.5**
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta

import pytest
from edu_it_application.learning.pagination import (
    CursorCodec,
    InvalidCursorError,
    ListEnrollmentsUseCase,
    ListQuizAttemptsUseCase,
)
from edu_it_domain.config import CURSOR_TTL, MAX_PAGE_SIZE
from edu_it_domain.learning.models import QuizAttempt
from edu_it_domain.ports.enrollment import EnrollmentRecord
from edu_it_infrastructure.fakes import (
    In_Memory_AttemptRepository,
    In_Memory_EnrollmentRepository,
    Test_Clock,
)
from hypothesis import given, settings
from hypothesis import strategies as st

_T0 = datetime(2025, 1, 1, 12, 0, tzinfo=UTC)
_USER = "usr-1"
_QUIZ = "qz-1"


def _enrollments(minutes: list[int]) -> tuple[ListEnrollmentsUseCase, Test_Clock]:
    clock = Test_Clock(start=_T0)
    repo = In_Memory_EnrollmentRepository()
    for i, minute in enumerate(minutes):
        repo.add(
            EnrollmentRecord(
                stable_id=f"enr-{i:04d}",
                user_id=_USER,
                track_id=f"trk-{i}",
                enrolled_at=_T0 + timedelta(minutes=minute),
            )
        )
    return ListEnrollmentsUseCase(enrollments=repo, codec=CursorCodec(clock)), clock


def _attempts(minutes: list[int]) -> ListQuizAttemptsUseCase:
    repo = In_Memory_AttemptRepository()
    for i, minute in enumerate(minutes):
        repo.add_quiz_attempt(
            _USER,
            QuizAttempt(
                stable_id=f"att-{i:04d}",
                quiz_id=_QUIZ,
                score=50,
                is_best=False,
                attempted_at=_T0 + timedelta(minutes=minute),
                content_version="v1",
            ),
            (),
        )
    return ListQuizAttemptsUseCase(attempts=repo, codec=CursorCodec(Test_Clock(start=_T0)))


def test_pages_follow_sort_key_with_stable_id_tiebreak() -> None:
    use_case, _ = _enrollments([5, 5, 5, 1])
    first = use_case.execute(_USER, limit=2)
    assert [e.stable_id for e in first.items] == ["enr-0003", "enr-0000"]
    assert first.next_cursor is not None
    second = use_case.execute(_USER, cursor=first.next_cursor, limit=2)
    assert [e.stable_id for e in second.items] == ["enr-0001", "enr-0002"]
    assert second.next_cursor is None


def test_empty_list_has_no_cursor() -> None:
    use_case, _ = _enrollments([])
    page = use_case.execute(_USER)
    assert page.items == ()
    assert page.next_cursor is None


def test_limit_is_clamped_to_maximum() -> None:
    use_case, _ = _enrollments(list(range(MAX_PAGE_SIZE + 5)))
    assert len(use_case.execute(_USER, limit=MAX_PAGE_SIZE + 50).items) == MAX_PAGE_SIZE


@pytest.mark.parametrize("cursor", ["", "!!!", "bm90LWpzb24", "W10", "e30"])
def test_malformed_cursor_is_rejected(cursor: str) -> None:
    use_case, _ = _enrollments([1])
    with pytest.raises(InvalidCursorError) as info:
        use_case.execute(_USER, cursor=cursor)
    assert info.value.reason == InvalidCursorError.MALFORMED


def test_cursor_from_another_list_is_rejected() -> None:
    use_case, clock = _enrollments([1, 2, 3])
    token = CursorCodec(clock).encode("quiz-attempts:other", (_T0, "x"))
    with pytest.raises(InvalidCursorError):
        use_case.execute(_USER, cursor=token)


def test_cursor_with_bad_fields_is_rejected() -> None:
    use_case, clock = _enrollments([1])
    payload = {"c": "enrollments", "t": "nope", "s": "x", "i": clock.now().isoformat()}
    token = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    with pytest.raises(InvalidCursorError):
        use_case.execute(_USER, cursor=token)


def test_expired_cursor_is_rejected_and_fresh_boundary_accepted() -> None:
    use_case, clock = _enrollments([1, 2, 3])
    page = use_case.execute(_USER, limit=1)
    assert page.next_cursor is not None
    clock.advance(CURSOR_TTL)
    assert use_case.execute(_USER, cursor=page.next_cursor, limit=1).items
    clock.advance(timedelta(seconds=1))
    with pytest.raises(InvalidCursorError) as info:
        use_case.execute(_USER, cursor=page.next_cursor, limit=1)
    assert info.value.reason == InvalidCursorError.EXPIRED


def test_quiz_attempt_cursor_is_bound_to_its_quiz() -> None:
    use_case = _attempts([1, 2, 3])
    page = use_case.execute(_USER, _QUIZ, limit=1)
    assert page.next_cursor is not None
    with pytest.raises(InvalidCursorError):
        use_case.execute(_USER, "qz-other", cursor=page.next_cursor)


@settings(max_examples=60, deadline=None)
@given(
    minutes=st.lists(st.integers(min_value=0, max_value=5), max_size=30),
    limit=st.integers(min_value=1, max_value=7),
)
def test_pages_cover_every_item_exactly_once(minutes: list[int], limit: int) -> None:
    """Feature: learning-core, Property 28: Cursor pagination covers the full set exactly once.

    **Validates: Requirements 17.5**
    """
    use_case = _attempts(minutes)
    seen: list[str] = []
    cursor: str | None = None
    for _ in range(len(minutes) + 2):
        page = use_case.execute(_USER, _QUIZ, cursor=cursor, limit=limit)
        seen.extend(a.stable_id for a in page.items)
        cursor = page.next_cursor
        if cursor is None:
            break
    assert cursor is None
    assert sorted(seen) == [f"att-{i:04d}" for i in range(len(minutes))]
    assert len(seen) == len(set(seen))
