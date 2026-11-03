"""ai-content traceability suite (ai-content task 26.1; Requirements 23.1, 23.2, 23.5).

``REQUIREMENT_MAP`` maps every requirement (1 to 25) of ``.kiro/specs/ai-content/requirements.md``
to ``(test file, needle)`` pairs. The test file must exist and contain the needle, so a renamed or
deleted test breaks the mapping. The needle ``CLAIM`` means the file also carries a
``Validates: Requirements N.x`` line for that requirement, read with the same parser as
``tools/check_traceability.py``. This is the complete version of the incremental checks run at
the milestone checkpoints (tasks 11, 18, 25).

A ``Validates:`` line carries no spec name, so ``tools/check_traceability.py`` also counts claims
of other specs. This map names ai-content tests only.

Requirements 24 and 25 describe the Live_Run (task 28) and the Phase_4_Report. Their
offline-testable criteria are mapped here. The criteria that only a live run or the final report
can show are listed in ``PENDING_LIVE_RUN`` and are not claimed yet; the ``ai-traceability`` check
of ``tools/run_verification.py --suite ai-content`` keeps failing until tasks 28 add them.

**Validates: Requirements 23.1, 23.2, 23.5**
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))
import check_traceability  # noqa: E402
import run_verification  # noqa: E402

REQUIREMENTS = REPO / ".kiro" / "specs" / "ai-content" / "requirements.md"
EXCEPTIONS = REPO / "tools" / "traceability_exceptions.toml"

CLAIM = "Validates:"
_T = "def test_"

_INFRA = "packages/infrastructure/tests"
_DOM = "packages/domain/tests"
_APP = "packages/application/tests"
_CONTENT = "packages/content/tests"
_WORKER = "apps/worker/tests"
_API = "apps/api/tests"
_WEB = "apps/web/src"
_TOOLS = "tools/tests"

#: Criteria that only the Live_Run (task 28) or the final Phase_4_Report can prove.
PENDING_LIVE_RUN = frozenset({"24.4", "24.7", "24.12"})

REQUIREMENT_MAP: dict[int, list[tuple[str, str]]] = {
    1: [
        (f"{_INFRA}/test_llm_engine.py", CLAIM),
        (f"{_INFRA}/test_llm_provider_selection.py", CLAIM),
        (f"{_INFRA}/test_llm_credentials.py", CLAIM),
        (f"{_INFRA}/test_llm_model_id_startup.py", CLAIM),
        (f"{_INFRA}/test_generation_ledger_contract.py", CLAIM),
    ],
    2: [
        (f"{_INFRA}/test_llm_replay.py", CLAIM),
        (f"{_INFRA}/test_llm_live.py", CLAIM),
        (f"{_INFRA}/test_fake_llm_provider.py", _T),
        (f"{_TOOLS}/test_no_model_network_guard.py", _T),
    ],
    3: [
        (f"{_DOM}/test_ai_privacy_property.py", CLAIM),
        (f"{_DOM}/test_ai_pseudonym_property.py", CLAIM),
        (f"{_DOM}/test_ai_inputs_privacy_fixtures.py", _T),
        (f"{_INFRA}/test_llm_replay.py", CLAIM),
    ],
    4: [
        (f"{_WORKER}/test_prompt_files_property.py", CLAIM),
        (f"{_TOOLS}/test_check_prompt_immutability.py", CLAIM),
        (f"{_WORKER}/test_prompt_store.py", _T),
        (f"{_WORKER}/test_prompt_renderer.py", _T),
    ],
    5: [
        (f"{_WORKER}/test_generator.py", CLAIM),
        (f"{_WORKER}/test_generator_stage_gate_property.py", CLAIM),
        (f"{_WORKER}/test_generator_git_stages.py", CLAIM),
        (f"{_WORKER}/test_content_writer.py", CLAIM),
        (f"{_WORKER}/test_generated_items_property.py", CLAIM),
    ],
    6: [
        (f"{_CONTENT}/test_validate_review_rule_properties.py", CLAIM),
        (f"{_CONTENT}/test_validate_review_rule.py", _T),
        (f"{_CONTENT}/test_validate_existing_content_strict.py", CLAIM),
    ],
    7: [
        (f"{_APP}/test_verify_item.py", CLAIM),
        (f"{_APP}/test_verify_item_blind_answerer_property.py", CLAIM),
        (f"{_APP}/test_verify_item_immutability_property.py", CLAIM),
        (f"{_DOM}/test_ai_coverage_property.py", CLAIM),
        (f"{_DOM}/test_ai_duplicate_property.py", CLAIM),
    ],
    8: [
        (f"{_APP}/test_verify_lab.py", CLAIM),
        (f"{_APP}/test_verify_lab_matrix_property.py", CLAIM),
        (f"{_APP}/test_verify_lab_cleanup_property.py", CLAIM),
    ],
    9: [
        (f"{_WORKER}/test_generation_report.py", CLAIM),
        (f"{_WORKER}/test_generation_report_property.py", CLAIM),
    ],
    10: [
        (f"{_DOM}/test_ai_budget_property.py", CLAIM),
        (f"{_DOM}/test_ai_budget_generation_checks.py", _T),
        (f"{_INFRA}/test_llm_engine.py", CLAIM),
        (f"{_WORKER}/test_generator.py", CLAIM),
    ],
    11: [
        (f"{_WORKER}/test_eval_runner.py", CLAIM),
        (f"{_WORKER}/test_eval_report_property.py", CLAIM),
    ],
    12: [
        (f"{_WORKER}/test_interrupted_runs_property.py", CLAIM),
        (f"{_WORKER}/test_generator.py", CLAIM),
        (f"{_INFRA}/test_generation_ledger_contract.py", CLAIM),
        (f"{_INFRA}/test_property_attempt_recording.py", CLAIM),
    ],
    13: [
        (f"{_APP}/test_refill_variant_pool.py", CLAIM),
        (f"{_APP}/test_variant_states_property.py", CLAIM),
        (f"{_WORKER}/test_ai_refill_property.py", CLAIM),
        (f"{_WORKER}/test_ai_worker_refill.py", _T),
        (f"{_WORKER}/test_ai_worker_refill_failure.py", _T),
    ],
    14: [
        (f"{_APP}/test_servable_catalog.py", CLAIM),
        (f"{_API}/test_milestone_b_invariant_api.py", CLAIM),
        (f"{_WORKER}/test_milestone_b_invariant.py", CLAIM),
        (f"{_API}/test_errors_ai_no_servable_question.py", CLAIM),
    ],
    15: [
        (f"{_APP}/test_review_task_actions.py", CLAIM),
        (f"{_APP}/test_review_task_authorization_property.py", CLAIM),
        (f"{_APP}/test_review_task_pagination_property.py", CLAIM),
        (f"{_API}/test_review_tasks_routes.py", CLAIM),
        (f"{_WEB}/components/reviewer.test.tsx", CLAIM),
    ],
    16: [
        (f"{_APP}/test_review_task_edit_property.py", CLAIM),
        (f"{_APP}/test_review_task_actions.py", CLAIM),
    ],
    17: [
        (f"{_APP}/test_path_advisor.py", CLAIM),
        (f"{_APP}/test_path_advisor_properties.py", CLAIM),
        (f"{_APP}/test_advisor_input.py", CLAIM),
        (f"{_DOM}/test_ai_ordering_property.py", CLAIM),
        (f"{_API}/test_path_recommendation_routes.py", CLAIM),
        (f"{_WEB}/components/path-recommendation-card.test.tsx", CLAIM),
    ],
    18: [
        (f"{_APP}/test_quality_loop.py", CLAIM),
        (f"{_APP}/test_flagging_property.py", CLAIM),
        (f"{_DOM}/test_ai_report_threshold_property.py", CLAIM),
        (f"{_API}/test_problem_reports_routes.py", CLAIM),
        (f"{_WEB}/components/report-problem.test.tsx", CLAIM),
    ],
    19: [
        (f"{_DOM}/test_ai_quality_property.py", CLAIM),
        (f"{_APP}/test_quality_loop.py", CLAIM),
        (f"{_INFRA}/test_item_observation_recording.py", CLAIM),
        (f"{_WORKER}/test_ai_worker_quality.py", CLAIM),
    ],
    20: [
        (f"{_DOM}/test_property_pool_fallback.py", CLAIM),
        (f"{_API}/test_milestone_c_invariant_api.py", CLAIM),
        (f"{_WORKER}/test_milestone_c_invariant.py", CLAIM),
        (f"{_API}/test_errors_ai_no_servable_question.py", CLAIM),
    ],
    21: [
        (f"{_INFRA}/test_gdpr_ai_content_end_to_end.py", CLAIM),
        (f"{_INFRA}/test_gdpr_ai_content_property.py", _T),
        (f"{_INFRA}/test_gdpr_problem_report_coverage.py", CLAIM),
        (f"{_INFRA}/test_gdpr_advisor_recommendation_coverage.py", CLAIM),
        (f"{_INFRA}/test_gdpr_item_observation_coverage.py", CLAIM),
        (f"{_WORKER}/test_ai_worker_retention.py", CLAIM),
    ],
    22: [
        (f"{_WORKER}/test_milestone_a_invariant.py", CLAIM),
        (f"{_WORKER}/test_milestone_b_invariant.py", CLAIM),
        (f"{_WORKER}/test_milestone_c_invariant.py", CLAIM),
        (f"{_API}/test_milestone_b_invariant_api.py", CLAIM),
        (f"{_API}/test_milestone_c_invariant_api.py", CLAIM),
    ],
    23: [
        (f"{_TOOLS}/test_check_traceability.py", CLAIM),
        (f"{_INFRA}/test_migration_ai_content.py", CLAIM),
        (f"{_TOOLS}/test_ai_content_verification_suite.py", CLAIM),
        (f"{_TOOLS}/test_package_dependency_graph.py", _T),
    ],
    24: [
        (f"{_WORKER}/test_interrupted_runs_property.py", CLAIM),
        (f"{_WORKER}/test_generation_report_property.py", CLAIM),
        (f"{_WORKER}/test_generated_items_property.py", CLAIM),
        (f"{_TOOLS}/test_llm_smoke.py", _T),
    ],
    25: [
        (f"{_TOOLS}/test_phase_report_writer.py", _T),
        (f"{_TOOLS}/test_phase4_report_property.py", CLAIM),
        (f"{_WORKER}/test_eval_report_property.py", "Feature: ai-content"),
    ],
}


def _requirement_numbers() -> list[int]:
    parsed = check_traceability.parse_requirements(REQUIREMENTS.read_text(encoding="utf-8"))
    return sorted(parsed)


def _claims_of(path: Path) -> set[str]:
    if path.suffix == ".py":
        return check_traceability._python_claims(path)
    return check_traceability._ids_from_text(path.read_text(encoding="utf-8"))


def _pairs() -> list[tuple[int, str, str]]:
    return [(n, f, needle) for n, entries in REQUIREMENT_MAP.items() for f, needle in entries]


def test_every_requirement_is_mapped_to_at_least_one_test() -> None:
    numbers = _requirement_numbers()
    assert numbers == list(range(1, 26))
    unmapped = [n for n in numbers if not REQUIREMENT_MAP.get(n)]
    assert unmapped == [], f"requirements without a mapped test: {unmapped}"
    stale = sorted(set(REQUIREMENT_MAP) - set(numbers))
    assert stale == [], f"mapping entries for requirements the spec lacks: {stale}"


@pytest.mark.parametrize(("number", "test_file", "needle"), _pairs())
def test_mapped_test_exists_and_contains_its_needle(
    number: int, test_file: str, needle: str
) -> None:
    path = REPO / test_file
    assert path.is_file(), f"Requirement {number}: mapped test file is missing: {test_file}"
    text = path.read_text(encoding="utf-8")
    assert needle in text, f"Requirement {number}: {test_file} does not contain {needle!r}"
    if needle == CLAIM:
        claimed = {i for i in _claims_of(path) if i.split(".")[0] == str(number)}
        assert claimed, f"{test_file} has no 'Validates: Requirements {number}.x' claim"


def test_repository_traceability_gap_is_limited_to_the_pending_live_run() -> None:
    missing = set(check_traceability.check("all", requirements_path=REQUIREMENTS))
    assert missing <= PENDING_LIVE_RUN, f"unclaimed criteria outside the Live_Run: {missing}"


def test_exceptions_have_reasons_and_name_real_criteria() -> None:
    parsed = check_traceability.parse_requirements(REQUIREMENTS.read_text(encoding="utf-8"))
    known = {c for criteria in parsed.values() for c in criteria}
    excepted = check_traceability.load_exceptions(EXCEPTIONS)
    assert set(excepted) <= known
    assert all(reason.strip() for reason in excepted.values())


def test_ai_content_suite_runs_every_check_of_requirement_23() -> None:
    checks = {c.check_id: c for c in run_verification.build_ai_content_checks()}
    text = {cid: " ".join(c.command or []) for cid, c in checks.items()}
    assert "ruff check" in text["ai-lint"]
    assert "mypy" in text["ai-typecheck"]
    assert "not propert" in text["ai-unit"]
    assert "-k propert" in text["ai-property"]
    assert "validate" in text["ai-content-validate"]
    assert "eval_entry" in text["ai-eval-replay"]
    assert checks["ai-eval-replay"].env == {"EDU_IT_ENV": "test"}
    assert "check_prompt_immutability.py" in text["ai-prompt-immutability"]
    assert "check_traceability.py --milestone all" in text["ai-traceability"]


def test_ai_content_suite_is_selectable_and_prompt_base_is_configurable() -> None:
    checks = run_verification.build_ai_content_checks(prompt_base="origin/develop")
    immutability = next(c for c in checks if c.check_id == "ai-prompt-immutability")
    assert immutability.command is not None
    assert immutability.command[-1] == "origin/develop"
    ids = [c.check_id for c in checks]
    assert len(ids) == len(set(ids))
