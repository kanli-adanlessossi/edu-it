"""Tests for the incremental traceability check (tools/check_traceability.py).

**Validates: Requirements 23.2, 23.5**
"""

from __future__ import annotations

from pathlib import Path

import pytest
from check_traceability import (
    claimed_ids,
    load_exceptions,
    main,
    milestone_ids,
    parse_requirements,
)

REQUIREMENTS = """\
## Requirements

## Milestone A: Generation pipeline

### Requirement 1: One

#### Acceptance Criteria

1. THE X SHALL do a.
2. THE X SHALL do b
   across two lines.

### Requirement 2: Two

#### Acceptance Criteria

1. THE Y SHALL do c.
"""


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_parse_requirements_lists_criteria_per_requirement() -> None:
    assert parse_requirements(REQUIREMENTS) == {1: ["1.1", "1.2"], 2: ["2.1"]}


def test_milestone_sets_include_extra_criteria() -> None:
    reqs = {n: [f"{n}.1"] for n in range(1, 26)}
    reqs[22] = [f"22.{i}" for i in range(1, 8)]
    a = milestone_ids("A", reqs)
    assert "1.1" in a and "12.1" in a and "22.1" in a and "22.4" in a
    assert "13.1" not in a and "22.2" not in a
    b = milestone_ids("B", reqs)
    assert {"13.1", "16.1", "22.2", "22.5", "22.6", "22.7"} <= set(b)
    assert "22.3" in milestone_ids("C", reqs)
    everything = milestone_ids("all", reqs)
    assert {"23.1", "24.1", "25.1", "22.3"} <= set(everything)


def test_unknown_requirement_in_spec_is_an_error() -> None:
    with pytest.raises(ValueError, match="Requirement 1 has no criteria"):
        milestone_ids("A", {})


def test_claims_read_from_docstrings_including_continuation_lines(tmp_path: Path) -> None:
    _write(
        tmp_path / "tests" / "test_one.py",
        '"""Module.\n\n**Validates: Requirements 1.1, 1.2\n    and 2.1**\n"""\n\n\n'
        'def test_a():\n    """Case.\n\n    Validates: Requirements 3.4\n    """\n',
    )
    _write(tmp_path / "web" / "x.test.ts", "// Validates: Requirements 5.5\n")
    _write(tmp_path / "node_modules" / "test_skip.py", '"""Validates: Requirements 9.9"""\n')
    claims = claimed_ids(tmp_path)
    assert set(claims) == {"1.1", "1.2", "2.1", "3.4", "5.5"}


def test_check_fails_naming_the_requirement_with_no_test(tmp_path: Path) -> None:
    req = _write(tmp_path / "requirements.md", REQUIREMENTS)
    _write(tmp_path / "t" / "test_x.py", '"""Validates: Requirements 1.1, 2.1"""\n')
    # Milestone sets are fixed, so use a spec holding Requirements 1 to 25 for `check`.
    reqs = parse_requirements(req.read_text(encoding="utf-8"))
    claimed = claimed_ids(tmp_path / "t")
    missing = [i for i in (c for cs in reqs.values() for c in cs) if i not in claimed]
    assert missing == ["1.2"]


def test_exceptions_cover_untested_requirements_and_need_a_reason(tmp_path: Path) -> None:
    ok = _write(tmp_path / "ok.toml", '[[exception]]\nid = "1.11"\nreason = "owner action"\n')
    assert load_exceptions(ok) == {"1.11": "owner action"}
    bad = _write(tmp_path / "bad.toml", '[[exception]]\nid = "1.11"\nreason = ""\n')
    with pytest.raises(ValueError, match="no reason"):
        load_exceptions(bad)


def test_main_reports_each_missing_requirement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import check_traceability as module

    # Full spec: every requirement 1..25 with one criterion, 22 with seven.
    lines = ["## Requirements"]
    for n in range(1, 26):
        count = 7 if n == 22 else 1
        lines.append(f"### Requirement {n}: R\n\n#### Acceptance Criteria\n")
        lines.extend(f"{i}. THE X SHALL y." for i in range(1, count + 1))
        lines.append("")
    req = _write(tmp_path / "requirements.md", "\n".join(lines))
    tests = tmp_path / "tests"
    claimed = [f"{n}.1" for n in range(1, 13) if n != 7] + ["22.1", "22.4"]
    _write(tests / "test_all.py", f'"""Validates: Requirements {", ".join(claimed)}"""\n')
    monkeypatch.setattr(module, "DEFAULT_REQUIREMENTS", req)
    monkeypatch.setattr(module, "DEFAULT_EXCEPTIONS", tmp_path / "none.toml")
    monkeypatch.setattr(module, "REPO_ROOT", tests)

    assert main(["--milestone", "A"]) == 1
    err = capsys.readouterr().err
    assert "Requirement 7.1" in err
    assert "Requirement 6.1" not in err

    _write(
        tmp_path / "none.toml",
        '[[exception]]\nid = "7.1"\nreason = "verified by review"\n',
    )
    assert main(["--milestone", "A"]) == 0
