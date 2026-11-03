"""Milestone A invariant proof (ai-content task 10.1).

The invariant: learners only ever see content an expert approved. After Milestone A the
Generator can only produce unreviewed AI items, and nothing it writes can be served.

- A Generation_Run for every Stage on the fakes sets no ``reviewed_by`` and leaves no item
  in a servable state.
- ``content validate`` (strict, as the CLI and CI run it) rejects every generated AI item.
- No test in the suite reaches a live model: the network guard is active and trips.

**Validates: Requirements 6.2, 22.1, 22.4**
"""

from __future__ import annotations

import importlib.util
import socket
import sys
import tempfile
from pathlib import Path
from types import ModuleType

import pytest
from edu_it_content.validation import load_content, validate_content
from edu_it_domain.ports.llm_provider import PromptRef

_HELPERS_PATH = Path(__file__).with_name("test_generated_items_property.py")


def _load_helpers() -> ModuleType:
    """Reuse the Generator wiring of the Property 10 test (tests are imported by path)."""
    name = "_milestone_a_generation_helpers"
    cached = sys.modules.get(name)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(name, _HELPERS_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_every_stage_run_yields_only_unreviewed_unservable_items_rejected_by_validate() -> None:
    """A run of every Stage on the fakes leaves no reviewed or servable item.

    **Validates: Requirements 6.2, 22.1**
    """
    helpers = _load_helpers()
    refs: list[PromptRef] = []
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        helpers._init_repo(repo)
        helpers._generate_all(repo, helpers._canned("alpha", 2), refs)

        items = list(helpers._items(repo))
        assert items, "the runs wrote no item"
        # every Stage contributed: the track, lessons, quizzes, labs and the project
        kinds = {i["provenance"]["stable_id"].split(".", 1)[0] for i in items}
        assert {"lesson", "quiz", "lab", "project"} <= kinds

        for item in items:
            prov = item["provenance"]
            assert prov["origin"] == "ai"
            assert not prov.get("reviewed_by")
            assert not prov.get("reviewed_at")
            assert not helpers._is_approved(prov), f"{prov['stable_id']} is servable"

        content = load_content(repo / "content")
        # structure is fine, which is what the Generator itself checks
        assert validate_content(content, repo_root=repo, include_review_rule=False) == []

        # strict validation, as the CLI and CI run it, rejects every AI item
        findings = validate_content(content, repo_root=repo)
        assert {f.rule for f in findings} == {"ai-reviewer-present"}
        rejected_text = "\n".join(str(f) for f in findings)
        for item in items:
            assert item["provenance"]["stable_id"] in rejected_text


@pytest.mark.network_guard_expected
def test_network_guard_is_active_so_no_test_can_call_a_live_model() -> None:
    """The suite runs under the no-model-network guard, which blocks non-loopback connects.

    **Validates: Requirements 22.4**
    """
    import pytest_no_model_network as guard

    assert socket.socket.connect is guard._guarded_connect
    assert socket.socket.connect_ex is guard._guarded_connect_ex
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        with pytest.raises(guard.NetworkBlockedError):
            sock.connect(("203.0.113.10", 443))  # TEST-NET-3, never routable


def test_pytest_is_configured_with_the_network_guard_plugin() -> None:
    """The root pytest configuration enables the guard for every test.

    **Validates: Requirements 22.4**
    """
    pyproject = Path(__file__).resolve().parents[3] / "pyproject.toml"
    assert "-p pytest_no_model_network" in pyproject.read_text(encoding="utf-8")
