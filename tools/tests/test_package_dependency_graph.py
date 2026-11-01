"""Package dependency-graph check (api-route-wiring Requirements 16.1, 16.4, 16.7).

Parses every workspace ``pyproject.toml`` and asserts the layering
``api -> application -> domain <- infrastructure`` holds at the manifest level:

* the EDU-IT dependency graph is acyclic;
* ``edu-it-domain`` and ``edu-it-content`` list no ``edu-it-*`` dependency;
* ``edu-it-application`` and ``edu-it-infrastructure`` list only ``edu-it-domain``;
* no ``packages/*`` project depends on an ``apps/*`` project;
* every third-party dependency of ``edu-it-content`` is pinned in
  ``docs/adr/0001-versions.md`` at the same exact version.

``tools/check_domain_boundary.py`` (domain imports only) is run alongside.
"""

from __future__ import annotations

import re
import subprocess
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ADR_PATH = REPO_ROOT / "docs" / "adr" / "0001-versions.md"
BOUNDARY_SCRIPT = REPO_ROOT / "tools" / "check_domain_boundary.py"

_NAME_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[([^\]]*)\])?\s*(.*)$")


def _workspace_manifests() -> dict[str, tuple[Path, dict]]:
    """Map project name -> (manifest path, parsed TOML) for every workspace member."""
    root = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    members = root["tool"]["uv"]["workspace"]["members"]
    result: dict[str, tuple[Path, dict]] = {}
    for member in members:
        path = REPO_ROOT / member / "pyproject.toml"
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        result[data["project"]["name"]] = (path, data)
    return result


def _requirements(data: dict) -> list[str]:
    """Runtime plus optional-extra requirement strings of one project."""
    project = data["project"]
    reqs = list(project.get("dependencies", []))
    for extra in project.get("optional-dependencies", {}).values():
        reqs.extend(extra)
    return reqs


def _split(requirement: str) -> tuple[str, str]:
    """Return (normalized name, specifier remainder) of a requirement string."""
    match = _NAME_RE.match(requirement)
    assert match, f"unparseable requirement: {requirement!r}"
    return match.group(1).lower().replace("_", "-"), match.group(3).strip()


def _edu_it_deps(data: dict) -> set[str]:
    names = {_split(r)[0] for r in _requirements(data)}
    return {n for n in names if n.startswith("edu-it-")}


def _adr_pins() -> dict[str, set[str]]:
    """Package name -> versions listed in the first column pairs of ADR table rows."""
    pins: dict[str, set[str]] = {}
    for line in ADR_PATH.read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if not line.lstrip().startswith("|") or len(cells) < 2:
            continue
        names = re.findall(r"`([^`]+)`", cells[0])
        version = cells[1].strip("` ")
        for name in names:
            pins.setdefault(name.lower().replace("_", "-"), set()).add(version)
    return pins


def test_edu_it_dependency_graph_is_acyclic() -> None:
    manifests = _workspace_manifests()
    graph = {name: _edu_it_deps(data) & manifests.keys() for name, (_, data) in manifests.items()}
    # Kahn's algorithm: repeatedly remove nodes with no remaining dependencies.
    remaining = {n: set(d) for n, d in graph.items()}
    while True:
        ready = [n for n, deps in remaining.items() if not deps]
        if not ready:
            break
        for n in ready:
            del remaining[n]
        for deps in remaining.values():
            deps.difference_update(ready)
    assert not remaining, f"dependency cycle among: {sorted(remaining)}"


def test_domain_and_content_list_no_edu_it_dependency() -> None:
    manifests = _workspace_manifests()
    for name in ("edu-it-domain", "edu-it-content"):
        assert _edu_it_deps(manifests[name][1]) == set(), name


def test_application_and_infrastructure_depend_only_on_domain() -> None:
    manifests = _workspace_manifests()
    for name in ("edu-it-application", "edu-it-infrastructure"):
        assert _edu_it_deps(manifests[name][1]) <= {"edu-it-domain"}, name


def test_packages_never_depend_on_apps() -> None:
    manifests = _workspace_manifests()
    app_names = {n for n, (p, _) in manifests.items() if p.parent.parent.name == "apps"}
    assert app_names, "expected at least one apps/* project"
    for name, (path, data) in manifests.items():
        if path.parent.parent.name == "packages":
            offending = _edu_it_deps(data) & app_names
            assert not offending, f"{name} depends on app(s) {sorted(offending)}"


def test_content_third_party_dependencies_match_adr_pins() -> None:
    _, data = _workspace_manifests()["edu-it-content"]
    pins = _adr_pins()
    third_party = [r for r in _requirements(data) if not _split(r)[0].startswith("edu-it-")]
    assert third_party, "edu-it-content is expected to have third-party dependencies"
    for requirement in third_party:
        name, spec = _split(requirement)
        assert spec.startswith("=="), f"{requirement!r} is not pinned exactly"
        version = spec[2:].strip()
        assert name in pins, f"{name} is not listed in docs/adr/0001-versions.md"
        assert version in pins[name], f"{name}=={version} differs from ADR pin {pins[name]}"


def test_domain_boundary_check_passes() -> None:
    completed = subprocess.run(
        [sys.executable, str(BOUNDARY_SCRIPT)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
