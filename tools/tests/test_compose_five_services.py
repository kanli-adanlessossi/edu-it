"""Unit test for the five-service Compose definition (task 10.2, Req 3.1, 20.4).

Requirement 3.1 fixes the local Compose stack at EXACTLY five services — web,
api, worker, PostgreSQL, Redis — no more, no fewer. Requirement 20.4 excludes
Kubernetes from this phase: ``deploy/compose/`` holds only the local Docker
Compose definition and no Kubernetes artifact is produced. This test asserts
both, which are concrete structural facts (not universal properties), so they
are checked here as examples rather than via Hypothesis.

Parsing note: the repo pins no YAML library in the Python dev toolchain (the
only ``pyyaml`` on disk is a transitive dependency of pre-commit), and the
existing tooling tests deliberately use the standard library only. So this test
extracts the top-level ``services:`` block's keys with a small, indentation-aware
standard-library scan rather than importing a YAML parser — enough to assert the
exact service set without adding a pinned dependency.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_DIR = REPO_ROOT / "deploy" / "compose"
COMPOSE_FILE = COMPOSE_DIR / "docker-compose.yml"

# The exact five services Req 3.1 mandates.
EXPECTED_SERVICES = {"web", "api", "worker", "postgres", "redis"}

# File extensions / directory names that would indicate a Kubernetes artifact
# (Req 20.4). Helm charts, raw manifests, and kustomize bases all live under one
# of these, so their absence under deploy/ is what "no Kubernetes artifact"
# means for this phase.
_K8S_DIR_NAMES = {"helm", "charts", "chart", "manifests", "k8s", "kubernetes", "kustomize"}
_K8S_FILE_NAMES = {"chart.yaml", "values.yaml", "kustomization.yaml", "kustomization.yml"}


def _top_level_service_keys(compose_text: str) -> set[str]:
    """Return the keys of the top-level ``services:`` mapping.

    A small indentation-aware scan: find the top-level ``services:`` line
    (no leading whitespace), then collect every key indented exactly one level
    under it, stopping at the next top-level key. This reads the service names a
    Compose file declares without a YAML parser, which is all this test needs.
    """
    lines = compose_text.splitlines()
    services: set[str] = set()
    in_services = False
    service_indent: int | None = None

    for raw in lines:
        # Ignore blank lines and comments for structure detection.
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue

        indent = len(raw) - len(raw.lstrip(" "))

        if not in_services:
            if indent == 0 and stripped.rstrip() == "services:":
                in_services = True
            continue

        # Inside the services block: a return to column 0 ends it.
        if indent == 0:
            break

        # The first indented key under services: sets the service-key indent;
        # every subsequent key at that same indent is another service name.
        if service_indent is None:
            service_indent = indent
        if indent == service_indent and stripped.endswith(":"):
            services.add(stripped[:-1].strip())

    return services


def test_compose_file_exists() -> None:
    """The local Compose definition exists at the expected path."""
    assert COMPOSE_FILE.is_file(), f"missing {COMPOSE_FILE}"


def test_compose_defines_exactly_the_five_services() -> None:
    """The Compose file declares exactly web, api, worker, postgres, redis (Req 3.1)."""
    services = _top_level_service_keys(COMPOSE_FILE.read_text(encoding="utf-8"))
    assert services == EXPECTED_SERVICES, (
        "compose services must be exactly "
        f"{sorted(EXPECTED_SERVICES)}; got {sorted(services)} "
        f"(extra={sorted(services - EXPECTED_SERVICES)}, "
        f"missing={sorted(EXPECTED_SERVICES - services)})"
    )


def test_deploy_contains_no_kubernetes_artifact() -> None:
    """deploy/ holds only the local Compose definition, no Kubernetes artifact (Req 20.4)."""
    deploy_dir = REPO_ROOT / "deploy"

    offending_dirs = [
        path
        for path in deploy_dir.rglob("*")
        if path.is_dir() and path.name.lower() in _K8S_DIR_NAMES
    ]
    assert not offending_dirs, f"unexpected Kubernetes directories under deploy/: {offending_dirs}"

    offending_files = [
        path
        for path in deploy_dir.rglob("*")
        if path.is_file() and path.name.lower() in _K8S_FILE_NAMES
    ]
    assert not offending_files, f"unexpected Kubernetes manifests under deploy/: {offending_files}"
