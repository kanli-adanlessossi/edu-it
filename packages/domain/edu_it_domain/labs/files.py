"""Learner file intake rules (hands-on-labs Req 14.6, 14.7, 12.9 file side).

Pure validation of the files a learner submits for a Code_Exercise: count and
size caps from Config, names restricted to the lab's declared set, and flat
names only (no path separators, no traversal). Nothing is copied anywhere when
validation fails (Req 14.7). Standard library only.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from edu_it_domain import config as _cfg

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping

__all__ = [
    "FileLimits",
    "FileValidation",
    "FileViolation",
    "validate_learner_files",
]


class FileViolation(Enum):
    """Why a set of learner files was rejected."""

    TOO_MANY_FILES = "too-many-files"
    FILE_TOO_LARGE = "file-too-large"
    NAME_NOT_ALLOWED = "name-not-allowed"
    UNSAFE_NAME = "unsafe-name"


@dataclass(frozen=True, slots=True)
class FileLimits:
    """The file caps, defaulting to the Config constants."""

    max_count: int = _cfg.LAB_FILE_MAX_COUNT
    max_bytes: int = _cfg.LAB_FILE_MAX_BYTES


@dataclass(frozen=True, slots=True)
class FileValidation:
    """Outcome of :func:`validate_learner_files`; ``violation`` is ``None`` when ok."""

    violation: FileViolation | None = None
    name: str | None = None
    """The offending file name, when the violation concerns one file."""

    @property
    def ok(self) -> bool:
        return self.violation is None


def _is_unsafe_name(name: str) -> bool:
    return name in {"", ".", ".."} or "/" in name or "\\" in name or "\x00" in name


def _size(content: str | bytes) -> int:
    return len(content) if isinstance(content, bytes) else len(content.encode("utf-8"))


def validate_learner_files(
    files: Mapping[str, str | bytes],
    allowed: Collection[str],
    config: FileLimits | None = None,
) -> FileValidation:
    """Validate ``files`` (name -> content) against the caps and ``allowed`` names.

    Checks run in a fixed order: count, then per-name safety and allow-list,
    then size. Names must be flat: separators (``/``, ``\\``), NUL, ``.`` and
    ``..`` are rejected before the allow-list is consulted, so a traversal
    attempt is reported as unsafe even if it were listed.
    """

    limits = config or FileLimits()
    if len(files) > limits.max_count:
        return FileValidation(FileViolation.TOO_MANY_FILES)
    allowed_set = frozenset(allowed)
    for name in files:
        if _is_unsafe_name(name):
            return FileValidation(FileViolation.UNSAFE_NAME, name)
        if name not in allowed_set:
            return FileValidation(FileViolation.NAME_NOT_ALLOWED, name)
    for name, content in files.items():
        if _size(content) > limits.max_bytes:
            return FileValidation(FileViolation.FILE_TOO_LARGE, name)
    return FileValidation()
