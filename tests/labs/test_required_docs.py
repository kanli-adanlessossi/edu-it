"""Required documentation checks for ``docs/LABS.md`` (task 20.2).

Validates: Requirements 4.9, 34.4, 34.5, 34.6
"""

from __future__ import annotations

import re
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

REPO_ROOT = Path(__file__).resolve().parents[2]
LABS_MD = (REPO_ROOT / "docs" / "LABS.md").read_text(encoding="utf-8")
CONFIG_PY = REPO_ROOT / "packages" / "domain" / "edu_it_domain" / "config.py"

LAB_SECTION_MARKER = "Hands-on-labs business numbers"


def _lab_config_names() -> list[str]:
    """Constants defined in config.py's hands-on-labs section (public names only)."""
    source = CONFIG_PY.read_text(encoding="utf-8")
    section = source[source.index(LAB_SECTION_MARKER) :]
    section = section.split("__all__", 1)[0]
    pattern = re.compile(r"^([A-Z][A-Z0-9_]*)\s*:\s*Final", re.MULTILINE)
    return [n for n in pattern.findall(section) if not n.startswith("_")]


LAB_CONFIG_NAMES = _lab_config_names()


def test_lab_config_names_found() -> None:
    assert len(LAB_CONFIG_NAMES) >= 30
    assert "LAB_SESSION_TTL" in LAB_CONFIG_NAMES
    assert "USABLE_RUN_BUDGET" in LAB_CONFIG_NAMES


def test_docs_contain_docker_provider_production_warning() -> None:
    """Req 4.9: the Docker provider is never for production."""
    assert re.search(r"Docker provider is never for production", LABS_MD, re.IGNORECASE)


def test_docs_contain_terminal_exit_shortcut() -> None:
    """Req 34.5: the shortcut that leaves the terminal and the editor."""
    assert "Ctrl+M" in LABS_MD
    assert "Ctrl+Shift+M" in LABS_MD


def test_docs_contain_digest_bump_procedure() -> None:
    """Req 34.6: rebuild, rescan, update the pinned digest, re-run lab-images.yml."""
    lowered = LABS_MD.lower()
    assert "updating a base-image digest" in lowered
    for step in ("rebuild", "rescan", "pinned digest", "lab-images.yml"):
        assert step in lowered, step


# Feature: hands-on-labs, Property 33: Every lab Config value is documented
# **Validates: Requirements 34.4**
@settings(max_examples=100, deadline=None)
@given(name=st.sampled_from(LAB_CONFIG_NAMES))
def test_every_lab_config_value_is_documented(name: str) -> None:
    assert name in LABS_MD, f"{name} is not documented in docs/LABS.md"
