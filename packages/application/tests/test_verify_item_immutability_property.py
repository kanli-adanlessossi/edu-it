"""Property test: the Verifier never alters items or findings (ai-content task 7.5).

Feature: ai-content, Property 14: The Verifier never alters items or findings

**Validates: Requirements 7.11**
"""

from __future__ import annotations

import copy
import json

from edu_it_application.ai import (
    ItemKind,
    ItemUnderVerification,
    QuestionUnderVerification,
    VerificationContext,
    VerifyItem,
)
from edu_it_domain.ai.verification import (
    FindingKind,
    LabUnderVerification,
    VerificationFinding,
)
from edu_it_infrastructure.fakes import Fake_LLMProvider, Fake_SandboxProvider, Test_Clock
from hypothesis import given, settings
from hypothesis import strategies as st

_text = st.text(alphabet="abcdef .,éà", min_size=0, max_size=60)
_finding = st.builds(
    VerificationFinding,
    st.sampled_from(list(FindingKind)),
    st.text(alphabet="abc12", min_size=1, max_size=5),
    st.text(alphabet="abc xyz", max_size=10),
)


class _Labs:
    """Lab verifier that returns whatever check results the test generated."""

    def __init__(self, findings: tuple[VerificationFinding, ...]) -> None:
        self._findings = findings

    def execute(self, lab: LabUnderVerification) -> tuple[VerificationFinding, ...]:
        return self._findings


@st.composite
def _items(draw: st.DrawFn) -> ItemUnderVerification:
    kind = draw(st.sampled_from(list(ItemKind)))
    texts = draw(st.dictionaries(st.sampled_from(["en", "fr"]), _text, max_size=2))
    question = None
    if draw(st.booleans()):
        options = tuple(draw(st.lists(_text, min_size=2, max_size=4)))
        key = tuple(draw(st.lists(st.integers(0, len(options) - 1), max_size=3)))
        question = QuestionUnderVerification("Q?", options, key)
    lab = LabUnderVerification("lab1", "img", (), "") if draw(st.booleans()) else None
    commands = tuple(draw(st.lists(st.sampled_from(["ls", "echo hi", "true"]), max_size=3)))
    concepts = tuple(draw(st.lists(st.sampled_from(["a", "b", "c"]), max_size=3)))
    return ItemUnderVerification("item1", kind, texts, question, commands, "img", lab, concepts)


@st.composite
def _contexts(draw: st.DrawFn) -> VerificationContext:
    return VerificationContext(
        existing_texts=tuple(draw(st.lists(_text, max_size=3))),
        source_corpus=tuple(draw(st.lists(_text, max_size=3))),
        track_concepts=tuple(draw(st.lists(st.sampled_from(["a", "b", "c", "d"]), max_size=4))),
        covered_concepts=frozenset(draw(st.lists(st.sampled_from(["a", "b"]), max_size=2))),
    )


@settings(max_examples=100, deadline=None)
@given(
    item=_items(),
    ctx=_contexts(),
    lab_findings=st.lists(_finding, max_size=4).map(tuple),
    answer=st.lists(st.integers(0, 3), max_size=3),
)
def test_verifier_never_alters_items_or_findings(
    item: ItemUnderVerification,
    ctx: VerificationContext,
    lab_findings: tuple[VerificationFinding, ...],
    answer: list[int],
) -> None:
    """The item and context are unchanged and earlier findings survive verbatim."""
    llm = Fake_LLMProvider({"verify_answer": json.dumps({"selected": answer})})
    verify = VerifyItem(
        independent_llm=llm,
        sandbox=Fake_SandboxProvider(Test_Clock()),
        lab_verifier=_Labs(lab_findings),
    )
    item_before = copy.deepcopy(item)
    ctx_before = copy.deepcopy(ctx)

    findings, _ = verify.execute(item, ctx)

    assert item == item_before
    assert ctx == ctx_before
    if item.lab is not None:
        # The lab findings (earlier check results) are present, unchanged, in order.
        remaining = iter(findings)
        assert all(any(f == g for g in remaining) for f in lab_findings)

    # Verifying again only reproduces the same findings; nothing is removed or rewritten.
    again, _ = verify.execute(item, ctx)
    assert again == findings
    assert item == item_before
