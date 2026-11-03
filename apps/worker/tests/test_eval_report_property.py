"""Property 22: the Eval_Report is deterministic and its pass rate is exact.

For any set of case outcomes and any ordering of the input cases, the report is byte-identical,
names each Prompt version and case outcome, and its aggregate pass rate equals passed cases over
all cases.

**Validates: Requirements 11.2, 11.5**
"""

from __future__ import annotations

import json
from decimal import ROUND_HALF_UP, Decimal

from edu_it_worker.ai.eval_runner import CASE_KINDS, CaseOutcome, build_report
from hypothesis import given, settings
from hypothesis import strategies as st

_PLACES = Decimal("0.0001")

_OUTCOMES = st.lists(
    st.builds(
        CaseOutcome,
        case_id=st.text(
            alphabet="abcdefghijklmnopqrstuvwxyz0123456789-_.", min_size=1, max_size=10
        ),
        kind=st.sampled_from(CASE_KINDS),
        passed=st.booleans(),
        detail=st.text(max_size=20),
    ),
    max_size=30,
    unique_by=lambda o: o.case_id,
)
_VERSIONS = st.lists(
    st.tuples(
        st.sampled_from(["draft_lesson", "verify_answer", "glossary"]), st.sampled_from("123")
    ),
    max_size=6,
)


@settings(max_examples=200, deadline=None)
@given(outcomes=_OUTCOMES, versions=_VERSIONS, data=st.data())
def test_property_22_eval_report_is_deterministic_and_pass_rate_exact(
    outcomes: list[CaseOutcome], versions: list[tuple[str, str]], data: st.DataObject
) -> None:
    """Feature: ai-content, Property 22: The Eval_Report is deterministic and its pass rate is exact

    **Validates: Requirements 11.2, 11.5**
    """
    shuffled = data.draw(st.permutations(outcomes))
    shuffled_versions = data.draw(st.permutations(versions))

    report = build_report("golden", versions, outcomes)
    other = build_report("golden", shuffled_versions, shuffled)

    # Byte-identical regardless of input order.
    assert report.to_json() == other.to_json()
    assert report == other

    document = json.loads(report.to_json())

    # Each Prompt version and each case outcome is named.
    assert document["prompt_versions"] == dict(sorted(set(versions)))
    assert {o["id"]: (o["kind"], o["passed"], o["detail"]) for o in document["outcomes"]} == {
        o.case_id: (o.kind, o.passed, o.detail) for o in outcomes
    }

    # The pass rate is exactly passed over total, to four places, half up.
    passed = sum(1 for o in outcomes if o.passed)
    assert document["passed"] == passed
    assert document["total"] == len(outcomes)
    expected = (Decimal(passed) / Decimal(len(outcomes)) if outcomes else Decimal(0)).quantize(
        _PLACES, ROUND_HALF_UP
    )
    assert document["pass_rate"] == str(expected)
