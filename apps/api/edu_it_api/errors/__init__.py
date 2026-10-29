"""RFC 9457 Problem Details serialization and locale rendering (Req 19, 7.8).

This package is the ``apps/api`` error boundary. It renders every non-2xx
response as an ``application/problem+json`` document carrying at least ``type``,
``title``, and ``status`` with ``status`` equal to the HTTP status code (Req
19.1, 19.2), renders a learner-facing ``detail`` in the request locale with a
default-locale fallback (Req 19.3, 19.4), maps Pydantic v2 validation failures
to one extension entry per rejected field with its location and reason (Req 7.8,
19.5), and never places internal representations, stack traces, SQL, or provider
errors in any member (Req 19.6), per ``docs/API-CONVENTIONS.md`` Section 1 and
``.kiro/steering/api-conventions.md``.

The pieces are kept separable so the property tests (tasks 8.3-8.6) can drive the
pure rendering logic without a running server:

- :mod:`edu_it_api.errors.locale` resolves the request locale (``fr``/``en``,
  default ``en``).
- :mod:`edu_it_api.errors.catalog` holds the FR/EN ``detail`` message catalog
  and the stable per-type ``title``/``type`` metadata.
- :mod:`edu_it_api.errors.problem` builds the ``ProblemDetails`` document from a
  status, a problem type, and the resolved locale, including the validation
  ``fields`` extension member.
- :mod:`edu_it_api.errors.handlers` installs the FastAPI exception handlers that
  turn framework errors into problem documents without leaking diagnostics.
- :mod:`edu_it_api.errors.learning` maps the learning domain/application errors
  (foreign option, cooldown, insufficient review pool, ...) to problem types
  with translated, actionable ``detail`` (Req 18.6).
"""

from __future__ import annotations

from edu_it_api.errors.catalog import (
    CSRF,
    DEFAULT_LOCALE,
    SUPPORTED_LOCALES,
    ProblemType,
    problem_type_for_status,
)
from edu_it_api.errors.handlers import install_error_handlers
from edu_it_api.errors.locale import resolve_locale
from edu_it_api.errors.problem import (
    PROBLEM_JSON_MEDIA_TYPE,
    FieldError,
    ProblemDetails,
    build_problem,
    build_validation_problem,
)

__all__ = [
    "CSRF",
    "DEFAULT_LOCALE",
    "PROBLEM_JSON_MEDIA_TYPE",
    "SUPPORTED_LOCALES",
    "FieldError",
    "ProblemDetails",
    "ProblemType",
    "build_problem",
    "build_validation_problem",
    "install_error_handlers",
    "problem_type_for_status",
    "resolve_locale",
]
