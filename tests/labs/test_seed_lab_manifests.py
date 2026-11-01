"""Seed lab manifest tests (hands-on-labs task 17.5).

Requirements 21.6, 21.7, 22.3 and 22.9. These tests read the authored repository files only: no
Docker, no sync, nothing served.
"""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
TRACK = REPO_ROOT / "content" / "linux-fundamentals"
LAB1 = "lab.fix-the-shared-folder"
LAB2 = "lab.world-writable-entries"
SEED_LABS = (LAB1, LAB2)
BUNDLE2 = TRACK / "lab-bundles" / "world-writable-entries"

#: One Check per Requirement 21.6 criterion, mapped to the Check name.
LAB1_CHECKS = {
    "sub-folders exist": "dirs-exist",
    "group devs and setgid": "group-setgid",
    "no world-writable entry remains": "no-world-writable",
    "planted file intact": "planted-intact",
}

#: Commands covered by the permissions lessons (reading and changing permissions) and the quiz
#: questions attached to them: ``ls``, ``chmod``, ``chown``, ``chgrp``, ``umask``.
PERMISSION_LESSON_COMMANDS = {"ls", "chmod", "chown", "chgrp", "umask"}
#: Unavoidable exceptions, each with its reason. The lesson bodies (MDX, authored per locale outside
#: the catalog) do not teach these, and no lesson command can replace them:
#: * ``mkdir``: Requirement 21.2 makes the learner create the folders ``docs`` and ``scripts``;
#:   only a folder-creating command can do that.
#: Hints never name ``find``, ``id`` or ``cd``: ``ls -l`` finds the world-writable file (21.4),
#: ``chgrp devs`` needs no ``id``, and every path in the hints is absolute.
UNAVOIDABLE_COMMANDS = {"mkdir"}
#: Commands a hint must never suggest (they are neither lesson commands nor unavoidable).
FORBIDDEN_IN_HINTS = ("find", "id", "cd", "sudo", "su", "setfacl", "chattr", "rm", "touch", "cat")

#: Case-id prefix -> category of Requirement 22.3.
CATEGORIES = {
    "file": "regular files",
    "dir": "directories",
    "symlink": "symlinks",
    "sticky": "sticky-bit entries",
    "total": "total header",
    "space": "names with spaces",
    "empty": "empty input",
}
#: Categories where a reported case cannot exist by the rule (a symlink is never reported; empty
#: input has no entry at all).
NEVER_REPORTED = {"symlink", "empty"}


def _load(name: str) -> Any:
    return json.loads((TRACK / name).read_text(encoding="utf-8"))


def _labs() -> dict[str, dict[str, Any]]:
    return {lab["provenance"]["stable_id"]: lab for lab in _load("labs.json")}


def _checks_of(lab_id: str) -> list[dict[str, Any]]:
    task_ids = {t["stable_id"] for t in _load("lab_tasks.json") if t["lab_stable_id"] == lab_id}
    return [c for c in _load("lab_checks.json") if c["lab_task_stable_id"] in task_ids]


def _b64(value: str) -> str:
    return base64.b64decode(value).decode("utf-8")


def _cases() -> dict[str, str]:
    """case_id -> expected stdout, across both Check expectation files of lab 2."""
    out: dict[str, str] = {}
    for path in sorted((BUNDLE2 / "expected").glob("*.json")):
        for case_id, data in json.loads(path.read_text(encoding="utf-8"))["cases"].items():
            out[case_id] = _b64(data["stdout_b64"])
    return out


def _inputs() -> dict[str, str]:
    out: dict[str, str] = {}
    for path in sorted((BUNDLE2 / "sandbox" / "cases").glob("*.json")):
        for case in json.loads(path.read_text(encoding="utf-8"))["cases"]:
            out[case["case_id"]] = _b64(case["stdin_b64"])
    return out


def _category(case_id: str) -> str:
    return case_id.split("-", 1)[0]


# --- Lab 2: categories, reported and not-reported (22.3) -------------------------------------


def test_every_category_has_cases_and_each_case_has_input_and_expectation() -> None:
    expected, inputs = _cases(), _inputs()
    assert set(expected) == set(inputs)
    # ``order-kept`` is an extra ordering case, not a Requirement 22.3 category.
    assert set(CATEGORIES) <= {_category(c) for c in expected}


def test_each_category_has_reported_and_not_reported_cases() -> None:
    expected = _cases()
    for prefix, label in CATEGORIES.items():
        outs = [out for cid, out in expected.items() if _category(cid) == prefix]
        assert any(o == "" for o in outs), f"{label}: no not-reported case"
        if prefix not in NEVER_REPORTED:
            assert any(o != "" for o in outs), f"{label}: no reported case"


def test_symlinks_are_never_reported_in_any_case() -> None:
    expected, inputs = _cases(), _inputs()
    for cid, text in inputs.items():
        reported = set(expected[cid].splitlines())
        for line in text.splitlines():
            if line.startswith("l"):
                name = line.split(None, 8)[8].split(" -> ")[0]
                assert name not in reported, f"{cid}: symlink {name} reported"


def test_sticky_cases_distinguish_world_write_from_sticky_alone() -> None:
    expected, inputs = _cases(), _inputs()
    assert expected["sticky-world-writable"].split() == ["tmp"]
    assert "drwxrwxrwt" in inputs["sticky-world-writable"]
    assert expected["sticky-not-world-writable"] == ""
    assert "drwxr-xr-t" in inputs["sticky-not-world-writable"]


def test_total_header_and_space_names_are_exercised() -> None:
    inputs, expected = _inputs(), _cases()
    assert inputs["total-header-with-reported"].startswith("total ")
    assert "total" not in expected["total-header-with-reported"]
    assert any(" " in line for line in expected["space-name-reported"].splitlines())
    assert inputs["empty-input"] == ""


# --- Lab 1: one Check per requirement (21.6) -------------------------------------------------


def test_lab1_has_exactly_one_check_per_requirement() -> None:
    names = [c["stable_id"].rsplit(".", 1)[1] for c in _checks_of(LAB1)]
    assert sorted(names) == sorted(LAB1_CHECKS.values())
    assert len(names) == len(set(names)) == len(LAB1_CHECKS)


def test_lab1_checks_each_have_a_failure_outcome_with_a_distinct_key() -> None:
    for check in _checks_of(LAB1):
        codes = {o["code"] for o in check["outcomes"]}
        assert "pass" in codes
        assert len(codes - {"pass", "unsafe-path"}) >= 1, check["stable_id"]


# --- Feedback_Keys and messages (21.6, 22.9) ------------------------------------------------


def test_feedback_keys_are_distinct_across_both_seed_labs() -> None:
    keys = [o["feedback_key"] for lab in SEED_LABS for c in _checks_of(lab) for o in c["outcomes"]]
    assert keys and len(keys) == len(set(keys))


def test_lab2_outcomes_are_the_four_required_with_distinct_keys_per_check() -> None:
    for check in _checks_of(LAB2):
        codes = [o["code"] for o in check["outcomes"]]
        assert sorted(codes) == sorted(["pass", "wrong-output", "crashed", "no-output"])
        keys = [o["feedback_key"] for o in check["outcomes"]]
        assert len(keys) == len(set(keys))


def test_every_message_exists_in_french_and_english() -> None:
    for lab in SEED_LABS:
        for check in _checks_of(lab):
            for outcome in check["outcomes"]:
                assert outcome["message"]["fr"].strip() and outcome["message"]["en"].strip()
                assert outcome["message"]["fr"] != outcome["message"]["en"]


def test_no_lab2_message_contains_expected_output() -> None:
    names = {line for out in _cases().values() for line in out.splitlines() if len(line) >= 4}
    assert names
    for check in _checks_of(LAB2):
        for outcome in check["outcomes"]:
            for lang in ("fr", "en"):
                text = outcome["message"][lang]
                for name in names:
                    assert name not in text, (outcome["feedback_key"], lang, name)


def test_no_lab_message_embeds_a_mode_string_or_the_planted_content() -> None:
    planted = (TRACK / "lab-bundles" / "fix-the-shared-folder" / "setup" / "setup.sh").read_text(
        encoding="utf-8"
    )
    secret_lines = re.findall(r"'(- [^']+)'", planted)
    mode = re.compile(r"[-dl][rwx-]{8}[rwxtT-]")
    for lab in SEED_LABS:
        for check in _checks_of(lab):
            for outcome in check["outcomes"]:
                for text in outcome["message"].values():
                    assert not mode.search(text), outcome["feedback_key"]
                    assert not any(s in text for s in secret_lines), outcome["feedback_key"]


# --- Lab 1 is solvable with permissions-lesson commands only (21.7) --------------------------


def test_lab1_hint_commands_come_from_the_permissions_lessons() -> None:
    tasks = [t for t in _load("lab_tasks.json") if t["lab_stable_id"] == LAB1]
    tried = 0
    for task in tasks:
        for hint in task["hints"]:
            for lang in ("fr", "en"):
                text = hint[lang]
                match = re.search(r"(?:Try|Essayez)\s*:\s*(.+)$", text)
                if not match:
                    continue
                tried += 1
                for segment in re.split(r"\s+(?:then|puis)\s+", match.group(1)):
                    command = segment.split()[0]
                    allowed = PERMISSION_LESSON_COMMANDS | UNAVOIDABLE_COMMANDS
                    assert command in allowed, (task["stable_id"], command)
    assert tried >= 3 * 2


def test_lab1_hints_never_mention_commands_outside_the_lessons() -> None:
    """Every word of every hint (not only the "Try:" line) avoids non-lesson commands."""
    for task in (t for t in _load("lab_tasks.json") if t["lab_stable_id"] == LAB1):
        for hint in task["hints"]:
            for lang in ("fr", "en"):
                words = set(re.findall(r"[A-Za-z][A-Za-z-]*", hint[lang]))
                # "id" and "su" are also ordinary words in English/French prose; match command
                # use only when the word starts a "Try:" segment or follows a pipe or "&&".
                for word in FORBIDDEN_IN_HINTS:
                    if word in {"id", "su", "cat"}:
                        assert not re.search(
                            rf"(?:^|[:|&;]\s*|puis\s+|then\s+){word}\s", hint[lang]
                        )
                    else:
                        assert word not in words, (task["stable_id"], lang, word)


def test_lab1_description_and_instructions_name_no_command_outside_the_lessons() -> None:
    lab = _labs()[LAB1]
    for lang in ("fr", "en"):
        for word in ("sudo", "su ", "setfacl", "chattr", "chmod 777", "visudo"):
            assert word not in lab["instructions"][lang]


# --- pending-review.json (20.13) ------------------------------------------------------------


def test_both_seed_labs_are_reviewed_and_no_longer_pending() -> None:
    # Task 19: after approval the list is emptied and each lab records its reviewer.
    assert _load("pending-review.json")["stable_ids"] == []
    for lab_id in SEED_LABS:
        assert lab_id in _labs()
        assert _labs()[lab_id]["provenance"]["reviewed_by"]
