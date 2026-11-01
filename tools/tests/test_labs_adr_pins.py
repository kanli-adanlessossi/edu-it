"""ADR 0001 vs manifests for dependencies introduced by hands-on-labs (task 20.4).

Requirements 4.13, 4.14: every dependency this spec introduces must have an exact version pinned
in ``docs/adr/0001-versions.md``, and the test suite fails when a manifest or CI workflow carries
one at a different version or when it has no pin.

Dependencies absent from a manifest are fine (they may be added by a later task); the check only
applies to those present. Standard library only, like the other tooling tests.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
ADR = REPO_ROOT / "docs" / "adr" / "0001-versions.md"
WORKFLOWS = REPO_ROOT / ".github" / "workflows"

# Dependencies introduced by this spec, by ecosystem.
NPM_DEPS = ["@xterm/xterm", "@xterm/addon-fit", "monaco-editor", "@monaco-editor/loader"]
PY_DEPS = ["websockets", "uvicorn", "docker"]
TOOL_DEPS = ["trivy", "aquasecurity/trivy-action"]
ALL_DEPS = NPM_DEPS + PY_DEPS + TOOL_DEPS

EXACT_VERSION = re.compile(r"^\d+(\.\d+)*([.-]?[A-Za-z0-9]+)*$")
HANDS_ON_HEADING = "### Hands-on labs"


def parse_adr_pins(text: str) -> dict[str, str]:
    """Return {dependency: version} from the hands-on-labs section's table."""
    start = text.find(HANDS_ON_HEADING)
    if start < 0:
        return {}
    section = text[start:]
    end = section.find("\n## ")
    if end >= 0:
        section = section[:end]
    pins: dict[str, str] = {}
    for line in section.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2 or not line.lstrip().startswith("|"):
            continue
        name = cells[0].strip("`")
        version = cells[1].strip("`")
        if re.match(r"^\d", version):
            pins[name] = version
    return pins


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def check_package_json(data: dict[str, Any], pins: dict[str, str]) -> list[str]:
    problems: list[str] = []
    for section in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
        for name, spec in (data.get(section) or {}).items():
            if name in NPM_DEPS and pins.get(name) != spec:
                problems.append(
                    f"package.json {section}: {name}={spec!r}, ADR pin={pins.get(name)!r}"
                )
    return problems


def _py_requirements(data: dict[str, Any]) -> list[str]:
    reqs = list(data.get("project", {}).get("dependencies", []))
    for extra in data.get("project", {}).get("optional-dependencies", {}).values():
        reqs += extra
    for group in data.get("dependency-groups", {}).values():
        reqs += [r for r in group if isinstance(r, str)]
    return reqs


def check_pyproject(data: dict[str, Any], pins: dict[str, str]) -> list[str]:
    problems: list[str] = []
    wanted = {_norm(d): d for d in PY_DEPS}
    for req in _py_requirements(data):
        m = re.match(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(\[[^\]]*\])?\s*(.*)$", req)
        if not m or _norm(m.group(1)) not in wanted:
            continue
        name = wanted[_norm(m.group(1))]
        spec = m.group(3).split(";")[0].strip()
        pin = pins.get(name)
        if pin is None or spec != f"=={pin}":
            problems.append(f"python manifest: {req!r}, ADR pin={pin!r}")
    return problems


def check_workflow(text: str, name: str, pins: dict[str, str]) -> list[str]:
    problems: list[str] = []
    trivy = pins.get("trivy")
    action = pins.get("aquasecurity/trivy-action")
    if "aquasecurity/trivy-action" in text:
        for m in re.finditer(r"^\s*version:\s*['\"]?v?([\w.\-]+)", text, re.M):
            if m.group(1) != trivy:
                problems.append(f"{name}: trivy version input {m.group(1)!r}, ADR pin={trivy!r}")
        if action is None or f"trivy-action v{action}" not in text:
            problems.append(f"{name}: trivy-action tag comment does not match ADR pin {action!r}")
        for m in re.finditer(r"trivy-action@(\S+)", text):
            ref = m.group(1)
            if not re.fullmatch(r"[0-9a-f]{40}", ref) and ref.lstrip("v") != action:
                problems.append(
                    f"{name}: trivy-action ref {ref!r} is neither the pinned tag nor a SHA"
                )
    for m in re.finditer(r"\btrivy[ @=-]v?(\d+\.\d+\.\d+)", text, re.I):
        if m.group(1) != trivy:
            problems.append(f"{name}: trivy {m.group(1)!r}, ADR pin={trivy!r}")
    return problems


# --- tests -----------------------------------------------------------------------------------


def _pins() -> dict[str, str]:
    return parse_adr_pins(ADR.read_text(encoding="utf-8"))


def test_every_labs_dependency_has_an_exact_pin_in_adr() -> None:
    pins = _pins()
    missing = [d for d in ALL_DEPS if d not in pins]
    assert not missing, f"no pin in ADR 0001 for: {missing}"
    inexact = {d: pins[d] for d in ALL_DEPS if not EXACT_VERSION.match(pins[d])}
    assert not inexact, f"non-exact pins in ADR 0001: {inexact}"


def test_package_json_matches_adr() -> None:
    pins = _pins()
    problems: list[str] = []
    for path in (REPO_ROOT / "apps").glob("*/package.json"):
        problems += check_package_json(json.loads(path.read_text(encoding="utf-8")), pins)
    assert not problems, problems


def test_python_manifests_match_adr() -> None:
    pins = _pins()
    problems: list[str] = []
    paths = [REPO_ROOT / "pyproject.toml"]
    paths += (REPO_ROOT / "apps").glob("*/pyproject.toml")
    paths += (REPO_ROOT / "packages").glob("*/pyproject.toml")
    for path in paths:
        problems += check_pyproject(tomllib.loads(path.read_text(encoding="utf-8")), pins)
    assert not problems, problems


def test_ci_workflows_match_adr() -> None:
    pins = _pins()
    problems: list[str] = []
    for path in sorted(WORKFLOWS.glob("*.y*ml")):
        problems += check_workflow(path.read_text(encoding="utf-8"), path.name, pins)
    assert not problems, problems


def test_checkers_detect_unpinned_and_mismatched_versions() -> None:
    pins = {"@xterm/xterm": "6.0.0", "docker": "7.2.0", "trivy": "0.76.1"}
    assert check_package_json({"dependencies": {"@xterm/xterm": "^6.0.0"}}, pins)
    assert check_package_json({"dependencies": {"monaco-editor": "0.57.0"}}, pins)  # no pin
    assert not check_package_json({"dependencies": {"@xterm/xterm": "6.0.0", "react": "^1"}}, pins)
    assert not check_package_json({}, pins)  # absent deps are fine
    assert check_pyproject({"project": {"dependencies": ["docker>=7"]}}, pins)
    assert check_pyproject({"project": {"dependencies": ["websockets==17.3"]}}, pins)  # no pin
    assert not check_pyproject({"project": {"dependencies": ["docker==7.2.0"]}}, pins)
    assert check_workflow("run: install trivy 0.1.0", "w.yml", pins)
    assert not check_workflow("name: x", "w.yml", pins)
