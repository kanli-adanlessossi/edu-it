"""Build RFC 9457 Problem Details documents (Req 19.1-19.6, 7.8).

:class:`ProblemDetails` is the serialized body: a Pydantic v2 model carrying the
standard members (``type``, ``title``, ``status``, and optionally ``detail`` and
``instance``) plus a validation ``fields`` extension member. Two builders
produce it:

- :func:`build_problem` for a status + problem type, rendering ``title`` and
  ``detail`` in the resolved request locale (Req 19.3, 19.4) with ``status``
  equal to the HTTP status code (Req 19.2).
- :func:`build_validation_problem` for a Pydantic v2 validation failure, adding
  one :class:`FieldError` per rejected field giving its location and reason in
  the ``fields`` extension member (Req 7.8, 19.5).

Both go through :func:`_sanitize_detail`/:func:`_sanitize_reason`, which keep
internal representations, stack traces, SQL, and provider errors out of every
member (Req 19.6). The validation reason uses Pydantic's own short, localizable
message and the field *location* only — never the rejected input value, which
could echo internal or sensitive data back.
"""

from __future__ import annotations

from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field

from edu_it_api.errors.catalog import ProblemType, problem_type_for_status

__all__ = [
    "PROBLEM_JSON_MEDIA_TYPE",
    "FieldError",
    "ProblemDetails",
    "build_problem",
    "build_validation_problem",
]

#: The media type every problem document is served with (RFC 9457 / D2 §1).
PROBLEM_JSON_MEDIA_TYPE: Final[str] = "application/problem+json"


class FieldError(BaseModel):
    """One rejected-field entry in the validation ``fields`` extension (Req 19.5).

    Each entry names the field's ``location`` (the dotted path to the offending
    input, e.g. ``body.email``) and the ``reason`` it was rejected. The reason is
    Pydantic's short message, never the rejected value or an internal
    representation.
    """

    model_config = ConfigDict(frozen=True)

    location: str = Field(description="Dotted path to the rejected field.")
    reason: str = Field(description="Human-readable reason the field was rejected.")


class ProblemDetails(BaseModel):
    """An RFC 9457 problem-details document (``application/problem+json``).

    Carries at least ``type``, ``title``, and ``status`` (Req 19.1); ``detail``
    and ``instance`` are included when they add value for the caller. ``fields``
    is the validation extension member (Req 19.5) and is omitted for
    non-validation problems. ``status`` always equals the response's HTTP status
    code (Req 19.2).
    """

    model_config = ConfigDict(frozen=True)

    type: str = Field(description="URI reference identifying the problem type.")
    title: str = Field(description="Short, stable, localized summary of the problem type.")
    status: int = Field(description="HTTP status code; equals the response status.")
    detail: str | None = Field(
        default=None,
        description="Localized, learner-facing explanation for this occurrence.",
    )
    instance: str | None = Field(
        default=None,
        description="URI reference identifying this specific occurrence.",
    )
    fields: tuple[FieldError, ...] | None = Field(
        default=None,
        description="One entry per rejected field for validation failures.",
    )

    def to_response_body(self) -> dict[str, Any]:
        """Return the JSON object to serialize, omitting absent optional members.

        Only the members that carry a value are emitted so a non-validation
        problem has no ``fields`` key and an occurrence with no ``detail`` or
        ``instance`` omits them, matching the RFC 9457 shape in D2 §1.
        """
        return self.model_dump(exclude_none=True)


def _sanitize_detail(text: str) -> str:
    """Collapse a detail string to a single safe line (Req 19.6).

    Learner-facing ``detail`` comes from the FR/EN catalog, so it is already free
    of diagnostics; this guard strips any stray newlines (which could smuggle in
    a stack-trace fragment if a future caller passes raw text) and trims
    surrounding whitespace.
    """
    return " ".join(text.split())


def _sanitize_reason(message: str) -> str:
    """Return a safe, single-line validation reason (Req 19.5, 19.6).

    Pydantic's error ``msg`` is a short, human-readable phrase (e.g. "Field
    required", "value is not a valid email address"); it is collapsed to one line
    so no internal representation, stack trace, or multi-line diagnostic can
    reach the response body.
    """
    return " ".join(message.split())


def _location_of(error: dict[str, Any]) -> str:
    """Render a Pydantic v2 error ``loc`` tuple as a dotted path.

    ``("body", "email")`` becomes ``"body.email"`` and
    ``("body", "items", 0, "name")`` becomes ``"body.items.0.name"`` so each
    rejected field has a stable, human-readable location without exposing the
    rejected value.
    """
    loc = error.get("loc", ())
    return ".".join(str(part) for part in loc) if loc else "(root)"


def build_problem(
    *,
    status: int,
    locale: str,
    problem_type: ProblemType | None = None,
    instance: str | None = None,
) -> ProblemDetails:
    """Build a problem document for ``status`` in ``locale`` (Req 19.1-19.4).

    ``problem_type`` supplies the stable ``type``/``title`` and the FR/EN
    ``detail`` template; when omitted a generic type is chosen for ``status``.
    ``title`` and ``detail`` are rendered in ``locale`` (the resolved request
    locale, already defaulted when absent/unsupported). The body's ``status``
    always equals ``status`` — the HTTP status code (Req 19.2) — even when the
    chosen type nominally maps to a different status.
    """
    chosen = problem_type if problem_type is not None else problem_type_for_status(status)
    return ProblemDetails(
        type=chosen.type_uri,
        title=chosen.title_for(locale),
        status=status,
        detail=_sanitize_detail(chosen.detail_for(locale)),
        instance=instance,
    )


def build_validation_problem(
    *,
    status: int,
    locale: str,
    errors: list[dict[str, Any]],
    problem_type: ProblemType,
    instance: str | None = None,
) -> ProblemDetails:
    """Build a validation problem with one entry per rejected field (Req 7.8, 19.5).

    ``errors`` is the list from a Pydantic v2 ``ValidationError.errors()`` (as
    FastAPI's ``RequestValidationError`` exposes it). Each error becomes one
    :class:`FieldError` giving the field's dotted ``location`` and a sanitized
    ``reason``; the rejected input value is never included (Req 19.6). The
    overall ``detail`` is the validation type's localized template.
    """
    field_errors = tuple(
        FieldError(location=_location_of(error), reason=_sanitize_reason(str(error.get("msg", ""))))
        for error in errors
    )
    return ProblemDetails(
        type=problem_type.type_uri,
        title=problem_type.title_for(locale),
        status=status,
        detail=_sanitize_detail(problem_type.detail_for(locale)),
        instance=instance,
        fields=field_errors,
    )
