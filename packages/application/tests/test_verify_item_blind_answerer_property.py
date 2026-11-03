"""Property test for the blind independent answerer (ai-content task 7.3).

Feature: ai-content, Property 12: The independent answerer is blind and the key is never changed

**Validates: Requirements 7.2, 7.4, 12.5**
"""

from __future__ import annotations

import json

from edu_it_application.ai import (
    ItemKind,
    ItemUnderVerification,
    QuestionUnderVerification,
    VerifyItem,
)
from edu_it_domain.ai.verification import FindingKind, LabUnderVerification, VerificationFinding
from edu_it_infrastructure.fakes import Fake_LLMProvider, Fake_SandboxProvider, Test_Clock
from hypothesis import given, settings
from hypothesis import strategies as st


class _Labs:
    def execute(self, lab: LabUnderVerification) -> tuple[VerificationFinding, ...]:
        return ()


_text = st.text(min_size=1, max_size=30)


@st.composite
def _scenarios(draw: st.DrawFn) -> tuple[QuestionUnderVerification, list[int]]:
    options = tuple(draw(st.lists(_text, min_size=2, max_size=6)))
    indexes = st.integers(min_value=0, max_value=len(options) - 1)
    key = tuple(draw(st.lists(indexes, min_size=1, max_size=len(options))))
    answer = draw(st.lists(indexes, min_size=1, max_size=len(options)))
    locale = draw(st.sampled_from(["en", "fr"]))
    return QuestionUnderVerification(draw(_text), options, key, locale), answer


@settings(max_examples=200, deadline=None)
@given(scenario=_scenarios())
def test_answerer_is_blind_and_key_is_never_changed(
    scenario: tuple[QuestionUnderVerification, list[int]],
) -> None:
    question, answer = scenario
    llm = Fake_LLMProvider({"verify_answer": json.dumps({"selected": answer})})
    verify = VerifyItem(
        independent_llm=llm,
        sandbox=Fake_SandboxProvider(Test_Clock()),
        lab_verifier=_Labs(),
    )
    item = ItemUnderVerification("q1", ItemKind.QUESTION, question=question)

    findings, outputs = verify.execute(item)

    # Blind: exactly one call, with only the question, the options as given, and the locale.
    ((_, sent),) = llm.calls
    assert sent.kind == "verify_answer"
    assert set(sent.fields) == {"question", "options", "locale"}
    assert sent.fields["question"] == question.prompt
    assert json.loads(sent.fields["options"]) == list(question.options)
    assert sent.fields["locale"] == question.locale

    # Disagreement iff the answer sets differ; exactly one finding, none on a match.
    disagreements = [f for f in findings if f.kind is FindingKind.DISAGREEMENT]
    if set(answer) == set(question.key):
        assert disagreements == []
        assert findings == ()
    else:
        assert len(disagreements) == 1
        assert disagreements[0].target == "q1"
        assert [f.kind for f in findings] == [FindingKind.DISAGREEMENT]
    assert outputs == ()

    # The key is unchanged in both cases.
    assert item.question is question
    assert item.question.key == question.key
