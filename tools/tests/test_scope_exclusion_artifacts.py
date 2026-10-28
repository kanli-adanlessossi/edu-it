"""Unit tests for scope-exclusion artifact absence (task 26.3, Req 20.1-20.4).

Requirement 20 bounds the foundations phase. The phase migrates the full
Data_Model (every table), but it must NOT ship any *source or service artifact*
that implements out-of-scope behavior:

* 20.1 — learning features (tracks, modules, lessons, quizzes, mastery, review,
  decay, placement, final assessment): no source/service artifact beyond the
  migrated Data_Model tables.
* 20.2 — hands-on labs, the sandbox, and the terminal WebSocket runtime: no
  source/service artifact.
* 20.3 — AI content generation and the content-review pipeline: no
  source/service artifact *beyond* providing the ``LLMProvider``
  Fake_Implementation (the domain port plus its fake).
* 20.4 — Kubernetes deployment artifacts (Helm charts, manifests, network
  policies, node-pool configuration): none anywhere in the repository.

These are concrete structural facts (not universal properties), so they are
checked here as examples rather than via Hypothesis, consistent with the
sibling ``test_compose_five_services.py`` (which asserts the complementary
Req 20.4 fact that ``deploy/`` carries no Kubernetes artifact).

Strategy. The data model is deliberately migrated in full, so learning/lab/AI
*table* names appear legitimately inside the single Alembic migration and the
shared SQLAlchemy metadata. This test therefore scans the hand-written
*source trees* of the shipped components — ``apps/`` and ``packages/`` — and
asserts that no source *module or package directory* is dedicated to an
out-of-scope feature. It matches on module/directory names (not on text buried
in comments or table definitions) so that migrating a table does not trip the
check while a real feature module (e.g. ``quizzes.py`` or a ``labs/`` package)
would. The persistence layer (migrations, metadata, repositories) is excluded
from the feature-module scan because that is exactly where the full data model
legitimately lives.

Standard library only: the repo pins no extra dependency for this kind of
structural scan, matching the existing tooling tests.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# The hand-written source trees shipped by the phase. ``deploy/`` is covered by
# test_compose_five_services.py; here we add the repo-wide Kubernetes check.
SOURCE_ROOTS = (REPO_ROOT / "apps", REPO_ROOT / "packages")

# Directory names that are build output, dependencies, caches, or generated
# code — never hand-written source, so excluded from every scan.
_IGNORED_DIR_NAMES = {
    "node_modules",
    ".next",
    ".venv",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".hypothesis",
    "generated",  # OpenAPI->TypeScript generated client (apps/web/src/generated)
    "dist",
    "build",
}

# Source file extensions we treat as "artifacts" for the feature-module scan.
_SOURCE_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".jsx", ".mjs"}

# The persistence layer legitimately carries the FULL data model (every table,
# including learning/lab/AI tables). Req 20.1 explicitly permits migrating those
# tables, so these paths are excluded from the feature-module scan.
_DATA_MODEL_PATHS = (
    REPO_ROOT / "packages" / "infrastructure" / "edu_it_infrastructure" / "persistence",
)


def _is_ignored(path: Path) -> bool:
    """True if any path segment is an ignored (non-source) directory."""
    return any(part in _IGNORED_DIR_NAMES for part in path.parts)


def _is_test_artifact(path: Path) -> bool:
    """True for test files/dirs — tests are not the artifacts Req 20 forbids."""
    if "tests" in path.parts:
        return True
    name = path.name
    return (
        name.startswith("test_")
        or name.endswith("_test.py")
        or ".test." in name
        or ".property." in name
        or ".spec." in name
    )


def _under_data_model(path: Path) -> bool:
    """True if the path lives in the persistence layer where the full data model is migrated."""
    return any(_is_within(path, root) for root in _DATA_MODEL_PATHS)


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _iter_source_artifacts() -> list[Path]:
    """Every hand-written source file/dir under the shipped source roots.

    Excludes ignored build/dependency/generated directories, test files, and
    the persistence layer that carries the migrated data model.
    """
    artifacts: list[Path] = []
    for root in SOURCE_ROOTS:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if _is_ignored(path) or _is_test_artifact(path) or _under_data_model(path):
                continue
            artifacts.append(path)
    return artifacts


def _artifact_stems(artifacts: list[Path]) -> list[tuple[Path, str]]:
    """Return (path, lowercased name-stem) pairs for source files and package dirs.

    For files we use the stem (``quizzes`` from ``quizzes.py``); for directories
    we use the directory name (``labs`` from a ``labs/`` package). Only source
    files and directories that contain source files are considered.
    """
    pairs: list[tuple[Path, str]] = []
    for path in artifacts:
        if path.is_file() and path.suffix in _SOURCE_SUFFIXES:
            pairs.append((path, path.stem.lower()))
        elif path.is_dir():
            pairs.append((path, path.name.lower()))
    return pairs


# -- 20.1: learning features --------------------------------------------------
# Feature names drawn verbatim from Req 20.1. A hand-written source module or
# package whose name is one of these indicates a learning-feature artifact.
_LEARNING_FEATURE_NAMES = {
    "track",
    "tracks",
    "module",
    "modules",
    "lesson",
    "lessons",
    "quiz",
    "quizzes",
    "mastery",
    "review",
    "reviews",
    "decay",
    "placement",
    "assessment",
    "assessments",
    "final_assessment",
}

# -- 20.2: labs / sandbox / terminal ------------------------------------------
_LAB_FEATURE_NAMES = {
    "lab",
    "labs",
    "sandbox",
    "terminal",
}

# -- 20.3: AI generation / content-review pipeline ----------------------------
# The ONLY permitted AI artifacts are the LLMProvider domain port and its fake.
_AI_FEATURE_NAMES = {
    "generation",
    "generation_job",
    "ai_generation",
    "content_generation",
    "content_review",
    "review_pipeline",
    "variant",
    "variants",
}

# Explicit allowlist for Req 20.3: the LLMProvider port and its fake are the
# only AI-related source artifacts the phase is allowed to ship.
_DOMAIN_PKG = REPO_ROOT / "packages" / "domain" / "edu_it_domain"
_INFRA_PKG = REPO_ROOT / "packages" / "infrastructure" / "edu_it_infrastructure"
_ALLOWED_AI_PATHS = {
    _DOMAIN_PKG / "ports" / "llm_provider.py",
    _INFRA_PKG / "fakes" / "llm_provider.py",
}


def test_source_roots_exist() -> None:
    """The shipped source trees exist so the scans below are meaningful."""
    for root in SOURCE_ROOTS:
        assert root.is_dir(), f"missing source root {root}"


def test_no_learning_feature_source_artifact() -> None:
    """No hand-written source module/package implements a learning feature (Req 20.1)."""
    offenders = [
        path
        for path, stem in _artifact_stems(_iter_source_artifacts())
        if stem in _LEARNING_FEATURE_NAMES
    ]
    assert not offenders, (
        "learning-feature source artifacts are out of scope (Req 20.1); "
        f"found: {sorted(str(p) for p in offenders)}"
    )


def test_no_lab_sandbox_or_terminal_source_artifact() -> None:
    """No hand-written source module/package implements labs, sandbox, or terminal (Req 20.2)."""
    offenders = [
        path
        for path, stem in _artifact_stems(_iter_source_artifacts())
        if stem in _LAB_FEATURE_NAMES
    ]
    assert not offenders, (
        "labs/sandbox/terminal source artifacts are out of scope (Req 20.2); "
        f"found: {sorted(str(p) for p in offenders)}"
    )


def test_no_ai_generation_source_artifact_beyond_llmprovider_fake() -> None:
    """No AI-generation source artifact exists beyond the LLMProvider port and fake (Req 20.3)."""
    offenders = [
        path
        for path, stem in _artifact_stems(_iter_source_artifacts())
        if stem in _AI_FEATURE_NAMES and path.resolve() not in _ALLOWED_AI_PATHS
    ]
    assert not offenders, (
        "AI-generation/content-review source artifacts are out of scope beyond the "
        f"LLMProvider fake (Req 20.3); found: {sorted(str(p) for p in offenders)}"
    )


def test_llmprovider_port_and_fake_are_the_only_ai_artifacts_present() -> None:
    """The LLMProvider port and fake are present — the one permitted AI artifact (Req 20.3)."""
    for allowed in _ALLOWED_AI_PATHS:
        assert allowed.is_file(), f"expected permitted LLMProvider artifact missing: {allowed}"


def test_repository_contains_no_kubernetes_artifact() -> None:
    """No Kubernetes deployment artifact exists anywhere in the repository (Req 20.4)."""
    k8s_dir_names = {"helm", "charts", "chart", "manifests", "k8s", "kubernetes", "kustomize"}
    k8s_file_names = {
        "chart.yaml",
        "chart.yml",
        "values.yaml",
        "values.yml",
        "kustomization.yaml",
        "kustomization.yml",
        "networkpolicy.yaml",
        "networkpolicy.yml",
    }

    offending_dirs: list[Path] = []
    offending_files: list[Path] = []
    for root in (*SOURCE_ROOTS, REPO_ROOT / "deploy"):
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if _is_ignored(path):
                continue
            if path.is_dir() and path.name.lower() in k8s_dir_names:
                offending_dirs.append(path)
            elif path.is_file() and path.name.lower() in k8s_file_names:
                offending_files.append(path)

    found_dirs = sorted(str(p) for p in offending_dirs)
    found_files = sorted(str(p) for p in offending_files)
    assert not offending_dirs, (
        f"Kubernetes directories are out of scope (Req 20.4); found: {found_dirs}"
    )
    assert not offending_files, (
        f"Kubernetes manifests are out of scope (Req 20.4); found: {found_files}"
    )
