"""Absolute tool paths used by the seed lab 1 scripts (task 17 follow-up, Requirements 4.15, 4.16).

Static layer: every ``/bin/<tool>`` or ``/usr/bin/<tool>`` named in the setup script and the four
Check scripts is one of ``SEED_LAB_TOOL_PATHS``, and each is recorded in ``lab-images/README.md``.
Docker layer (``docker`` marker, skipped without Docker or the images): each recorded path exists
and is executable in both Lab_Images, ``stat`` is GNU coreutils at ``/bin/stat`` with the format
strings the Checks use, and ``/usr/bin/stat`` does not exist. That last assertion pins the
documented deviation (Alpine's ``coreutils`` installs ``stat`` under ``/bin`` only), so a
base-image change that moves it is noticed instead of silently breaking every Check.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BUNDLE = REPO_ROOT / "content" / "linux-fundamentals" / "lab-bundles" / "fix-the-shared-folder"
README = REPO_ROOT / "lab-images" / "README.md"
IMAGES = ("edu-it/lab-linux-base:dev", "edu-it/lab-python:dev")

#: Every absolute tool path the seed lab 1 scripts may call, as recorded in lab-images/README.md.
SEED_LAB_TOOL_PATHS = frozenset(
    {
        "/bin/stat",
        "/usr/bin/id",
        "/usr/bin/find",
        "/usr/bin/sha256sum",
        "/usr/bin/printf",
        "/bin/mkdir",
        "/bin/chmod",
    }
)
_TOOL = re.compile(r"(?<![\w./-])(/(?:usr/)?bin/[A-Za-z0-9_.-]+)")
#: Interpreters named in shebangs and ``sh`` invocations are not tools of the lab itself.
_INTERPRETERS = {"/bin/sh", "/bin/bash"}


def _scripts() -> list[Path]:
    found = [BUNDLE / "setup" / "setup.sh", *sorted((BUNDLE / "sandbox" / "checks").glob("*.sh"))]
    assert len(found) == 5
    return found


def _used_paths(script: Path) -> set[str]:
    code = [
        line.split("#", 1)[0] if not line.startswith("#!") else ""
        for line in script.read_text(encoding="utf-8").splitlines()
    ]
    return set(_TOOL.findall("\n".join(code))) - _INTERPRETERS


def test_scripts_only_call_recorded_tool_paths() -> None:
    used = set().union(*(_used_paths(s) for s in _scripts()))
    assert used, "the scan found no tool path; the regex is broken"
    assert used <= SEED_LAB_TOOL_PATHS, sorted(used - SEED_LAB_TOOL_PATHS)
    # The paths the Checks rely on most are really in use (guards against a vacuous scan).
    assert {"/bin/stat", "/usr/bin/find", "/usr/bin/sha256sum"} <= used


def test_check_scripts_never_use_a_stat_path_other_than_bin_stat() -> None:
    for script in _scripts():
        text = script.read_text(encoding="utf-8")
        assert "/usr/bin/stat" not in text, script.name


def test_every_recorded_path_is_documented_in_the_image_readme() -> None:
    text = README.read_text(encoding="utf-8")
    for path in SEED_LAB_TOOL_PATHS:
        assert f"`{path}`" in text, path
    assert "**Deviation: `stat` is `/bin/stat`, not `/usr/bin/stat`.**" in text


def test_check_script_headers_state_the_stat_path() -> None:
    for script in _scripts():
        if "/bin/stat" in script.read_text(encoding="utf-8") and script.parent.name == "checks":
            head = "\n".join(script.read_text(encoding="utf-8").splitlines()[:8])
            assert "/bin/stat" in head, script.name


def _docker(*args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(["docker", *args], capture_output=True, timeout=60, check=False)


def _available(image: str) -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        return _docker("image", "inspect", image).returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


@pytest.mark.docker
@pytest.mark.parametrize("image", IMAGES)
def test_seed_lab_tool_paths_exist_in_the_lab_images(image: str) -> None:
    if not _available(image):
        pytest.skip(f"Docker or image {image} not available")

    def run(*argv: str) -> subprocess.CompletedProcess[bytes]:
        return _docker(
            "run", "--rm", "--network", "none", "--entrypoint", argv[0], image, *argv[1:]
        )

    for path in sorted(SEED_LAB_TOOL_PATHS):
        check = run("/bin/sh", "-c", f'test -x "{path}"')
        assert check.returncode == 0, f"{path} is missing or not executable in {image}"

    assert run("/bin/sh", "-c", "test ! -e /usr/bin/stat").returncode == 0, "/usr/bin/stat exists"
    version = run("/bin/stat", "--version")
    assert version.returncode == 0 and b"GNU coreutils" in version.stdout
    # The exact forms the Checks use, on a directory (no -L, so a symlink would report itself).
    assert run("/bin/stat", "-c", "%F", "--", "/tmp").stdout.strip() == b"directory"
    assert run("/bin/stat", "-c", "%G %a", "--", "/tmp").stdout.split()[1] == b"1777"
    assert run("/usr/bin/id", "-nG").returncode == 0
    assert b"GNU findutils" in run("/usr/bin/find", "--version").stdout
