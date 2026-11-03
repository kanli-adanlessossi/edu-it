"""Property 22: lab content validation reports each defect (task 14.3).

**Validates: Requirements 18.4, 20.1, 20.2, 20.3, 20.4, 20.5, 20.6, 20.7, 20.8, 20.9,
20.13, 23.6, 23.8**

Drives the real ``edu-it content validate`` CLI over on-disk fixture content: one
defect per rule must be reported naming the rule and the offending ``stable_id``
and exit non-zero; the all-valid fixture exits zero; ``--allow-pending`` drops only
``ai-reviewer-present`` for listed labs.
"""

from __future__ import annotations

import json
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from edu_it_content.cli import main
from hypothesis import given, settings
from hypothesis import strategies as st

Items = dict[str, list[dict[str, Any]]]


def _loc(text: str = "x") -> dict[str, str]:
    return {"fr": f"{text}-fr", "en": f"{text}-en"}


def _prov(stable_id: str, *, ai_unreviewed: bool = False) -> dict[str, Any]:
    prov: dict[str, Any] = {
        "stable_id": stable_id,
        "content_version": "cv_1",
        "origin": "ai" if ai_unreviewed else "human",
    }
    if not ai_unreviewed:
        prov["reviewed_by"] = "usr_r"
        prov["reviewed_at"] = "2026-01-01T00:00:00+00:00"
        prov["reviewed_at"] = "2026-01-01T00:00:00+00:00"
    return prov


def _check(stable_id: str, timeout: int, prefix: str) -> dict[str, Any]:
    def outcome(code: str) -> dict[str, Any]:
        key = f"{prefix}.{code}"
        return {
            "code": code,
            "exit_code": 0 if code == "pass" else 1,
            "feedback_key": key,
            "message": _loc(key),
        }

    return {
        "stable_id": stable_id,
        "lab_task_stable_id": "lt_1",
        "timeout_seconds": timeout,
        "script": "check.sh",
        "outcomes": [outcome("pass"), outcome("fail")],
    }


def _items() -> Items:
    return {
        "track": [{"provenance": _prov("tr_1"), "title": _loc(), "status": "published"}],
        "module": [
            {
                "provenance": _prov("md_1"),
                "track_stable_id": "tr_1",
                "ordinal": 0,
                "title": _loc(),
            }
        ],
        "lesson": [
            {
                "provenance": _prov("ls_1"),
                "module_stable_id": "md_1",
                "ordinal": 0,
                "title": _loc(),
            }
        ],
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
        "lab_check": [_check("lc_1", 4, "a"), _check("lc_2", 4, "b")],
    }


@contextmanager
def _content(items: Items, pending: list[str] | None = None) -> Iterator[Path]:
    """Write a repo layout and yield its content root."""
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        (repo / "lab-images" / "linux-base").mkdir(parents=True)
        (repo / "labs" / "bundle").mkdir(parents=True)
        (repo / "labs" / "setup.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        root = repo / "content"
        (root / "linux-fundamentals").mkdir(parents=True)
        (root / "items.json").write_text(json.dumps(items), encoding="utf-8")
        if pending is not None:
            (root / "linux-fundamentals" / "pending-review.json").write_text(
                json.dumps({"stable_ids": pending}), encoding="utf-8"
            )
        yield root


def _validate(
    root: Path, capsys: pytest.CaptureFixture[str], *, allow_pending: bool = False
) -> tuple[int, str]:
    capsys.readouterr()
    argv = ["validate", "--content-root", str(root)]
    if allow_pending:
        argv.append("--allow-pending")
    code = main(argv)
    return code, capsys.readouterr().err


def _rules_in(stderr: str) -> set[str]:
    return {
        line[8 : line.index("]")] for line in stderr.splitlines() if line.startswith("error: [")
    }


# --- one mutation per rule: (mutate, rule(s) accepted, offending stable_id) -------------


def _m_reference(items: Items, n: int) -> None:
    items["lab"][0]["image"] = f"missing{n}"


def _m_timeout_missing(items: Items, n: int) -> None:
    del items["lab_check"][0]["timeout_seconds"]


def _m_single_over(items: Items, n: int) -> None:
    items["lab_check"] = [_check("lc_1", 13 + n, "a")]


def _m_sum_over(items: Items, n: int) -> None:
    first = 7 + n % 5  # 7..11
    items["lab_check"] = [_check("lc_1", first, "a"), _check("lc_2", 13 - first + 1, "b")]


def _m_feedback_missing(items: Items, n: int) -> None:
    del items["lab_check"][0]["outcomes"][1]["feedback_key"]


def _m_feedback_duplicate(items: Items, n: int) -> None:
    items["lab_check"][1]["outcomes"][0]["feedback_key"] = "a.pass"


def _m_bundle_location(items: Items, n: int) -> None:
    root = ["/workspace", "/home", "/tmp", "/var/tmp", "/dev/shm"][n % 5]
    items["lab"][0]["check_bundle_dest"] = f"{root}/b{n}"


def _m_bilingual(items: Items, n: int) -> None:
    items["lab"][0]["instructions"] = {"fr": "seulement"}


def _m_reviewer(items: Items, n: int) -> None:
    items["lab"][0]["provenance"] = _prov("lb_1", ai_unreviewed=True)


_CASES: dict[str, tuple[Callable[[Items, int], None], set[str], str, bool]] = {
    # name: (mutation, accepted rules, offending id, rules must be exactly the accepted set)
    "lab-reference": (_m_reference, {"lab-reference"}, "lb_1", True),
    "lab-check-timeout": (_m_timeout_missing, {"lab-check-timeout"}, "lc_1", False),
    "budget-single": (_m_single_over, {"check-timeouts-fit-budget"}, "lc_1", True),
    "budget-sum": (_m_sum_over, {"check-timeouts-fit-budget"}, "lb_1", True),
    "feedback-missing": (_m_feedback_missing, {"lab-feedback-complete"}, "lc_1", False),
    "feedback-duplicate": (_m_feedback_duplicate, {"lab-feedback-complete"}, "lb_1", True),
    "bundle-location": (_m_bundle_location, {"lab-bundle-location"}, "lb_1", True),
    "bilingual": (_m_bilingual, {"schema-validation", "bilingual-completeness"}, "lb_1", False),
    "ai-reviewer-present": (_m_reviewer, {"ai-reviewer-present"}, "lb_1", True),
}


def test_all_valid_lab_content_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with _content(_items()) as root:
        code, err = _validate(root, capsys)
    assert code == 0, err


@settings(max_examples=40, deadline=None)
@given(case=st.sampled_from(sorted(_CASES)), n=st.integers(min_value=0, max_value=20))
def test_each_defect_is_reported_with_rule_and_stable_id(case: str, n: int) -> None:
    mutate, accepted, stable_id, exact = _CASES[case]
    items = _items()
    mutate(items, n)
    capsys_free = _run_cli_capture(items)
    code, err = capsys_free
    assert code == 1
    reported = _rules_in(err)
    assert reported & accepted
    if exact:
        assert reported == accepted
    assert any(
        line.startswith("error: [")
        and f"] {stable_id}:" in line
        and line[8 : line.index("]")] in accepted
        for line in err.splitlines()
    )


def _run_cli_capture(items: Items, **kwargs: Any) -> tuple[int, str]:
    """Run the CLI in-process capturing stderr without the pytest fixture (for hypothesis)."""
    import contextlib
    import io

    buffer = io.StringIO()
    with _content(items, kwargs.get("pending")) as root, contextlib.redirect_stderr(buffer):
        argv = ["validate", "--content-root", str(root)]
        if kwargs.get("allow_pending"):
            argv.append("--allow-pending")
        code = main(argv)
    return code, buffer.getvalue()


# --- strict / --allow-pending / post-review ---------------------------------------------


def _unreviewed() -> Items:
    items = _items()
    _m_reviewer(items, 0)
    return items


def test_listed_lab_missing_only_reviewer_strict_reports_exactly_reviewer() -> None:
    code, err = _run_cli_capture(_unreviewed(), pending=["lb_1"])
    assert code == 1
    assert _rules_in(err) == {"ai-reviewer-present"}
    assert "lb_1" in err


def test_listed_lab_missing_only_reviewer_passes_allow_pending() -> None:
    code, err = _run_cli_capture(_unreviewed(), pending=["lb_1"], allow_pending=True)
    assert code == 0, err


def test_listed_lab_with_other_failure_still_fails_allow_pending() -> None:
    items = _unreviewed()
    _m_reference(items, 0)
    code, err = _run_cli_capture(items, pending=["lb_1"], allow_pending=True)
    assert code == 1
    assert _rules_in(err) == {"lab-reference"}


def test_unlisted_unreviewed_lab_fails_allow_pending() -> None:
    code, err = _run_cli_capture(_unreviewed(), pending=[], allow_pending=True)
    assert code == 1
    assert _rules_in(err) == {"ai-reviewer-present"}


def test_post_review_state_validates_strictly_with_empty_list() -> None:
    items = _items()
    items["lab"][0]["provenance"] = {
        "stable_id": "lb_1",
        "content_version": "cv_1",
        "origin": "ai",
        "reviewed_by": "usr_expert",
        "reviewed_at": "2026-01-01T00:00:00+00:00",
    }
    code, err = _run_cli_capture(items, pending=[])
    assert code == 0, err
