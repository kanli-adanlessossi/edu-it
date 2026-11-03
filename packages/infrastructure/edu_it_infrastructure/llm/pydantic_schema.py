"""Pydantic adapter for the domain ``OutputSchema`` protocol (ai-content Req 1.2).

The caller supplies the model class, so infrastructure never imports
``edu-it-content``.
"""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import BaseModel, ValidationError

__all__ = ["PydanticOutputSchema"]


class PydanticOutputSchema:
    """Validates raw model JSON text against a Pydantic model."""

    def __init__(self, model: type[BaseModel]) -> None:
        self._model = model

    def validate(self, text: str) -> Mapping[str, object]:
        """Return the validated mapping, or raise ``ValueError`` if invalid.

        The mapping is the model's own dump of the parsed value (JSON mode), so
        it is exactly what validated; nothing is patched or defaulted by us.
        """
        try:
            parsed = self._model.model_validate_json(text)
        except ValidationError as exc:
            raise ValueError(
                f"output failed schema validation: {exc.error_count()} error(s)"
            ) from exc
        return parsed.model_dump(mode="json")
