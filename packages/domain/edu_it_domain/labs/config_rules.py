"""Startup validation of the lab Config (Requirements 1.8, 1.11).

Pure: no I/O, no clock. ``config`` is either a mapping of key to value or any
object exposing the keys as attributes (for example the ``config`` module).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
from typing import Final

__all__ = [
    "LAB_CONFIG_KEYS",
    "LabConfigError",
    "validate_lab_config",
]

#: Every lab Config key that must be present and positive.
LAB_CONFIG_KEYS: Final[tuple[str, ...]] = (
    "LAB_SESSION_TTL",
    "LAB_IDLE_TIMEOUT",
    "LAB_GRADING_MIN_INTERVAL",
    "LAB_GRADING_RUN_DEADLINE",
    "LAB_GRADING_FREEZE_BOUND",
    "LAB_GRADING_FREEZE_ATTEMPTS",
    "LAB_GRADING_TEARDOWN_RESERVE",
    "LAB_CHECK_TIMEOUT_DEFAULT",
    "LAB_RESULT_POLL_INTERVAL",
    "SANDBOX_OPERATION_TIMEOUT",
    "LAB_ALLOWED_WEB_ORIGINS",
    "LAB_PROVISION_MAX_RETRIES",
    "LAB_PROVISION_MIN_INTERVAL",
    "LAB_PROVISION_TIMEOUT",
    "LAB_PROVISION_READY_TARGET",
    "LAB_PROVISION_RATE_LIMIT",
    "LAB_PROVISION_RATE_WINDOW",
    "SANDBOX_CPU_LIMIT",
    "SANDBOX_MEMORY_LIMIT",
    "SANDBOX_PID_LIMIT",
    "SANDBOX_WRITABLE_QUOTA",
    "SANDBOX_QUOTA_WORKSPACE",
    "SANDBOX_QUOTA_HOME",
    "SANDBOX_QUOTA_TMP",
    "SANDBOX_QUOTA_GRADER",
    "LAB_PROGRAM_MEMORY_LIMIT",
    "LAB_PROGRAM_PROC_LIMIT",
    "LAB_REAPER_INTERVAL",
    "LAB_SWEEP_INTERVAL",
    "LAB_RETENTION_PERIOD",
    "LAB_RETENTION_INTERVAL",
    "TERMINAL_PING_INTERVAL",
    "TERMINAL_MAX_MISSED_PONGS",
    "TERMINAL_MAX_MESSAGE_BYTES",
    "TERMINAL_MAX_MESSAGES_PER_SECOND",
    "TERMINAL_STATUS_POLL_INTERVAL",
    "LAB_ACTIVITY_WRITE_INTERVAL",
    "LAB_RECONNECT_BACKOFF",
    "LAB_RECONNECT_WINDOW",
    "LAB_FILE_MAX_COUNT",
    "LAB_FILE_MAX_BYTES",
    "LAB_WARNING_THRESHOLD",
    "LAB_TASK_MASTERY_GAIN",
)

_RUN_DEADLINE: Final[str] = "LAB_GRADING_RUN_DEADLINE"
_FREEZE_BOUND: Final[str] = "LAB_GRADING_FREEZE_BOUND"
_TEARDOWN_RESERVE: Final[str] = "LAB_GRADING_TEARDOWN_RESERVE"

_MISSING: Final[object] = object()


class LabConfigError(ValueError):
    """Raised when the lab Config is invalid; ``keys`` names the offending keys."""

    def __init__(self, keys: tuple[str, ...], message: str) -> None:
        super().__init__(message)
        self.keys: tuple[str, ...] = keys


def _get(config: object, key: str) -> object:
    if isinstance(config, Mapping):
        return config.get(key, _MISSING)
    return getattr(config, key, _MISSING)


def _is_positive(value: object) -> bool:
    if value is _MISSING or value is None or isinstance(value, bool):
        return False
    if isinstance(value, timedelta):
        return value > timedelta(0)
    if isinstance(value, (int, float)):
        return value > 0
    return False


def _key_is_valid(key: str, value: object) -> bool:
    if key == "LAB_ALLOWED_WEB_ORIGINS":
        return (
            isinstance(value, (tuple, list))
            and len(value) > 0
            and all(isinstance(o, str) and o != "" for o in value)
        )
    if key == "LAB_RECONNECT_BACKOFF":
        return (
            isinstance(value, (tuple, list))
            and len(value) > 0
            and all(_is_positive(d) for d in value)
        )
    return _is_positive(value)


def validate_lab_config(config: object) -> None:
    """Return normally for a valid lab Config, else raise :class:`LabConfigError`.

    Offending keys are: every missing or non-positive lab key (an empty
    ``LAB_ALLOWED_WEB_ORIGINS`` or a ``LAB_RECONNECT_BACKOFF`` with any
    non-positive element included); and, when the freeze bound plus the
    teardown reserve is not strictly below the run deadline, those three keys.
    """
    offending: list[str] = [
        key for key in LAB_CONFIG_KEYS if not _key_is_valid(key, _get(config, key))
    ]

    deadline = _get(config, _RUN_DEADLINE)
    freeze = _get(config, _FREEZE_BOUND)
    reserve = _get(config, _TEARDOWN_RESERVE)
    if (
        isinstance(deadline, timedelta)
        and isinstance(freeze, timedelta)
        and isinstance(reserve, timedelta)
        and freeze + reserve >= deadline
    ):
        for key in (_FREEZE_BOUND, _TEARDOWN_RESERVE, _RUN_DEADLINE):
            if key not in offending:
                offending.append(key)

    if offending:
        keys = tuple(offending)
        raise LabConfigError(keys, "invalid lab Config: " + ", ".join(keys))
