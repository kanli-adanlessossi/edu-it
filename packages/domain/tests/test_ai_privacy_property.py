"""Property test for the AI privacy filter (ai-content Property 6).

Property 6: LLM inputs carry only allowed data.

**Validates: Requirements 3.1, 3.2, 3.4, 18.6**
"""

from __future__ import annotations

import pytest
from edu_it_domain.ai.inputs import INPUT_FIELDS, InputKind
from edu_it_domain.ai.privacy import ensure_clean, scan, scan_payload
from edu_it_domain.ports.llm_provider import LLMInput, PersonalDataInRequestError
from hypothesis import assume, given
from hypothesis import strategies as st

# No '@', '_' or long runs: cannot look like an email, usr_/sess_ id or token.
_clean_text = st.text(alphabet="abcdefghij klmnop.,0123456789", max_size=30)
_kinds = st.sampled_from(sorted(InputKind))


@st.composite
def _clean_inputs(draw: st.DrawFn) -> LLMInput:
    kind = draw(_kinds)
    names = sorted(INPUT_FIELDS[kind])
    chosen = draw(st.lists(st.sampled_from(names), unique=True, max_size=len(names)))
    return LLMInput(kind.value, {name: draw(_clean_text) for name in chosen})


_pii = st.one_of(
    st.from_regex(r"[a-z]{1,8}\.[a-z]{1,8}@[a-z]{1,8}\.(com|org|io)", fullmatch=True),
    st.from_regex(r"usr_[A-Za-z0-9]{4,12}", fullmatch=True),
    st.from_regex(r"sess_[A-Za-z0-9]{8,16}", fullmatch=True),
    st.from_regex(r"[a-f0-9]{40}", fullmatch=True).filter(lambda s: not s.isdigit()),
)


@given(_clean_inputs())
def test_clean_allowlisted_inputs_are_accepted(value: LLMInput) -> None:
    assert scan(value) == ()
    ensure_clean(value)


@given(_clean_inputs(), st.text(alphabet="qrstuvwxyz", min_size=1, max_size=10).map("x{}".format))
def test_unknown_field_is_rejected(value: LLMInput, extra: str) -> None:
    if extra in INPUT_FIELDS[InputKind(value.kind)]:
        return
    bad = LLMInput(value.kind, {**value.fields, extra: "fine"})
    with pytest.raises(PersonalDataInRequestError):
        ensure_clean(bad)


@given(_clean_inputs(), _pii, _clean_text, _clean_text)
def test_personal_data_value_is_rejected_without_echo(
    value: LLMInput, pii: str, before: str, after: str
) -> None:
    field = sorted(INPUT_FIELDS[InputKind(value.kind)])[0]
    bad = LLMInput(value.kind, {**value.fields, field: f"{before} {pii} {after}"})
    with pytest.raises(PersonalDataInRequestError) as err:
        ensure_clean(bad)
    assert pii not in str(err.value)


@given(
    _clean_inputs(),
    st.text(alphabet="ABCDEFGH", min_size=3, max_size=10),
    st.integers(min_value=0, max_value=3),
)
def test_seeded_sample_is_rejected_at_any_depth(value: LLMInput, sample: str, depth: int) -> None:
    # The clean-text alphabet overlaps the sample alphabet, so a generated
    # clean field may legitimately contain the (case-folded) sample.
    assume(all(sample.lower() not in v.lower() for v in value.fields.values()))
    field = sorted(INPUT_FIELDS[InputKind(value.kind)])[0]
    nested: object = f"note {sample.lower()} end"
    for _ in range(depth):
        nested = {"k": [nested]}
    payload = {**value.fields, field: nested}
    assert scan_payload(payload, seeded=(sample,))
    assert not scan_payload(value.fields, seeded=(sample,))


@given(_clean_inputs())
def test_clean_payload_passes_recorded_payload_scan(value: LLMInput) -> None:
    assert scan_payload({"kind": value.kind, "fields": dict(value.fields)}) == ()
