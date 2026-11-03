"""Startup refusal of ``global.`` model ids in ``CompositionSettings`` (ADR 0007).

**Validates: Requirements 1.10, 1.11**
"""

from __future__ import annotations

import pytest
from edu_it_infrastructure.composition import CompositionSettings
from pydantic import ValidationError


@pytest.mark.parametrize("var", ["LLM_MODEL_GENERATION", "LLM_MODEL_VERIFIER"])
@pytest.mark.parametrize("model", ["global.anthropic.claude-x-v1:0", "GLOBAL.anthropic.claude-x"])
def test_global_profile_is_refused_naming_the_setting(var: str, model: str) -> None:
    with pytest.raises(ValidationError) as exc:
        CompositionSettings.from_env({var: model})
    assert var in str(exc.value)


@pytest.mark.parametrize("model", ["eu.anthropic.claude-x-v1:0", "anthropic.claude-x-v1:0"])
def test_eu_profile_and_single_region_id_are_accepted(model: str) -> None:
    settings = CompositionSettings.from_env(
        {"LLM_MODEL_GENERATION": model, "LLM_MODEL_VERIFIER": model}
    )
    assert settings.llm_model_generation == model
    assert settings.llm_model_verifier == model


def test_unset_and_empty_models_are_accepted() -> None:
    settings = CompositionSettings.from_env({"LLM_MODEL_VERIFIER": ""})
    assert settings.llm_model_generation is None
    assert settings.llm_model_verifier is None
