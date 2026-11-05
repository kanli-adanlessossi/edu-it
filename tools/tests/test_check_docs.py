"""Basic tests for the documents check (tools/check_docs.py).

**Validates: Requirements 17.6, 17.8, 18.2, 18.5**
"""

# Claims: 17.6, 17.8, 18.2, 18.5

from __future__ import annotations

from pathlib import Path

import pytest
from check_docs import (
    GO_LIVE_CHECKLIST_ITEMS,
    find_broken_links,
    find_go_live_blocking_gaps,
    find_missing_checklist_items,
    french_section_word_count,
    main,
)


def _checklist(skip: str | None = None) -> str:
    items = [i for i in GO_LIVE_CHECKLIST_ITEMS if i != skip]
    return (
        "## Install\n\nSteps.\n\n## Go-live checklist\n\n"
        "Go-live is blocked until each item passes.\n\n"
        + "\n".join(f"- [ ] **Verify {i}.** Pass: `cmd`. Fix: [Install](#install)." for i in items)
        + "\n"
    )


# Claims: 17.8
def test_complete_checklist_has_no_missing_items() -> None:
    assert find_missing_checklist_items(_checklist()) == []


# Claims: 17.8
def test_missing_checklist_item_is_reported() -> None:
    assert find_missing_checklist_items(_checklist(skip="tls")) == ["tls"]


# Claims: 17.8
def test_item_without_checkbox_does_not_count() -> None:
    text = _checklist(skip="tls") + "\nTLS is important.\n"
    assert find_missing_checklist_items(text) == ["tls"]


# Claims: 18.5
def test_broken_link_reported_and_valid_or_external_ignored(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "ok.md").write_text("x", encoding="utf-8")
    text = (
        "[ok](docs/ok.md) [anchor](docs/ok.md#sec) [ext](https://example.com/x)\n"
        "[page](#top) [bad](docs/missing.md)\n"
    )
    findings = find_broken_links(text, tmp_path, tmp_path)
    assert [(f.line, f.target) for f in findings] == [(2, "docs/missing.md")]


# Claims: 18.2
def test_french_section_word_count() -> None:
    readme = "# T\n\nEnglish words here.\n\n## Français\n\n" + "mot " * 5 + "\n\n## Next\n\nmore\n"
    # heading excluded; five words in the section
    assert french_section_word_count(readme) == 5
    assert french_section_word_count("# T\n\nNo french.\n") is None


def _write_repo(root: Path, french_words: int, checklist: str, link: str) -> None:
    (root / "docs").mkdir()
    (root / "docs" / "DEPLOYMENT.md").write_text(checklist, encoding="utf-8")
    (root / "README.md").write_text(
        f"# R\n\n[guide]({link})\n\n## Français\n\n" + "mot " * french_words + "\n",
        encoding="utf-8",
    )


# Claims: 17.8, 18.2, 18.5
def test_main_ok(tmp_path: Path) -> None:
    _write_repo(tmp_path, 300, _checklist(), "docs/DEPLOYMENT.md")
    assert main(["--repo", str(tmp_path)]) == 0


# Claims: 18.2
def test_main_french_over_limit_fails(tmp_path: Path) -> None:
    _write_repo(tmp_path, 301, _checklist(), "docs/DEPLOYMENT.md")
    assert main(["--repo", str(tmp_path)]) == 1


# Claims: 17.8, 18.5
def test_main_broken_link_and_missing_item_fail(tmp_path: Path) -> None:
    _write_repo(tmp_path, 10, _checklist(skip="rbac review"), "docs/NOPE.md")
    assert main(["--repo", str(tmp_path)]) == 1


# Claims: 18.5
def test_main_missing_file_is_usage_error(tmp_path: Path) -> None:
    assert main(["--repo", str(tmp_path)]) == 2


# Claims: 18.5
def test_main_reports_broken_link_with_file_line_and_target(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_repo(tmp_path, 10, _checklist(), "docs/NOPE.md")
    assert main(["--repo", str(tmp_path)]) == 1
    err = capsys.readouterr().err
    assert "README.md: line 3: link target 'docs/NOPE.md' does not resolve" in err
    assert "missing Go_Live_Checklist" not in err
    assert "French section has" not in err


# Claims: 17.8
def test_main_reports_missing_checklist_item_by_name(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_repo(tmp_path, 10, _checklist(skip="websocket timeouts"), "docs/DEPLOYMENT.md")
    assert main(["--repo", str(tmp_path)]) == 1
    err = capsys.readouterr().err
    assert "missing Go_Live_Checklist item(s) for: websocket timeouts" in err
    assert "does not resolve" not in err


# Claims: 18.2
def test_french_section_boundary_300_ok_301_over() -> None:
    def readme(n: int) -> str:
        return "# R\n\n## Français\n\n" + "mot " * n + "\n"

    assert french_section_word_count(readme(300)) == 300
    assert french_section_word_count(readme(301)) == 301


# Claims: 18.2
def test_main_reports_french_section_over_300_words(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_repo(tmp_path, 301, _checklist(), "docs/DEPLOYMENT.md")
    assert main(["--repo", str(tmp_path)]) == 1
    err = capsys.readouterr().err
    assert "French section has 301 words, exceeds the 300-word limit" in err
    assert "does not resolve" not in err


# Claims: 17.10
def test_complete_checklist_states_blocking_and_names_a_fix_section() -> None:
    assert find_go_live_blocking_gaps(_checklist()) == []


# Claims: 17.10
def test_missing_blocking_statement_is_reported() -> None:
    text = _checklist().replace("Go-live is blocked until each item passes.", "")
    assert find_go_live_blocking_gaps(text) == [
        "no statement that go-live is blocked until a failing item passes"
    ]


# Claims: 17.10
def test_item_without_fix_or_with_dangling_anchor_is_reported() -> None:
    text = _checklist() + "- [ ] **No fix.** Pass: `cmd`.\n- [ ] **Bad.** Fix: [X](#nowhere).\n"
    gaps = find_go_live_blocking_gaps(text)
    assert gaps == [
        "checklist item 'no fix' has no 'Fix:' corrective section",
        "checklist item 'bad' has no 'Fix:' link to a guide section",
    ]


# Claims: 17.10
def test_main_fails_when_an_item_names_no_corrective_section(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_repo(
        tmp_path, 10, _checklist() + "- [ ] **Extra.** Pass: `cmd`.\n", "docs/DEPLOYMENT.md"
    )
    assert main(["--repo", str(tmp_path)]) == 1
    assert "has no 'Fix:' corrective section" in capsys.readouterr().err


# Claims: 17.10
def test_real_deployment_guide_names_a_corrective_section_for_every_item() -> None:
    guide = Path(__file__).resolve().parents[2] / "docs" / "DEPLOYMENT.md"
    assert find_go_live_blocking_gaps(guide.read_text(encoding="utf-8")) == []
