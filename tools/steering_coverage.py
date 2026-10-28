"""Steering ``fileMatch`` coverage checker (task 25, Req 22).

Phase 0 produced the steering files under ``.kiro/steering/``; several of them
are *conditionally* included (``inclusion: fileMatch``) and carry a
``fileMatchPattern`` in their YAML front matter. Requirement 22 asks the
foundations phase to verify, for each such Phase-0 steering file, that its
pattern actually matches the real paths introduced in this phase, and to update
or add a pattern where it does not — while keeping the inclusion keys and a
parseable YAML front matter intact.

This module is the framework-free core that the Property 37 property test
(``tools/tests/test_steering_filematch_coverage_properties.py``) drives. It has
no third-party dependency (stdlib only, like the sibling tooling tests) and
exposes three pure pieces:

* :func:`pattern_matches` — evaluates a Kiro ``fileMatchPattern`` (``|`` union,
  ``{a,b}`` brace alternation, ``**`` cross-segment and ``*`` within-segment
  wildcards) against a POSIX-style repository-relative path.
* :func:`read_front_matter` — parses a steering file's leading ``---`` YAML
  front matter into its ``inclusion`` key and, when present, its
  ``fileMatchPattern`` string, using a small stdlib scan (the repo pins no YAML
  library in the Python dev toolchain).
* :data:`COVERAGE_SPEC` — the declarative map from each conditionally-included
  Phase-0 steering file to the real paths introduced this phase that the file is
  intended to cover. The property test asserts every listed path matches the
  file's live ``fileMatchPattern``.

The paths in :data:`COVERAGE_SPEC` are checked to be real on disk, so the spec
cannot silently drift from the repository.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "COVERAGE_SPEC",
    "REPO_ROOT",
    "STEERING_DIR",
    "SteeringFrontMatter",
    "pattern_matches",
    "read_front_matter",
    "steering_path",
]

REPO_ROOT = Path(__file__).resolve().parents[1]
STEERING_DIR = REPO_ROOT / ".kiro" / "steering"


def steering_path(name: str) -> Path:
    """Absolute path to a steering file given its bare file name."""
    return STEERING_DIR / name


# -- Front-matter parsing -----------------------------------------------------


@dataclass(frozen=True, slots=True)
class SteeringFrontMatter:
    """The inclusion-relevant front matter of a steering file.

    ``inclusion`` is the required inclusion key (e.g. ``"fileMatch"`` or
    ``"always"``); ``file_match_pattern`` is the ``fileMatchPattern`` string when
    the file declares one, else ``None``.
    """

    inclusion: str | None
    file_match_pattern: str | None


_FRONT_MATTER_RE = re.compile(r"\A---[ \t]*\r?\n(?P<body>.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)
# A top-level ``key: value`` line in the (flat) steering front matter. Steering
# front matter is a shallow mapping, so a line-oriented scan is sufficient and
# avoids pinning a YAML dependency (matching the sibling tooling tests).
_KEY_VALUE_RE = re.compile(r"^(?P<key>[A-Za-z_][\w-]*):[ \t]*(?P<value>.*?)[ \t]*$")


def _strip_scalar_quotes(value: str) -> str:
    """Strip one layer of matching single/double quotes from a YAML scalar."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def read_front_matter(text: str) -> SteeringFrontMatter:
    """Parse the leading ``---`` YAML front matter of a steering file.

    Returns the ``inclusion`` value and the ``fileMatchPattern`` (``None`` when
    absent). Raises :class:`ValueError` when the file has no parseable leading
    front-matter block, which is exactly the "front matter stays parseable"
    guarantee Req 22.3/22.4 require after an edit.
    """
    match = _FRONT_MATTER_RE.match(text)
    if match is None:
        raise ValueError("steering file has no parseable leading YAML front matter block")

    inclusion: str | None = None
    file_match_pattern: str | None = None
    for raw_line in match.group("body").splitlines():
        line = raw_line.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        kv = _KEY_VALUE_RE.match(line)
        if kv is None:
            continue
        key = kv.group("key")
        value = _strip_scalar_quotes(kv.group("value"))
        if key == "inclusion":
            inclusion = value
        elif key == "fileMatchPattern":
            file_match_pattern = value

    return SteeringFrontMatter(inclusion=inclusion, file_match_pattern=file_match_pattern)


# -- Glob matching ------------------------------------------------------------


def _expand_alternations(pattern: str) -> list[str]:
    """Expand ``|`` unions and ``{a,b,c}`` brace groups into plain globs.

    Kiro ``fileMatchPattern`` syntax joins alternatives with ``|`` at the top
    level and allows ``{...}`` brace alternation anywhere inside a single glob.
    Both expand to a flat list of brace-free globs, any one of which matching the
    path means the pattern matches.
    """
    globs: list[str] = []
    for alternative in pattern.split("|"):
        globs.extend(_expand_braces(alternative))
    return globs


def _expand_braces(glob: str) -> list[str]:
    """Recursively expand the first ``{a,b,...}`` group in ``glob``.

    Nested and multiple brace groups are handled by recursion: each expansion
    produces strings that may still contain further groups, which are expanded on
    the next pass until none remain.
    """
    start = glob.find("{")
    if start == -1:
        return [glob]

    # Find the matching close brace for this group, honouring nesting.
    depth = 0
    end = -1
    for i in range(start, len(glob)):
        if glob[i] == "{":
            depth += 1
        elif glob[i] == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
    if end == -1:
        # Unbalanced brace: treat the rest literally, nothing to expand.
        return [glob]

    prefix = glob[:start]
    suffix = glob[end + 1 :]
    options = _split_top_level_commas(glob[start + 1 : end])

    results: list[str] = []
    for option in options:
        for expanded in _expand_braces(prefix + option + suffix):
            results.append(expanded)
    return results


def _split_top_level_commas(body: str) -> list[str]:
    """Split a brace body on commas that are not inside a nested brace group."""
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for ch in body:
        if ch == "{":
            depth += 1
            current.append(ch)
        elif ch == "}":
            depth -= 1
            current.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    parts.append("".join(current))
    return parts


def _glob_to_regex(glob: str) -> re.Pattern[str]:
    """Translate a single brace-free glob to an anchored regex.

    Segment semantics: ``**`` matches any run of characters including ``/``
    (cross-segment), ``*`` matches any run that does not cross a ``/``
    (within-segment), and ``?`` matches a single non-``/`` character. All other
    characters are matched literally.
    """
    i = 0
    out: list[str] = ["^"]
    n = len(glob)
    while i < n:
        ch = glob[i]
        if ch == "*":
            if i + 1 < n and glob[i + 1] == "*":
                out.append(".*")
                i += 2
                # Collapse a following slash so ``**/`` also matches zero
                # leading segments (e.g. ``**/*.py`` matches ``a.py``).
                if i < n and glob[i] == "/":
                    i += 1
            else:
                out.append("[^/]*")
                i += 1
        elif ch == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(ch))
            i += 1
    out.append("$")
    return re.compile("".join(out))


def pattern_matches(pattern: str, path: str) -> bool:
    """Return whether a Kiro ``fileMatchPattern`` matches a repo-relative path.

    ``path`` is a POSIX-style path relative to the repository root (e.g.
    ``apps/api/edu_it_api/csrf.py``). The pattern matches when any of its ``|``-
    and ``{}``-expanded alternatives matches the whole path.
    """
    normalized = path.replace("\\", "/").lstrip("/")
    for glob in _expand_alternations(pattern):
        if not glob:
            continue
        if _glob_to_regex(glob).match(normalized):
            return True
    return False


# -- Coverage specification ---------------------------------------------------
#
# For each conditionally-included Phase-0 steering file, the real repository-
# relative paths introduced this phase that the file is intended to cover. The
# Property 37 test asserts every listed path matches the file's live
# ``fileMatchPattern`` and that each listed path exists on disk.

COVERAGE_SPEC: dict[str, tuple[str, ...]] = {
    # The HTTP API boundary: every apps/api module, including the auth/session
    # enforcement points and the OpenAPI export (Req 22.1).
    "api-conventions.md": (
        "apps/api/edu_it_api/app.py",
        "apps/api/edu_it_api/csrf.py",
        "apps/api/edu_it_api/middleware.py",
        "apps/api/edu_it_api/openapi_export.py",
        "apps/api/edu_it_api/errors/problem.py",
    ),
    # Authentication and privacy: the real auth paths introduced this phase span
    # the apps/api enforcement (CSRF), the application use cases, the web auth
    # pages/session helper, and the privileged-user CLI (Req 22.1, 22.2).
    "auth-privacy.md": (
        "apps/api/edu_it_api/csrf.py",
        "packages/application/edu_it_application/authenticate.py",
        "packages/application/edu_it_application/sign_up.py",
        "packages/application/edu_it_application/oauth_sign_in.py",
        "packages/application/edu_it_application/reset_password.py",
        "apps/web/src/auth-forms.ts",
        "apps/web/src/session.ts",
        "apps/worker/edu_it_worker/privileged_user_cli.py",
    ),
    # Deployment: the local Docker Compose stack lives under deploy/compose/
    # (Req 22.1). No Kubernetes artifact is produced this phase.
    "deployment.md": (
        "deploy/compose/docker-compose.yml",
        "deploy/compose/wait_for_ready.py",
    ),
    # Domain rules apply across the apps and shared packages (Req 22.1).
    "domain-rules.md": (
        "packages/domain/edu_it_domain/config.py",
        "packages/application/edu_it_application/sign_up.py",
        "apps/api/edu_it_api/app.py",
    ),
    # Content pipeline and localization: the web message catalogs/locale routing
    # introduced this phase (Req 22.1).
    "content.md": (
        "apps/web/src/messages/fr.json",
        "apps/web/src/messages/en.json",
    ),
}
