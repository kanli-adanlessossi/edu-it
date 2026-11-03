"""Property 34: problem reports are stored once and bounded.

*For any* report and any repeat count under one ``Idempotency-Key``, exactly one report is
stored, holding the item's ``stable_id`` and ``content_version``, the category and the
optional comment, and every repeat returns the original result; a comment above
``PROBLEM_REPORT_COMMENT_MAX_CHARS`` is rejected with nothing stored, while a length at
or below it is accepted.

**Validates: Requirements 18.2, 18.3, 18.5**
"""

from __future__ import annotations

import pytest
from edu_it_application.ai import (
    InvalidProblemReportError,
    SubmitProblemReport,
    SubmitProblemReportRequest,
)
from edu_it_domain import config
from edu_it_domain.ports.item_flags import ItemKey, ItemKind
from edu_it_infrastructure.fakes import Test_Clock
from edu_it_infrastructure.fakes.idempotency import (
    In_Memory_IdempotencyStore,
    Sequential_IdGenerator,
)
from edu_it_infrastructure.fakes.problem_reports import In_Memory_ProblemReportRepository
from hypothesis import given, settings
from hypothesis import strategies as st

_MAX = config.PROBLEM_REPORT_COMMENT_MAX_CHARS
_TEXT = st.text(max_size=40)
_IDS = st.text(alphabet="abcdef0123456789-", min_size=1, max_size=12)


def _build() -> tuple[SubmitProblemReport, In_Memory_ProblemReportRepository]:
    reports = In_Memory_ProblemReportRepository()
    use_case = SubmitProblemReport(
        reports=reports,
        idempotency=In_Memory_IdempotencyStore(),
        clock=Test_Clock(),
        ids=Sequential_IdGenerator("rep"),
    )
    return use_case, reports


def _request(
    *,
    kind: ItemKind,
    item: str,
    version: str,
    category: str,
    comment: str | None,
    key: str,
) -> SubmitProblemReportRequest:
    return SubmitProblemReportRequest(
        user_id="u-1",
        item=ItemKey(kind, item),
        content_version=version,
        category=category,
        idempotency_key=key,
        comment=comment,
    )


@settings(max_examples=60, deadline=None)
@given(
    kind=st.sampled_from(list(ItemKind)),
    item=_IDS,
    version=_IDS,
    category=st.sampled_from(config.PROBLEM_REPORT_CATEGORIES),
    comment=st.one_of(st.none(), _TEXT),
    key=_IDS,
    repeats=st.integers(min_value=0, max_value=5),
)
def test_repeats_store_exactly_one_report(
    kind: ItemKind,
    item: str,
    version: str,
    category: str,
    comment: str | None,
    key: str,
    repeats: int,
) -> None:
    use_case, reports = _build()
    req = _request(
        kind=kind, item=item, version=version, category=category, comment=comment, key=key
    )
    first = use_case.execute(req)
    assert not first.replayed
    for _ in range(repeats):
        again = use_case.execute(req)
        assert again.replayed
        assert again.report == first.report

    stored = reports.get(first.report.stable_id)
    assert stored == first.report
    assert reports.reporters_for(ItemKey(kind, item), version) == {"u-1"}
    assert reports.get("rep-2") is None  # only one id was ever minted and stored
    assert stored is not None
    assert stored.key == ItemKey(kind, item)
    assert stored.content_version == version
    assert stored.category == category
    assert stored.comment == comment


@settings(max_examples=40, deadline=None)
@given(
    length=st.one_of(
        st.integers(min_value=0, max_value=_MAX),
        st.integers(min_value=_MAX + 1, max_value=_MAX + 500),
    ),
    category=st.sampled_from(config.PROBLEM_REPORT_CATEGORIES),
)
def test_comment_length_bound(length: int, category: str) -> None:
    use_case, reports = _build()
    req = _request(
        kind=ItemKind.QUESTION,
        item="q1",
        version="v1",
        category=category,
        comment="x" * length,
        key="k1",
    )
    if length <= _MAX:
        result = use_case.execute(req)
        assert reports.get(result.report.stable_id) is not None
    else:
        with pytest.raises(InvalidProblemReportError) as exc:
            use_case.execute(req)
        assert exc.value.reason == "comment_too_long"
        assert reports.reporters_for(ItemKey(ItemKind.QUESTION, "q1"), "v1") == frozenset()


@settings(max_examples=30, deadline=None)
@given(category=_TEXT.filter(lambda c: c not in config.PROBLEM_REPORT_CATEGORIES))
def test_invalid_category_stores_nothing(category: str) -> None:
    use_case, reports = _build()
    req = _request(
        kind=ItemKind.QUESTION, item="q1", version="v1", category=category, comment=None, key="k"
    )
    with pytest.raises(InvalidProblemReportError) as exc:
        use_case.execute(req)
    assert exc.value.reason == "invalid_category"
    assert reports.reporters_for(ItemKey(ItemKind.QUESTION, "q1"), "v1") == frozenset()
