"""Tests for code-fence tolerance in PydanticOutputSchema (ai-content Req 1.2).

**Validates: Requirements 1.2**
"""

from __future__ import annotations

import pytest
from edu_it_infrastructure.llm.pydantic_schema import PydanticOutputSchema
from pydantic import BaseModel


class Out(BaseModel):
    title: str


SCHEMA = PydanticOutputSchema(Out)
BODY = '{"title": "x"}'
EXPECTED = {"title": "x"}


@pytest.mark.parametrize(
    "text",
    [
        BODY,
        f"```json\n{BODY}\n```",
        f"```JSON\n{BODY}\n```",
        f"```\n{BODY}\n```",
        f"  \n```json\r\n{BODY}\r\n```\r\n  ",
    ],
)
def test_accepted_forms_return_the_unfenced_mapping(text: str) -> None:
    assert SCHEMA.validate(text) == SCHEMA.validate(BODY) == EXPECTED


@pytest.mark.parametrize(
    "text",
    [
        f"Here you go:\n```json\n{BODY}\n```",
        f"```json\n{BODY}\n```\nHope this helps.",
        f"```json\n{BODY}\n```\n```json\n{BODY}\n```",
        f"```json\n{BODY}\n",
        "```json\n{not json}\n```",
        f"```json\n```json\n{BODY}\n```\n```",
    ],
)
def test_unrepaired_forms_fail_validation(text: str) -> None:
    with pytest.raises(ValueError, match="output failed schema validation"):
        SCHEMA.validate(text)
