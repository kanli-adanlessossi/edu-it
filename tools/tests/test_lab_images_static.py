"""Static and built-image tests for the Lab_Images and the lab-images workflow.

hands-on-labs task 3.3, Requirements 4.2, 4.3, 4.4, 4.5, 4.6, 4.7, 4.8, 4.10, 4.11.

Static tests read Dockerfiles, the bake file, and workflows. Tests marked ``docker`` inspect the
locally built images (``docker buildx bake --load`` in ``lab-images/``) and skip when the daemon
or the images are missing.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
LAB_IMAGES = ROOT / "lab-images"
WORKFLOWS = ROOT / ".github" / "workflows"
LAB_WORKFLOW = WORKFLOWS / "lab-images.yml"
DOCKERFILES = sorted(LAB_IMAGES.glob("*/Dockerfile"))
IMAGES = ("edu-it/lab-linux-base:dev", "edu-it/lab-python:dev")

FROM_RE = re.compile(r"^\s*FROM\s+(?:--\S+\s+)*(\S+)(?:\s+AS\s+(\S+))?\s*$", re.I | re.M)
DIGEST_RE = re.compile(r"^[^@\s]+@sha256:[0-9a-f]{64}$")


# --------------------------------------------------------------------------------------------
# Dockerfiles
# --------------------------------------------------------------------------------------------
def _bake_named_contexts() -> set[str]:
    text = (LAB_IMAGES / "docker-bake.hcl").read_text(encoding="utf-8")
    block = re.search(r"contexts\s*=\s*\{(.*?)\}", text, re.S)
    if not block:
        return set()
    return set(re.findall(r"^\s*([\w.-]+)\s*=", block.group(1), re.M))


def test_both_lab_images_exist() -> None:
    assert {p.parent.name for p in DOCKERFILES} == {"linux-base", "python"}


@pytest.mark.parametrize("dockerfile", DOCKERFILES, ids=lambda p: p.parent.name)
def test_every_external_from_is_digest_pinned(dockerfile: Path) -> None:
    """Req 4.2: external FROM references carry an immutable digest."""
    text = dockerfile.read_text(encoding="utf-8")
    froms = FROM_RE.findall(text)
    assert froms, "no FROM found"
    stages = {alias for _, alias in froms if alias}
    internal = _bake_named_contexts() | stages | {"scratch"}
    for ref, _alias in froms:
        if ref in internal:
            continue
        assert DIGEST_RE.match(ref), f"{dockerfile}: FROM {ref} is not digest pinned"


def test_python_image_builds_on_a_bake_named_context() -> None:
    """The python image's only non-digest FROM must be a declared bake context."""
    refs = [r for r, _ in FROM_RE.findall((LAB_IMAGES / "python" / "Dockerfile").read_text("utf-8"))]
    assert refs == ["linux-base"]
    assert "linux-base" in _bake_named_contexts()


@pytest.mark.parametrize("dockerfile", DOCKERFILES, ids=lambda p: p.parent.name)
def test_default_user_is_non_root(dockerfile: Path) -> None:
    """Req 4.3: the last USER instruction is a non-root user."""
    users = re.findall(r"^\s*USER\s+(\S+)\s*$", dockerfile.read_text("utf-8"), re.M)
    assert users, "no USER instruction"
    assert users[-1] not in {"root", "0", "0:0", "root:root"}
    assert users[-1] == "learner"


@pytest.mark.parametrize("dockerfile", DOCKERFILES, ids=lambda p: p.parent.name)
def test_dockerfile_copies_no_secrets_bundles_or_solutions(dockerfile: Path) -> None:
    """Req 4.4: nothing sensitive is added to an image."""
    text = dockerfile.read_text("utf-8")
    for line in text.splitlines():
        if re.match(r"^\s*(COPY|ADD)\b", line, re.I):
            assert not re.search(r"secret|bundle|solution|expected|\.env|\.pem|id_rsa", line, re.I), line
    assert not re.search(r"^\s*ENV\s+\S*(PASSWORD|SECRET|TOKEN|KEY)", text, re.I | re.M)


def test_lab_image_directories_contain_only_dockerfiles() -> None:
    files = [p.name for p in LAB_IMAGES.rglob("*") if p.is_file() and p.parent != LAB_IMAGES]
    assert sorted(files) == ["Dockerfile", "Dockerfile"]


# --------------------------------------------------------------------------------------------
# Workflow
# --------------------------------------------------------------------------------------------
def _load(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _triggers(workflow: dict[str, Any]) -> dict[str, Any]:
    # PyYAML parses the bare key ``on`` as boolean True.
    return workflow.get("on", workflow.get(True))


def _steps(workflow: dict[str, Any]) -> list[dict[str, Any]]:
    return [s for job in workflow["jobs"].values() for s in job["steps"]]


def test_workflow_has_build_step() -> None:
    """Req 4.5: the workflow builds the Lab_Images."""
    steps = _steps(_load(LAB_WORKFLOW))
    assert any("docker buildx bake" in s.get("run", "") for s in steps)


def test_workflow_scan_step_fails_on_any_finding() -> None:
    """Req 4.6, 4.7: scan step fails on every severity, fixed or not."""
    steps = _steps(_load(LAB_WORKFLOW))
    scans = [s for s in steps if "trivy" in s.get("uses", "").lower()]
    assert scans, "no scan step"
    for step in scans:
        w = step["with"]
        assert str(w["exit-code"]) == "1"
        assert set(str(w["severity"]).split(",")) == {"UNKNOWN", "LOW", "MEDIUM", "HIGH", "CRITICAL"}
        assert w["ignore-unfixed"] in (False, "false")
        assert "@" in step["uses"] and re.search(r"@[0-9a-f]{40}$", step["uses"])
    # Every built image is scanned.
    matrix = _load(LAB_WORKFLOW)["jobs"]["build-and-scan"]["strategy"]["matrix"]["image"]
    assert set(matrix) == set(IMAGES)


def test_workflow_triggers_are_exactly_scoped() -> None:
    """Req 4.8, 4.10: push/pull_request limited to lab-images/** plus a weekly schedule."""
    on = _triggers(_load(LAB_WORKFLOW))
    assert set(on) == {"push", "pull_request", "schedule"}
    for event in ("push", "pull_request"):
        assert on[event] == {"paths": ["lab-images/**"]}
    schedule = on["schedule"]
    assert len(schedule) == 1
    fields = schedule[0]["cron"].split()
    assert len(fields) == 5
    minute, hour, dom, month, dow = fields
    assert minute.isdigit() and hour.isdigit()
    assert dom == "*" and month == "*" and dow.isdigit()  # exactly weekly


def test_lab_images_workflow_is_not_required_by_other_workflows() -> None:
    """Req 4.10: no other workflow depends on or calls lab-images.yml."""
    for wf in WORKFLOWS.glob("*.y*ml"):
        if wf == LAB_WORKFLOW:
            continue
        text = wf.read_text("utf-8")
        assert "lab-images.yml" not in text, wf
        on = _triggers(_load(wf)) or {}
        if isinstance(on, dict) and "workflow_run" in on:
            assert "Lab images" not in on["workflow_run"].get("workflows", [])
        for job in _load(wf).get("jobs", {}).values():
            assert not str(job.get("uses", "")).endswith("lab-images.yml")


def test_lab_images_workflow_has_no_workflow_call_or_dispatch() -> None:
    on = _triggers(_load(LAB_WORKFLOW))
    assert "workflow_call" not in on and "workflow_run" not in on


# --------------------------------------------------------------------------------------------
# Waiver absence
# --------------------------------------------------------------------------------------------
SCANNER_FILE_RE = re.compile(
    r"(^|/)(\.trivyignore(\..*)?|trivy[-.\w]*\.ya?ml|\.grype\.ya?ml|grype\.ya?ml|\.snyk"
    r"|osv-scanner\.toml|\.checkov\.ya?ml|\.hadolint\.ya?ml|\.dockleignore|\.clair.*"
    r"|.*(waiver|allowlist|allow-list|vex)[\w.-]*\.(ya?ml|json|toml|txt|rego))$",
    re.I,
)
FORBIDDEN_KEYS = {
    "trivyignores", "trivy-ignores", "ignore-policy", "ignore_policy", "skip-dirs", "skip-files",
    "skip-setup", "vex", "ignorefile", "ignore-file", "ignores", "allowlist", "allow-list",
    "waiver", "waivers", "exceptions", "cve-ignore", "ignore-cves", "ignore-vulns",
}


def _repo_files() -> list[str]:
    git = shutil.which("git")
    if git:
        res = subprocess.run(
            [git, "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
        if res.returncode == 0 and res.stdout.strip():
            return [line for line in res.stdout.splitlines() if line]
    skip = {".git", ".venv", "node_modules", ".hypothesis", ".mypy_cache", ".ruff_cache"}
    return [
        p.relative_to(ROOT).as_posix()
        for p in ROOT.rglob("*")
        if p.is_file() and not skip.intersection(p.relative_to(ROOT).parts)
    ]


def _all_keys(node: Any) -> list[str]:
    keys: list[str] = []
    if isinstance(node, dict):
        for k, v in node.items():
            keys.append(str(k).lower())
            keys.extend(_all_keys(v))
    elif isinstance(node, list):
        for item in node:
            keys.extend(_all_keys(item))
    return keys


def test_no_scanner_ignore_or_waiver_file_in_repository() -> None:
    """Req 4.7, 4.11: no scanner ignore file, allowlist, or waiver anywhere."""
    offenders = [f for f in _repo_files() if SCANNER_FILE_RE.search(f)]
    assert offenders == []


def test_no_waiver_keys_or_thresholds_in_workflows() -> None:
    """Req 4.7, 4.11: no waiver keys, skip lists, or severity thresholds in any workflow."""
    for wf in WORKFLOWS.glob("*.y*ml"):
        keys = set(_all_keys(_load(wf)))
        assert not (keys & FORBIDDEN_KEYS), (wf, keys & FORBIDDEN_KEYS)
        for step in _steps(_load(wf)):
            if "trivy" in step.get("uses", "").lower():
                w = step.get("with", {})
                assert not any(k.lower().startswith(("skip", "ignore-policy", "trivyignores"))
                               for k in w)
                assert set(str(w.get("severity", "")).split(",")) == {
                    "UNKNOWN", "LOW", "MEDIUM", "HIGH", "CRITICAL"
                }
                assert str(w.get("exit-code")) == "1"
                assert w.get("ignore-unfixed") in (False, "false")


def test_waiver_detector_catches_planted_violations(tmp_path: Path) -> None:
    """The detectors themselves must fire (guards against a vacuous pass)."""
    assert SCANNER_FILE_RE.search(".trivyignore")
    assert SCANNER_FILE_RE.search("lab-images/.trivyignore.yaml")
    assert SCANNER_FILE_RE.search("security/cve-waivers.yaml")
    assert not SCANNER_FILE_RE.search("lab-images/linux-base/Dockerfile")
    planted = yaml.safe_load("jobs:\n  s:\n    steps:\n      - with:\n          trivyignores: x\n")
    assert set(_all_keys(planted)) & FORBIDDEN_KEYS


# --------------------------------------------------------------------------------------------
# Built images (docker marker)
# --------------------------------------------------------------------------------------------
def _docker(*args: str, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *args], capture_output=True, text=True, timeout=timeout, check=False
    )


@pytest.fixture(params=IMAGES)
def built_image(request: pytest.FixtureRequest) -> str:
    if shutil.which("docker") is None or _docker("info").returncode != 0:
        pytest.skip("no reachable Docker daemon")
    image: str = request.param
    if _docker("image", "inspect", image).returncode != 0:
        pytest.skip(f"{image} not built (run docker buildx bake --load in lab-images/)")
    return image


@pytest.mark.docker
def test_built_image_default_user_is_non_root(built_image: str) -> None:
    """Req 4.3."""
    user = _docker("image", "inspect", "--format", "{{.Config.User}}", built_image).stdout.strip()
    assert user not in {"", "root", "0", "0:0"}
    uid = _docker("run", "--rm", "--network", "none", "--entrypoint", "id", built_image, "-u")
    assert uid.stdout.strip() == "1000"


@pytest.mark.docker
def test_built_image_has_no_secrets_bundles_or_solutions(built_image: str) -> None:
    """Req 4.4: no secret, Check_Bundle, or solution file inside the image."""
    script = (
        "find / -xdev \\( -path /proc -o -path /sys -o -path '/usr/lib/python3*' "
        "-o -path '/usr/share/*' -o -path '/etc/ssl/*' \\) -prune -o -type f "
        "\\( -iname '*.pem' -o -iname 'id_rsa*' -o -iname 'id_ed25519*' -o -iname '.env' "
        "-o -iname '.env.*' -o -iname '*secret*' -o -iname '*credential*' -o -iname '*bundle*' "
        "-o -iname '*solution*' -o -iname 'expected*' -o -iname '.netrc' \\) -print; "
        "ls -A /grader"
    )
    res = _docker("run", "--rm", "--network", "none", "--user", "0", "--entrypoint", "sh",
                  built_image, "-c", script)
    assert res.returncode == 0, res.stderr
    assert res.stdout.strip() == "", res.stdout


@pytest.mark.docker
def test_built_image_has_no_setuid_binaries(built_image: str) -> None:
    res = _docker("run", "--rm", "--network", "none", "--entrypoint", "find", built_image,
                  "/", "-xdev", "-type", "f", "(", "-perm", "-4000", "-o", "-perm", "-2000", ")")
    assert res.stdout.strip() == ""


@pytest.mark.docker
def test_built_image_users_and_group(built_image: str) -> None:
    """Req 4.3 context: learner 1000, grader 1001, learner in devs."""
    out = _docker("run", "--rm", "--network", "none", "--entrypoint", "sh", built_image, "-c",
                  "id -u learner; id -u grader; id -nG learner").stdout.split("\n")
    assert out[0] == "1000" and out[1] == "1001" and "devs" in out[2].split()
