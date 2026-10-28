"""Unit tests for the domain import-boundary check and tooling exit statuses.

Task 2.4 of the foundations spec. These tests prove two things required by
Requirement 1 ("Monorepo tooling for both languages"):

* Req 1.8 - the domain import-boundary check (``tools/check_domain_boundary.py``)
  FAILS with a non-zero status and names the offending file + banned import when
  ``packages/domain`` contains a web/API framework import, and PASSES (exit 0)
  when the domain is clean.
* Req 1.4 / 1.5 / 1.6 / 1.7 - the lint, typecheck, and test commands each exit
  non-zero and report the offending location on a violation / type error / test
  failure, and exit zero when clean.

Design notes
------------
Every "violation" fixture is created inside an isolated temporary directory and
the tool is pointed at it explicitly, so the broken fixtures can never be picked
up by the repo's own green ``ruff`` / ``mypy`` / ``pytest`` run. Nothing banned
is ever written into the real ``packages/domain`` tree.

The subprocess invocations use the SAME pinned tools the monorepo configures
(``ruff``, ``mypy``, ``pytest`` from the root ``pyproject.toml`` dev group, and
the stdlib ``tools/check_domain_boundary.py`` script). They are run with
isolation flags so they exercise the tools' real exit-status contract against a
single fixture rather than re-scanning the whole repository.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BOUNDARY_SCRIPT = REPO_ROOT / "tools" / "check_domain_boundary.py"


def _run(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Run a subprocess capturing text stdout/stderr, never raising on failure."""
    return subprocess.run(
        args,
        cwd=str(cwd) if cwd is not None else None,
        capture_output=True,
        text=True,
        check=False,
    )


# ---------------------------------------------------------------------------
# Req 1.8 - domain import-boundary check
# ---------------------------------------------------------------------------


def test_boundary_check_fails_on_framework_import(tmp_path: Path) -> None:
    """A web/API framework import in the domain fails the boundary check.

    The script must exit non-zero and name both the offending file and the
    banned module.
    """
    domain_dir = tmp_path / "domain"
    domain_dir.mkdir()
    offending = domain_dir / "service.py"
    offending.write_text(
        "from fastapi import FastAPI\n\napp = FastAPI()\n",
        encoding="utf-8",
    )

    result = _run([sys.executable, str(BOUNDARY_SCRIPT), str(domain_dir)])

    assert result.returncode != 0, result.stdout + result.stderr
    combined = result.stdout + result.stderr
    assert "service.py" in combined  # offending file named
    assert "fastapi" in combined  # banned module named


def test_boundary_check_names_line_and_statement(tmp_path: Path) -> None:
    """The failure output includes the offending line and the import statement."""
    domain_dir = tmp_path / "domain"
    domain_dir.mkdir()
    offending = domain_dir / "repo.py"
    # Banned import on line 2 so we can assert the reported line number.
    offending.write_text(
        "x = 1\nimport sqlalchemy\n",
        encoding="utf-8",
    )

    result = _run([sys.executable, str(BOUNDARY_SCRIPT), str(domain_dir)])

    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "repo.py:2" in combined
    assert "sqlalchemy" in combined


def test_boundary_check_passes_on_clean_domain(tmp_path: Path) -> None:
    """A clean domain (no banned imports, relative imports allowed) exits 0."""
    domain_dir = tmp_path / "domain"
    pkg = domain_dir / "edu_it_domain"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text('__version__ = "0.0.0"\n', encoding="utf-8")
    (pkg / "clock.py").write_text(
        "from __future__ import annotations\n"
        "from dataclasses import dataclass\n"
        "from . import __version__  # intra-domain relative import is fine\n\n"
        "@dataclass\n"
        "class Tick:\n"
        "    value: int\n",
        encoding="utf-8",
    )

    result = _run([sys.executable, str(BOUNDARY_SCRIPT), str(domain_dir)])

    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK" in result.stdout


def test_boundary_check_reports_missing_domain_dir(tmp_path: Path) -> None:
    """A missing domain directory exits with status 2 and a clear message."""
    missing = tmp_path / "does-not-exist"

    result = _run([sys.executable, str(BOUNDARY_SCRIPT), str(missing)])

    assert result.returncode == 2
    assert "not found" in result.stderr


def test_real_domain_tree_stays_clean() -> None:
    """Sanity guard: the real packages/domain tree still passes the check.

    This protects against a future change accidentally introducing a banned
    import into the real domain (which would break the repo-wide check).
    """
    result = _run([sys.executable, str(BOUNDARY_SCRIPT)], cwd=REPO_ROOT)

    assert result.returncode == 0, result.stdout + result.stderr


# ---------------------------------------------------------------------------
# Req 1.4 / 1.7 - lint command exit status (ruff)
# ---------------------------------------------------------------------------


def test_lint_fails_with_location_on_violation(tmp_path: Path) -> None:
    """Ruff exits non-zero and reports the file path + rule on a violation."""
    bad = tmp_path / "bad.py"
    # `import os` is unused -> F401 (pyflakes), reported with path and code.
    bad.write_text("import os\n", encoding="utf-8")

    result = _run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "--isolated",
            "--select",
            "F",
            str(bad),
        ]
    )

    assert result.returncode != 0, result.stdout + result.stderr
    combined = result.stdout + result.stderr
    assert "bad.py" in combined  # offending file path
    assert "F401" in combined  # violated rule code


def test_lint_passes_on_clean_file(tmp_path: Path) -> None:
    """Ruff exits zero on a clean file."""
    good = tmp_path / "good.py"
    good.write_text('"""Clean module."""\n\nVALUE = 1\n', encoding="utf-8")

    result = _run([sys.executable, "-m", "ruff", "check", "--isolated", str(good)])

    assert result.returncode == 0, result.stdout + result.stderr


# ---------------------------------------------------------------------------
# Req 1.5 / 1.7 - typecheck command exit status (mypy)
# ---------------------------------------------------------------------------


def test_typecheck_fails_with_location_on_type_error(tmp_path: Path) -> None:
    """Mypy exits non-zero and reports file:line on a type error."""
    bad = tmp_path / "bad_types.py"
    # Assigning a str to an int-annotated name is a type error on line 1.
    bad.write_text("x: int = 'not an int'\n", encoding="utf-8")
    cfg = tmp_path / "mypy.ini"
    cfg.write_text("[mypy]\n", encoding="utf-8")

    result = _run(
        [
            sys.executable,
            "-m",
            "mypy",
            "--no-incremental",
            "--config-file",
            str(cfg),
            "--show-column-numbers",
            str(bad),
        ]
    )

    assert result.returncode != 0, result.stdout + result.stderr
    combined = result.stdout + result.stderr
    assert "bad_types.py:1" in combined  # offending file + line
    assert "error" in combined.lower()


def test_typecheck_passes_on_clean_file(tmp_path: Path) -> None:
    """Mypy exits zero on a well-typed file."""
    good = tmp_path / "good_types.py"
    good.write_text("x: int = 1\n", encoding="utf-8")
    cfg = tmp_path / "mypy.ini"
    cfg.write_text("[mypy]\n", encoding="utf-8")

    result = _run(
        [
            sys.executable,
            "-m",
            "mypy",
            "--no-incremental",
            "--config-file",
            str(cfg),
            str(good),
        ]
    )

    assert result.returncode == 0, result.stdout + result.stderr


# ---------------------------------------------------------------------------
# Req 1.6 / 1.7 - test command exit status (pytest)
# ---------------------------------------------------------------------------


def test_test_command_fails_and_names_failing_test(tmp_path: Path) -> None:
    """Pytest exits non-zero and identifies the failing test on a failure."""
    failing = tmp_path / "test_failing_fixture.py"
    failing.write_text(
        "def test_that_fails() -> None:\n    assert 1 == 2\n",
        encoding="utf-8",
    )
    # Write an empty config so the nested pytest does not discover the repo
    # root config (which would re-scan the whole monorepo).
    (tmp_path / "empty.ini").write_text("[pytest]\n", encoding="utf-8")

    result = _run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            "-c",
            str(tmp_path / "empty.ini"),
            str(failing),
        ],
        cwd=tmp_path,
    )

    assert result.returncode != 0, result.stdout + result.stderr
    combined = result.stdout + result.stderr
    assert "test_that_fails" in combined  # failing test identified
    assert "1 failed" in combined


def test_test_command_passes_on_green_fixture(tmp_path: Path) -> None:
    """Pytest exits zero when every collected test passes."""
    passing = tmp_path / "test_passing_fixture.py"
    passing.write_text(
        "def test_that_passes() -> None:\n    assert 1 == 1\n",
        encoding="utf-8",
    )
    (tmp_path / "empty.ini").write_text("[pytest]\n", encoding="utf-8")

    result = _run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            "-c",
            str(tmp_path / "empty.ini"),
            str(passing),
        ],
        cwd=tmp_path,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout + result.stderr
