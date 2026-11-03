"""Pydantic adapter for the domain ``OutputSchema`` protocol (ai-content Req 1.2).

The caller supplies the model class, so infrastructure never imports
``edu-it-content``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from pydantic import BaseModel, ValidationError

__all__ = ["PydanticOutputSchema"]

# The whole reply is exactly one fenced block: optional whitespace, an opening
# line of three backticks with an optional ``json`` tag, a body, and a closing
# line of three backticks.
_FENCED = re.compile(
    r"\A\s*```(?:json)?[ \t]*\r?\n(?P<body>.*?)\r?\n[ \t]*```\s*\Z",
    re.DOTALL | re.IGNORECASE,
)


def _strip_single_fence(text: str) -> str:
    """Remove one surrounding code fence; anything else is returned unchanged."""
    match = _FENCED.match(text)
    if match is None or "```" in match.group("body"):
        return text
    return match.group("body")


class PydanticOutputSchema:
    """Validates raw model JSON text against a Pydantic model."""

    def __init__(self, model: type[BaseModel]) -> None:
        self._model = model

    def validate(self, text: str) -> Mapping[str, object]:
        """Return the validated mapping, or raise ``ValueError`` if invalid.

        If the whole reply is exactly one markdown code fence (optionally tagged
        ``json``), only that transport wrapper is removed first. Prose around a
        fence, several or nested fences and unclosed fences are not repaired and
        fail validation. The mapping is the model's own dump of the parsed value
        (JSON mode), so it is exactly what validated; nothing is patched or
        defaulted by us.
        """
        try:
            parsed = self._model.model_validate_json(_strip_single_fence(text))
        except ValidationError as exc:
            raise ValueError(
                f"output failed schema validation: {exc.error_count()} error(s)"
            ) from exc
        return parsed.model_dump(mode="json")
