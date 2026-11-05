"""JSON logging with redaction (kubernetes-deployment Req 13.8, 13.11).

The Api and the Worker write each log record as a **single-line JSON object**
(Req 13.8). A record carries ``request_id`` when it is request-scoped and
``lab_session_id`` when it is lab-scoped; both come from context variables (so
any log call in the call stack picks them up) or from ``extra=``. A field that
is not bound is omitted rather than written as ``null``.

:class:`RedactionFilter` removes terminal I/O, secrets and learner personal data
from every record before it is formatted (Req 13.11). It works on two axes:

* **field names**: a structured field (``extra=``, nested mapping keys) whose
  name denotes terminal data, a secret or personal data has its value replaced
  by :data:`REDACTED`;
* **text patterns**: the message, exception text and every string value are
  scrubbed for e-mail addresses, bearer tokens, JWTs, cloud access keys, PEM
  private keys and ``key=value`` secret assignments.

Redaction is applied by the filter *and* again by the formatter, so a record is
safe even when the formatter is used on a handler without the filter. The module
uses only the standard library: no logging framework is pinned in
``docs/adr/0001-versions.md``.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from typing import Any, Final

__all__ = [
    "REDACTED",
    "JsonLogFormatter",
    "RedactionFilter",
    "bind_lab_session_id",
    "bind_request_id",
    "configure_logging",
    "current_lab_session_id",
    "current_request_id",
    "redact_text",
    "redact_value",
    "reset_lab_session_id",
    "reset_request_id",
]

#: Replacement written in place of a removed value.
REDACTED: Final[str] = "[REDACTED]"

_request_id_var: ContextVar[str | None] = ContextVar("edu_it_telemetry_request_id", default=None)
_lab_session_id_var: ContextVar[str | None] = ContextVar(
    "edu_it_telemetry_lab_session_id", default=None
)

#: Identifier fields that are part of the record shape and never redacted.
_CORRELATION_KEYS: Final[frozenset[str]] = frozenset({"request_id", "lab_session_id"})

#: Reserved ``LogRecord`` attributes; anything else is a caller-supplied field.
_RESERVED_RECORD_KEYS: Final[frozenset[str]] = frozenset(
    logging.makeLogRecord({}).__dict__.keys() | {"message", "asctime", "taskName"}
)

#: Field-name fragments that mark a secret (substring match on the normalized key).
_SECRET_FRAGMENTS: Final[tuple[str, ...]] = (
    "password",
    "passwd",
    "secret",
    "token",
    "apikey",
    "api_key",
    "authorization",
    "cookie",
    "credential",
    "private_key",
    "privatekey",
    "passphrase",
    "signature",
)

#: Field-name prefixes that mark terminal I/O.
_TERMINAL_PREFIXES: Final[tuple[str, ...]] = ("terminal", "pty")

#: Exact field names that mark terminal I/O.
_TERMINAL_KEYS: Final[frozenset[str]] = frozenset(
    {"stdin", "stdout", "stderr", "keystrokes", "keystroke", "tty"}
)

#: Exact field names that mark learner personal data.
_PERSONAL_KEYS: Final[frozenset[str]] = frozenset(
    {
        "email",
        "e_mail",
        "email_address",
        "name",
        "first_name",
        "last_name",
        "full_name",
        "display_name",
        "username",
        "user_name",
        "phone",
        "phone_number",
        "address",
        "ip",
        "ip_address",
        "client_ip",
        "remote_addr",
        "user_agent",
        "learner",
        "learner_id",
        "user",
        "user_id",
        "birthdate",
        "date_of_birth",
        "dob",
    }
)

_EMAIL_RE: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_BEARER_RE: Final[re.Pattern[str]] = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=\-]{8,}")
_JWT_RE: Final[re.Pattern[str]] = re.compile(
    r"\beyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]*"
)
_CLOUD_KEY_RE: Final[re.Pattern[str]] = re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")
_PEM_RE: Final[re.Pattern[str]] = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)", re.DOTALL
)
_ASSIGNMENT_RE: Final[re.Pattern[str]] = re.compile(
    r"(?i)\b([A-Za-z0-9_\-]*(?:password|passwd|secret|token|api[_\-]?key|authorization|credential)"
    r"[A-Za-z0-9_\-]*)(\s*[=:]\s*)(?:\"[^\"]*\"|'[^']*'|[^\s,;&\"']+)"
)


def bind_request_id(request_id: str) -> Token[str | None]:
    """Bind ``request_id`` to the current context; returns the reset token."""
    return _request_id_var.set(request_id)


def reset_request_id(token: Token[str | None]) -> None:
    """Restore the request id captured by ``token``."""
    _request_id_var.reset(token)


def current_request_id() -> str | None:
    """Return the bound request id, or ``None`` outside a request."""
    return _request_id_var.get()


def bind_lab_session_id(lab_session_id: str) -> Token[str | None]:
    """Bind ``lab_session_id`` to the current context; returns the reset token."""
    return _lab_session_id_var.set(lab_session_id)


def reset_lab_session_id(token: Token[str | None]) -> None:
    """Restore the lab session id captured by ``token``."""
    _lab_session_id_var.reset(token)


def current_lab_session_id() -> str | None:
    """Return the bound lab session id, or ``None`` outside lab work."""
    return _lab_session_id_var.get()


def _normalize_key(key: object) -> str:
    return re.sub(r"[\s\-.]+", "_", str(key).strip().lower())


def _is_sensitive_key(key: object) -> bool:
    normalized = _normalize_key(key)
    if normalized in _CORRELATION_KEYS:
        return False
    if normalized in _PERSONAL_KEYS or normalized in _TERMINAL_KEYS:
        return True
    if normalized.startswith(_TERMINAL_PREFIXES):
        return True
    return any(fragment in normalized for fragment in _SECRET_FRAGMENTS)


def redact_text(text: str) -> str:
    """Scrub secrets and e-mail addresses out of free text."""
    scrubbed = _PEM_RE.sub(REDACTED, text)
    scrubbed = _JWT_RE.sub(REDACTED, scrubbed)
    scrubbed = _BEARER_RE.sub(lambda m: f"{m.group(1)} {REDACTED}", scrubbed)
    scrubbed = _CLOUD_KEY_RE.sub(REDACTED, scrubbed)
    scrubbed = _ASSIGNMENT_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", scrubbed)
    return _EMAIL_RE.sub(REDACTED, scrubbed)


def redact_value(value: Any) -> Any:
    """Return ``value`` with sensitive mapping entries and text scrubbed.

    Mappings are walked: an entry with a sensitive key has its value replaced by
    :data:`REDACTED`. Sequences are walked element by element. Strings are scrubbed
    by pattern. Other scalars pass through.
    """
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Mapping):
        return {
            str(key): (REDACTED if _is_sensitive_key(key) else redact_value(item))
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [redact_value(item) for item in value]
    if isinstance(value, bytes | bytearray):
        return REDACTED
    return value


class RedactionFilter(logging.Filter):
    """Remove terminal data, secrets and learner personal data from a record.

    The filter rewrites the record in place and always lets it through. The
    message is rendered and scrubbed (``args`` are cleared so nothing is
    re-interpolated), caller-supplied fields are redacted, and exception and stack
    text are rendered and scrubbed.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact_text(record.getMessage())
        record.args = None

        if record.exc_info:
            rendered = logging.Formatter().formatException(record.exc_info)
            record.exc_text = redact_text(rendered)
            record.exc_info = None
        elif record.exc_text:
            record.exc_text = redact_text(record.exc_text)
        if record.stack_info:
            record.stack_info = redact_text(record.stack_info)

        for key in [k for k in record.__dict__ if k not in _RESERVED_RECORD_KEYS]:
            if key in _CORRELATION_KEYS:
                record.__dict__[key] = redact_text(str(record.__dict__[key]))
            elif _is_sensitive_key(key):
                record.__dict__[key] = REDACTED
            else:
                record.__dict__[key] = redact_value(record.__dict__[key])
        return True


class JsonLogFormatter(logging.Formatter):
    """Format a :class:`logging.LogRecord` as a single-line JSON object.

    The object carries ``timestamp``, ``level``, ``logger`` and ``message``, plus
    ``request_id`` and ``lab_session_id`` when bound (or supplied via ``extra=``).
    Newlines inside values are JSON-escaped, so a record is always exactly one
    line. Redaction is applied here as well as in :class:`RedactionFilter`.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": redact_text(record.getMessage()),
        }

        extras = {k: v for k, v in record.__dict__.items() if k not in _RESERVED_RECORD_KEYS}

        request_id = extras.pop("request_id", None) or current_request_id()
        if request_id is not None:
            payload["request_id"] = redact_text(str(request_id))
        lab_session_id = extras.pop("lab_session_id", None) or current_lab_session_id()
        if lab_session_id is not None:
            payload["lab_session_id"] = redact_text(str(lab_session_id))

        if record.exc_info:
            payload["exception"] = redact_text(self.formatException(record.exc_info))
        elif record.exc_text:
            payload["exception"] = redact_text(record.exc_text)
        if record.stack_info:
            payload["stack"] = redact_text(self.formatStack(record.stack_info))

        for key, value in extras.items():
            if key in payload:
                continue
            payload[key] = REDACTED if _is_sensitive_key(key) else redact_value(value)

        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging(*, level: int = logging.INFO) -> None:
    """Install the redacting JSON handler on the root logger.

    Idempotent: the root handlers are replaced by a single stream handler that
    carries :class:`RedactionFilter` and :class:`JsonLogFormatter`.
    """
    handler = logging.StreamHandler()
    handler.addFilter(RedactionFilter())
    handler.setFormatter(JsonLogFormatter())

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
