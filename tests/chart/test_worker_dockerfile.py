"""Worker image carries lab-images/ so content sync finds ``<repo>/lab-images/<name>`` (task 7.2).

Static checks on ``deploy/images/worker/Dockerfile``, ``.dockerignore`` and the CI build commands.
"""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

from edu_it_content.validation import _find_repo_root

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = REPO_ROOT / "deploy" / "images" / "worker" / "Dockerfile"
DOCKERIGNORE = REPO_ROOT / ".dockerignore"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "deployment.yml"


def _final_stage_lines() -> list[str]:
    """Instruction lines of the last build stage, continuation lines joined."""
    text = DOCKERFILE.read_text(encoding="utf-8").replace("\\\n", " ")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    last_from = max(i for i, ln in enumerate(lines) if ln.upper().startswith("FROM "))
    return lines[last_from:]


def _env_value(lines: list[str], key: str) -> str:
    joined = " ".join(ln for ln in lines if ln.upper().startswith("ENV "))
    match = re.search(rf"\b{key}=(\S+)", joined)
    assert match, f"ENV {key} not set in the final stage"
    return match.group(1)


# Claims: 16.1
def test_final_stage_copies_lab_images_next_to_content() -> None:
    lines = _final_stage_lines()
    copies = [ln.split()[1:] for ln in lines if ln.upper().startswith("COPY ") and "--from" not in ln]
    targets = {PurePosixPath(args[0].rstrip("/")).name: args[-1] for args in copies}
    assert targets.get("lab-images") == "/app/lab-images"
    assert targets.get("content") == "/app/content"


# Claims: 16.1
def test_lab_images_location_is_found_by_content_sync_root_lookup(tmp_path: Path) -> None:
    lines = _final_stage_lines()
    content_root = PurePosixPath(_env_value(lines, "EDU_IT_CONTENT_ROOT"))
    lab_images = PurePosixPath("/app/lab-images")
    # The lookup walks the content root and its ancestors; /app must be one of them.
    assert lab_images.parent in (content_root, *content_root.parents)

    # Mirror the image layout under tmp_path and run the real lookup.
    (tmp_path / "app" / "content").mkdir(parents=True)
    (tmp_path / "app" / "lab-images" / "linux-base").mkdir(parents=True)
    assert _find_repo_root(tmp_path / "app" / "content") == (tmp_path / "app").resolve()


# Claims: 16.1
def test_image_stays_distroless_nonroot_with_numeric_user() -> None:
    lines = _final_stage_lines()
    assert "distroless" in lines[0]
    user = next(ln for ln in lines if ln.upper().startswith("USER "))
    assert re.fullmatch(r"USER [1-9]\d*(:[1-9]\d*)?", user)
    # Nothing in the final stage may add a shell or package manager.
    assert not [ln for ln in lines if ln.upper().startswith("RUN ")]


# Claims: 16.1
def test_dockerignore_keeps_lab_images_and_content_in_the_build_context() -> None:
    patterns = [
        ln.strip()
        for ln in DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.startswith("#")
    ]
    for path in ("lab-images", "content"):
        for pattern in patterns:
            if pattern.startswith("!"):
                continue
            assert pattern.strip("/") not in (path, f"{path}/**", "**/" + path), pattern


# Claims: 16.1
def test_ci_builds_the_worker_image_with_the_repository_root_as_context() -> None:
    text = WORKFLOW.read_text(encoding="utf-8").replace("\\\n", " ")
    builds = [ln for ln in text.splitlines() if re.search(r"docker build\b", ln) and "deploy/images" in ln]
    assert builds, "no image docker build command in deployment.yml"
    for ln in builds:
        assert re.search(r"\s\.(\s|$)", ln), f"build context is not the repository root: {ln.strip()}"
