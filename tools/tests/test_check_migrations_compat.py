"""Tests for the migration backward-compatibility check (tools/check_migrations_compat.py).

**Validates: Requirements 21.6**
"""

# Claims: 21.6

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from check_migrations_compat import changed_migration_files, find_incompatible_operations, main


def _migration(upgrade_body: str, downgrade_body: str = "pass", extra: str = "") -> str:
    indented_up = "\n".join("    " + ln for ln in upgrade_body.splitlines())
    indented_down = "\n".join("    " + ln for ln in downgrade_body.splitlines())
    return (
        "import sqlalchemy as sa\nfrom alembic import op\n\n"
        f"{extra}\n"
        f"def upgrade():\n{indented_up}\n\n"
        f"def downgrade():\n{indented_down}\n"
    )


def _ops(src: str) -> list[str]:
    return [f.operation for f in find_incompatible_operations(src)]


# Claims: 21.6
def test_add_only_has_no_findings() -> None:
    src = _migration(
        "op.create_table('t', sa.Column('id', sa.Integer()))\n"
        "op.add_column('t', sa.Column('x', sa.Integer()))\n"
        "op.create_index('ix', 't', ['x'])\n"
        "op.alter_column('t', 'x', nullable=True)"
    )
    assert find_incompatible_operations(src) == []


# Claims: 21.6
@pytest.mark.parametrize(
    ("body", "op_name"),
    [
        ("op.drop_column('t', 'x')", "drop_column"),
        ("op.drop_table('t')", "drop_table"),
        ("op.rename_table('a', 'b')", "rename_table"),
        ("op.alter_column('t', 'x', new_column_name='y')", "alter_column"),
        ("with op.batch_alter_table('t') as batch:\n    batch.drop_column('x')", "drop_column"),
    ],
)
def test_drop_and_rename_are_reported(body: str, op_name: str) -> None:
    assert _ops(_migration(body)) == [op_name]


# Claims: 21.6
@pytest.mark.parametrize(
    "sql",
    ["DROP TABLE t", "alter table t drop column x", "ALTER TABLE t RENAME COLUMN a TO b"],
)
def test_raw_sql_drop_or_rename_is_reported(sql: str) -> None:
    assert _ops(_migration(f"op.execute({sql!r})")) == ["execute"]
    assert _ops(_migration(f"op.execute(sa.text({sql!r}))")) == ["execute"]


# Claims: 21.6
def test_raw_sql_add_only_is_not_reported() -> None:
    assert _ops(_migration("op.execute('UPDATE t SET x = 1')")) == []


# Claims: 21.6
def test_downgrade_is_ignored() -> None:
    src = _migration("op.add_column('t', sa.Column('x', sa.Integer()))", "op.drop_column('t', 'x')")
    assert find_incompatible_operations(src) == []


# Claims: 21.6
def test_drop_in_helper_called_by_upgrade_is_reported() -> None:
    extra = "def _cleanup():\n    op.drop_table('old')\n"
    assert _ops(_migration("_cleanup()", extra=extra)) == ["drop_table"]


# Claims: 21.6
def test_drop_in_uncalled_helper_is_ignored() -> None:
    extra = "def _unused():\n    op.drop_table('old')\n"
    assert _ops(_migration("op.create_table('t')", extra=extra)) == []


# Claims: 21.6
def test_allow_marker_same_line_and_line_above() -> None:
    same = _migration("op.drop_column('t', 'x')  # migration-compat: allow unused since 1.4")
    above = _migration("# migration-compat: allow unused since 1.4\nop.drop_table('t')")
    assert find_incompatible_operations(same) == []
    assert find_incompatible_operations(above) == []


# Claims: 21.6
def test_marker_only_covers_its_own_operation() -> None:
    src = _migration(
        "op.drop_column('t', 'x')  # migration-compat: allow ok\n"
        "op.add_column('t', sa.Column('z', sa.Integer()))\n"
        "op.drop_table('u')"
    )
    assert _ops(src) == ["drop_table"]


# Claims: 21.6
def test_findings_are_sorted_with_line_numbers() -> None:
    src = _migration("op.drop_table('a')\nop.drop_column('t', 'x')")
    findings = find_incompatible_operations(src)
    assert [f.line for f in findings] == sorted(f.line for f in findings)
    assert len(findings) == 2
    assert "drops a table" in findings[0].render()


# Claims: 21.6
def test_cli_exit_0_for_add_only(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    f = tmp_path / "0001_ok.py"
    f.write_text(_migration("op.create_table('t')"), encoding="utf-8")
    assert main([str(f)]) == 0
    assert "OK" in capsys.readouterr().out


# Claims: 21.6
def test_cli_exit_1_for_violation(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    f = tmp_path / "0002_bad.py"
    f.write_text(_migration("op.drop_column('t', 'x')"), encoding="utf-8")
    assert main([str(f)]) == 1
    err = capsys.readouterr().err
    assert "FAIL" in err and "0002_bad.py" in err


# Claims: 21.6
def test_cli_exit_2_for_missing_file_and_syntax_error(tmp_path: Path) -> None:
    assert main([str(tmp_path / "missing.py")]) == 2
    broken = tmp_path / "broken.py"
    broken.write_text("def upgrade(:\n", encoding="utf-8")
    assert main([str(broken)]) == 2


# Claims: 21.6
def test_cli_exit_2_for_bad_git_base(tmp_path: Path) -> None:
    subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], check=True, capture_output=True)
    assert main(["--base", "no-such-ref", "--repo", str(tmp_path)]) == 2


# Claims: 21.6
def test_cli_requires_files_or_base() -> None:
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 2


# Claims: 21.6
def test_base_mode_checks_only_changed_migrations(tmp_path: Path) -> None:
    versions = "packages/infrastructure/edu_it_infrastructure/persistence/migrations/versions"

    def git(*args: str) -> None:
        subprocess.run(
            [
                "git", "-C", str(tmp_path), "-c", "user.name=t", "-c", "user.email=t@e.invalid",
                "-c", "commit.gpgsign=false", *args,
            ],
            check=True,
            capture_output=True,
        )

    def write(name: str, text: str) -> None:
        p = tmp_path / versions / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8", newline="\n")

    git("init", "-q", "-b", "main")
    write("0001_old.py", _migration("op.drop_table('legacy')"))
    git("add", "-A")
    git("commit", "-q", "-m", "base")
    git("checkout", "-q", "-b", "feature")
    write("0002_new.py", _migration("op.create_table('t')"))
    git("add", "-A")
    git("commit", "-q", "-m", "add")

    assert changed_migration_files(tmp_path, "main") == [tmp_path / versions / "0002_new.py"]
    assert main(["--base", "main", "--repo", str(tmp_path)]) == 0

    write("0003_bad.py", _migration("op.rename_table('a', 'b')"))
    git("add", "-A")
    git("commit", "-q", "-m", "bad")
    assert main(["--base", "main", "--repo", str(tmp_path)]) == 1
