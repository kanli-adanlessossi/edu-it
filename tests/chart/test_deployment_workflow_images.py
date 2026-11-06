"""Static checks on the image supply-chain jobs of ``.github/workflows/deployment.yml`` (task 13.1).

Covers the multi-architecture build, the scan, the SBOM and the image checks. Signing,
provenance and gating (task 13.4) are covered in the last tests and confined to the sign job.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml
from check_ci_pins import find_unpinned_actions

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "deployment.yml"
TEXT = WORKFLOW.read_text(encoding="utf-8")
DOC: dict[str, Any] = yaml.safe_load(TEXT)
JOBS: dict[str, Any] = DOC["jobs"]
PLATFORMS = {"linux/amd64", "linux/arm64"}


def _steps(job: str) -> list[dict[str, Any]]:
    return JOBS[job]["steps"]


def _step(job: str, name_part: str) -> dict[str, Any]:
    found = [s for s in _steps(job) if name_part in s.get("name", "")]
    assert len(found) == 1, f"{job}: expected one step matching {name_part!r}, got {len(found)}"
    return found[0]


def _needs(job: str) -> set[str]:
    needs = JOBS[job].get("needs", [])
    return {needs} if isinstance(needs, str) else set(needs)


def _image_set() -> list[dict[str, str]]:
    """The IMAGE_SET literal embedded in the discover job."""
    script = _step("discover", "List images")["run"]
    match = re.search(r"IMAGE_SET = (\[.*?\n\s*\])", script, re.S)
    assert match, "IMAGE_SET not found in the discover job"
    literal = re.sub(r",(\s*\])", r"\1", match.group(1))
    return json.loads(literal)


# Claims: 12.1, 15.1
def test_every_image_is_built_for_both_platforms_in_its_own_job() -> None:
    matrix = JOBS["build"]["strategy"]["matrix"]
    assert set(matrix["platform"]) == PLATFORMS
    assert "discover" in _needs("build")
    # One build per platform: the platform comes from the matrix, never a combined list.
    run = _step("build", "Build ")["run"]
    assert '--platform "$PLATFORM"' in run
    assert not re.search(r"--platform[ =]\S*,", run)


# Claims: 12.1
def test_image_set_covers_every_dockerfile_target_the_chart_uses() -> None:
    images = _image_set()
    assert {i["name"] for i in images} == {"web", "api", "migrations", "worker"}
    for image in images:
        dockerfile = REPO_ROOT / "deploy" / "images" / image["dir"] / "Dockerfile"
        text = dockerfile.read_text(encoding="utf-8")
        stage = rf"^FROM\s+\S+\s+AS\s+{re.escape(image['target'])}\s*$"
        assert re.search(stage, text, re.M | re.I), image
    values = REPO_ROOT / "deploy" / "helm" / "edu-it" / "values.yaml"
    chart = yaml.safe_load(values.read_text(encoding="utf-8"))
    assert {i["name"] for i in images} == set(chart["images"])


# Claims: 12.2
def test_a_failed_build_names_the_image_and_platform_and_nothing_is_published() -> None:
    name = JOBS["build"]["name"]
    assert "matrix.image.name" in name and "matrix.platform" in name
    run = _step("build", "Build ")["run"]
    assert re.search(r"::error[^\n]*image=\$IMAGE_NAME platform=\$PLATFORM", run)
    # The build job has no push: no registry push flag, no push or login step.
    build_text = yaml.safe_dump(JOBS["build"])
    assert not re.search(r"\bpush\b|docker/login-action|--push|push=true", build_text)
    # Publish waits for every build job (default `needs` semantics: all must succeed).
    assert "build" in _needs("publish")
    assert "if" not in JOBS["publish"], "publish must not override the default success gating"


# Claims: 12.2, 12.6
def test_publish_goes_only_to_the_local_registry() -> None:
    run = _step("publish", "Publish the multi-architecture manifest")["run"]
    assert "localhost:5001) ;;" in run
    assert "Refusing to push to a non-local registry" in run
    assert DOC["env"]["LOCAL_REGISTRY"] == "localhost:5001"
    assert DOC["permissions"] == {"contents": "read"}
    for name, job in JOBS.items():
        if name != "sign":
            assert "id-token" not in job.get("permissions", {}), name


# Claims: 12.1
def test_publish_builds_one_manifest_covering_both_platforms() -> None:
    run = _step("publish", "Publish the multi-architecture manifest")["run"]
    assert "docker buildx imagetools create" in run
    assert "linux-amd64" in run and "linux-arm64" in run
    assert "linux/amd64" in run and "linux/arm64" in run
    assert "manifest is missing platform" in run


# Claims: 12.3, 12.4
def test_check_image_contents_runs_on_every_built_archive() -> None:
    step = _step("build", "check_image_contents")
    assert "tools/check_image_contents.py" in step["run"]
    assert "steps.archive.outputs.path" in step["run"]
    # It runs before the scan and before the archive is handed to publish.
    names = [s.get("name", "") for s in _steps("build")]
    assert names.index("check_image_contents") < names.index("Upload the image archive")


# Claims: 12.5, 15.2
def test_scan_uses_the_pipeline_threshold_and_fails_on_a_fixable_finding() -> None:
    assert DOC["env"]["SCAN_SEVERITY_CUTOFF"] in {"high", "critical"}
    step = _step("build", "Vulnerability scan")
    with_ = step["with"]
    assert with_["severity-cutoff"] == "${{ env.SCAN_SEVERITY_CUTOFF }}"
    assert with_["fail-build"] is True
    assert with_["only-fixed"] is True
    # The scan runs on the archive that was built and checked, per platform.
    assert "steps.archive.outputs.path" in with_["image"]


# Claims: 12.6
def test_an_incomplete_scan_fails_the_build_and_is_reported() -> None:
    assert int(DOC["env"]["SCAN_TIMEOUT_MINUTES"]) > 0
    scan = _step("build", "Vulnerability scan")
    assert scan["timeout-minutes"] == "${{ fromJSON(env.SCAN_TIMEOUT_MINUTES) }}"
    assert "continue-on-error" not in scan
    report = _step("build", "Report the scan result")
    assert "always()" in report["if"] and "steps.scan.outcome" in report["if"]
    assert "incomplete" in report["run"] and "exit 1" in report["run"]
    assert "image=$IMAGE_NAME platform=$PLATFORM" in report["run"]


# Claims: 12.7
def test_an_sbom_per_platform_is_attached_by_digest_and_read_back() -> None:
    for platform in PLATFORMS:
        step = _step("publish", f"({platform})")
        assert step["uses"].startswith("anchore/sbom-action@")
        assert step["with"]["format"] == "spdx-json"
        assert step["with"]["upload-release-assets"] is False
        slug = platform.replace("/", "_")
        assert f"steps.manifest.outputs.{slug}_digest" in step["with"]["image"]
    attach = _step("publish", "Attach the SBOMs by digest")
    assert attach["env"]["DIGEST"] == "${{ steps.manifest.outputs.digest }}"
    assert "oras attach" in attach["run"] and "oras discover" in attach["run"]
    assert "application/spdx+json" in attach["run"]
    assert "set -euo pipefail" in attach["run"]
    names = [s.get("name", "") for s in _steps("publish")]
    assert names.index("Publish the multi-architecture manifest") < names.index(
        "Attach the SBOMs by digest and verify them"
    )


# Claims: 12.11
def test_image_refs_check_covers_built_digests_and_the_four_values_files() -> None:
    assert "publish" in _needs("image-refs")
    run = _step("image-refs", "check_image_refs (built images")["run"]
    assert "tools/check_image_refs.py" in run
    assert "built-images.yaml" in run
    for target in ("dev", "eks", "aks", "onprem"):
        assert f"deploy/helm/edu-it/values-{target}.yaml" in run
        assert (REPO_ROOT / "deploy" / "helm" / "edu-it" / f"values-{target}.yaml").is_file()


def _non_comment_jobs_text() -> str:
    """The ``jobs:`` section with comment lines stripped."""
    jobs_text = TEXT.split("\njobs:", 1)[1]
    return "\n".join(ln for ln in jobs_text.splitlines() if not ln.lstrip().startswith("#"))


# Claims: 15.1
def test_pull_request_runs_build_scan_and_sbom_without_signing_or_a_real_registry() -> None:
    on = DOC.get("on", DOC.get(True))  # PyYAML (YAML 1.1) reads the key `on` as True
    assert "pull_request" in on
    assert "push" in on
    # Signing, login and the real registry exist only in the sign job (task 13.4).
    without_sign = yaml.safe_dump({k: v for k, v in JOBS.items() if k != "sign"})
    for needle in ("cosign sign", "id-token", "cosign attest", "docker/login-action", "ghcr.io"):
        assert needle not in without_sign, needle


# Claims: 12.8, 12.9, 12.12, 12.13, 15.8, 15.9
def test_signing_job_is_gated_least_privilege_and_signs_and_attests_by_digest() -> None:
    sign = JOBS["sign"]
    assert sign["permissions"] == {"contents": "read", "packages": "write", "id-token": "write"}
    assert DOC["permissions"] == {"contents": "read"}
    gate = sign["if"]
    assert "github.event_name == 'push'" in gate
    assert "default_branch" in gate and "refs/tags/v" in gate
    assert "pull_request" not in gate
    assert {"publish", "image-refs", "kind", "tests", "chart", "static-checks"} <= _needs("sign")
    assert "continue-on-error" not in yaml.safe_dump(sign)
    sign_step = _step("sign", "Sign by digest")
    assert "cosign sign --yes" in sign_step["run"]
    assert "steps.push.outputs.digest" in sign_step["env"]["REF"]
    attest = _step("sign", "provenance attestation")["run"]
    assert "cosign attest --yes --type slsaprovenance1" in attest
    assert "oras attach" in _step("sign", "Attach the SBOMs")["run"]
    names = [s.get("name", "") for s in _steps("sign")]
    assert names.index("Attach the SBOMs by digest") < names.index("Sign by digest (keyless)")
    verify = _step("sign", "Verify the signature")["run"]
    assert "tools/verify_image_signatures.py" in verify and "verify-attestation" in verify


# Claims: 12.9, 15.8
def test_release_gate_fails_unless_signing_succeeded_or_was_skipped_with_a_note() -> None:
    gate = JOBS["release-gate"]
    assert "sign" in _needs("release-gate") and "always()" in gate["if"]
    run = _step("release-gate", "release decision")["run"]
    assert "Signature verification SKIPPED" in run and "NOT released" in run and "exit 1" in run


# Claims: 12.14, 15.8
def test_kind_job_verifies_signatures_before_the_install() -> None:
    names = [s.get("name", "") for s in _steps("kind")]
    verify = next(n for n in names if n.startswith("Verify image signatures"))
    install = next(n for n in names if n.startswith("Install the Chart"))
    assert names.index(verify) < names.index(install)
    verify_step = _step("kind", "Verify image signatures (local-registry digests")
    assert "verify_image_signatures.py --pull-request" in verify_step["run"]


# Claims: 15.11
def test_every_third_party_action_is_pinned_to_a_commit_sha() -> None:
    assert find_unpinned_actions(TEXT) == []
    used = re.findall(r"uses:\s*(\S+)@", TEXT)
    for action in (
        "docker/setup-qemu-action",
        "docker/setup-buildx-action",
        "anchore/scan-action",
        "anchore/sbom-action",
        "oras-project/setup-oras",
        "actions/upload-artifact",
        "actions/download-artifact",
    ):
        assert action in used, action
