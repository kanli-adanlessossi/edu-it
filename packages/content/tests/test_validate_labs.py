"""Lab rules of ``edu-it content validate`` (task 14.2, Req 18.4, 20.1-20.8)."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
from edu_it_content.validation import LoadedContent, ValidationFailure, validate_content


def _loc(text: str = "x") -> dict[str, str]:
    return {"fr": f"{text}-fr", "en": f"{text}-en"}


def _prov(stable_id: str) -> dict[str, Any]:
    return {
        "stable_id": stable_id,
        "content_version": "cv_1",
        "origin": "human",
        "reviewed_by": "usr_r",
    }


def _outcome(code: str, key: str) -> dict[str, Any]:
    return {
        "code": code,
        "exit_code": 0 if code == "pass" else 1,
        "feedback_key": key,
        "message": _loc(key),
    }


def _check(stable_id: str, task: str, timeout: int, prefix: str) -> dict[str, Any]:
    return {
        "stable_id": stable_id,
        "lab_task_stable_id": task,
        "timeout_seconds": timeout,
        "script": "check.sh",
        "outcomes": [_outcome("pass", f"{prefix}.pass"), _outcome("fail", f"{prefix}.fail")],
    }


def _items() -> dict[str, list[dict[str, Any]]]:
    return {
        "concept": [{"provenance": _prov("cn_1"), "title": _loc()}],
        "lab": [
            {
                "provenance": _prov("lb_1"),
                "lesson_stable_id": "ls_1",
                "title": _loc(),
                "kind": "terminal",
                "image": "linux-base",
                "concept_stable_ids": ["cn_1"],
                "instructions": _loc(),
                "setup_script": "labs/setup.sh",
                "check_bundle": "labs/bundle",
            }
        ],
        "lab_task": [
            {
                "stable_id": "lt_1",
                "lab_stable_id": "lb_1",
                "ordinal": 0,
                "title": _loc(),
                "description": _loc(),
            }
        ],
        "lab_check": [_check("lc_1", "lt_1", 4, "a"), _check("lc_2", "lt_1", 4, "b")],
    }


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "lab-images" / "linux-base").mkdir(parents=True)
    (tmp_path / "labs" / "bundle").mkdir(parents=True)
    (tmp_path / "labs" / "setup.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    return tmp_path


def _run(repo: Path, items: dict[str, list[dict[str, Any]]]) -> list[ValidationFailure]:
    failures = validate_content(
        LoadedContent(raw_items=items, root=repo / "content"), repo_root=repo
    )
    # Reference-resolution to the (absent) lesson is out of scope for these tests.
    return [f for f in failures if f.rule != "reference-resolution"]


def _rules(failures: list[ValidationFailure]) -> set[tuple[str, str]]:
    return {(f.rule, f.stable_id) for f in failures}


def test_valid_lab_passes(repo: Path) -> None:
    assert _run(repo, _items()) == []


def test_lab_reference_failures(repo: Path) -> None:
    items = _items()
    items["lab"][0].update(
        image="nope",
        concept_stable_ids=["cn_x"],
        setup_script="labs/missing.sh",
        check_bundle="labs/missing",
    )
    failures = _run(repo, items)
    assert {f.rule for f in failures} == {"lab-reference"}
    assert len(failures) == 4
    assert {f.stable_id for f in failures} == {"lb_1"}


def test_missing_timeout_reports_lab_check_timeout(repo: Path) -> None:
    items = _items()
    del items["lab_check"][0]["timeout_seconds"]
    assert ("lab-check-timeout", "lc_1") in _rules(_run(repo, items))


def test_per_check_timeout_above_budget_names_check(repo: Path) -> None:
    items = _items()
    items["lab_check"] = [_check("lc_1", "lt_1", 13, "a")]
    assert ("check-timeouts-fit-budget", "lc_1") in _rules(_run(repo, items))


def test_sum_above_budget_names_lab(repo: Path) -> None:
    items = _items()
    items["lab_check"] = [_check("lc_1", "lt_1", 7, "a"), _check("lc_2", "lt_1", 6, "b")]
    assert _rules(_run(repo, items)) == {("check-timeouts-fit-budget", "lb_1")}


def test_sum_equal_to_budget_passes(repo: Path) -> None:
    items = _items()
    items["lab_check"] = [_check("lc_1", "lt_1", 6, "a"), _check("lc_2", "lt_1", 6, "b")]
    assert _run(repo, items) == []


def test_missing_feedback_key_reports_check(repo: Path) -> None:
    items = _items()
    del items["lab_check"][0]["outcomes"][1]["feedback_key"]
    assert ("lab-feedback-complete", "lc_1") in _rules(_run(repo, items))


def test_duplicate_feedback_key_within_lab_names_lab(repo: Path) -> None:
    items = _items()
    items["lab_check"][1]["outcomes"][0]["feedback_key"] = "a.pass"
    assert _rules(_run(repo, items)) == {("lab-feedback-complete", "lb_1")}


@pytest.mark.parametrize(
    "dest",
    [
        "/workspace",
        "/workspace/bundle",
        "/home/learner",
        "/tmp/x",
        "/var/tmp/x",
        "/dev/shm/x",
        "/tmp/../tmp/y",
    ],
)
def test_bundle_in_learner_writable_location_fails(repo: Path, dest: str) -> None:
    items = copy.deepcopy(_items())
    items["lab"][0]["check_bundle_dest"] = dest
    assert _rules(_run(repo, items)) == {("lab-bundle-location", "lb_1")}


def test_bundle_dest_default_and_lookalike_pass(repo: Path) -> None:
    items = _items()
    items["lab"][0]["check_bundle_dest"] = "/workspaces-grader"
    assert _run(repo, items) == []
