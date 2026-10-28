"""Unit tests for RFC 9457 Problem_Details serialization and locale rendering.

These cover task 8.1's behavior without a running server: locale resolution with
default fallback (Req 19.3, 19.4), the standard-member/status-equality shape (Req
19.1, 19.2), one validation entry per rejected field (Req 7.8, 19.5), and the
no-internal-diagnostics guarantee (Req 19.6). Handler wiring is checked by
constructing a Starlette request and invoking the registered coroutine directly,
since the FastAPI ``TestClient`` (httpx) is not a dependency of this layer.
"""

from __future__ import annotations

import asyncio
import json
from http import HTTPStatus

import pytest
from edu_it_api.app import create_app
from edu_it_api.errors import (
    DEFAULT_LOCALE,
    PROBLEM_JSON_MEDIA_TYPE,
    build_problem,
    build_validation_problem,
    problem_type_for_status,
    resolve_locale,
)
from edu_it_api.errors.catalog import VALIDATION
from edu_it_api.errors.handlers import (
    _handle_http_exception,
    _handle_unexpected_error,
    _handle_validation_error,
    request_locale,
)
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request

# --- Locale resolution (Req 19.3, 19.4) --------------------------------------


def test_explicit_supported_locale_is_used() -> None:
    assert resolve_locale(explicit="fr") == "fr"
    assert resolve_locale(explicit="en") == "en"


def test_explicit_takes_precedence_over_accept_language() -> None:
    assert resolve_locale(explicit="fr", accept_language="en") == "fr"


def test_region_subtag_is_reduced_to_primary() -> None:
    assert resolve_locale(explicit="fr-CA") == "fr"
    assert resolve_locale(accept_language="EN-us") == "en"


def test_accept_language_first_supported_entry_wins() -> None:
    assert resolve_locale(accept_language="de, fr;q=0.9, en;q=0.8") == "fr"


def test_absent_locale_falls_back_to_default() -> None:
    assert resolve_locale() == DEFAULT_LOCALE


def test_unsupported_locale_falls_back_to_default() -> None:
    assert resolve_locale(explicit="de", accept_language="es, it") == DEFAULT_LOCALE


# --- Problem document shape and status equality (Req 19.1, 19.2) -------------


@pytest.mark.parametrize(
    "status",
    [400, 401, 403, 404, 405, 409, 422, 429, 500, 503, 418],
)
def test_problem_carries_standard_members_and_status_equals_code(status: int) -> None:
    problem = build_problem(status=status, locale="en")
    body = problem.to_response_body()
    assert set(body).issuperset({"type", "title", "status"})
    assert body["status"] == status


def test_problem_detail_is_localized() -> None:
    fr = build_problem(status=404, locale="fr").detail
    en = build_problem(status=404, locale="en").detail
    assert fr is not None and en is not None
    assert fr != en


def test_unsupported_locale_detail_falls_back_to_default() -> None:
    fallback = build_problem(status=404, locale="de").detail
    default = build_problem(status=404, locale=DEFAULT_LOCALE).detail
    assert fallback == default


def test_about_blank_type_for_uncatalogued_status() -> None:
    problem = build_problem(status=418, locale="en")
    assert problem.type == "about:blank"
    assert problem.status == 418


def test_response_body_omits_absent_optional_members() -> None:
    problem = build_problem(status=500, locale="en")
    body = problem.to_response_body()
    # No validation fields on a non-validation problem.
    assert "fields" not in body


# --- Validation failures (Req 7.8, 19.5) -------------------------------------


class _SignUp(BaseModel):
    email: str
    password: str


def _validation_errors() -> list[dict[str, object]]:
    try:
        _SignUp.model_validate({"email": 123})
    except ValidationError as exc:
        return [dict(e) for e in exc.errors()]
    raise AssertionError("expected a ValidationError")


def test_validation_problem_has_one_entry_per_rejected_field() -> None:
    errors = _validation_errors()
    problem = build_validation_problem(
        status=int(HTTPStatus.UNPROCESSABLE_ENTITY),
        locale="en",
        errors=errors,
        problem_type=VALIDATION,
    )
    assert problem.fields is not None
    assert len(problem.fields) == len(errors)
    locations = {f.location for f in problem.fields}
    # Both the wrong-typed email and the missing password are reported.
    assert any(loc.endswith("email") for loc in locations)
    assert any(loc.endswith("password") for loc in locations)


def test_validation_entry_reports_location_and_reason() -> None:
    errors = _validation_errors()
    problem = build_validation_problem(
        status=422,
        locale="en",
        errors=errors,
        problem_type=VALIDATION,
    )
    assert problem.fields is not None
    for entry in problem.fields:
        assert entry.location
        assert entry.reason


# --- No internal-diagnostic leakage (Req 19.6) -------------------------------


def test_validation_body_never_contains_rejected_input_value() -> None:
    errors = _validation_errors()
    problem = build_validation_problem(
        status=422,
        locale="en",
        errors=errors,
        problem_type=VALIDATION,
    )
    serialized = json.dumps(problem.to_response_body())
    # The rejected value (123) and Pydantic's internal input echo must not leak.
    assert '"input"' not in serialized
    assert "123" not in serialized


def test_detail_is_single_line() -> None:
    problem = build_problem(status=500, locale="en")
    assert problem.detail is not None
    assert "\n" not in problem.detail


# --- Handler wiring (Req 19.1-19.6) ------------------------------------------


def _request(path: str = "/v1/resource", headers: dict[str, str] | None = None) -> Request:
    raw_headers = [
        (k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in (headers or {}).items()
    ]
    scope = {
        "type": "http",
        "method": "POST",
        "path": path,
        "raw_path": path.encode("latin-1"),
        "query_string": b"",
        "headers": raw_headers,
    }
    return Request(scope)


def test_request_locale_reads_path_segment() -> None:
    assert request_locale(_request(path="/fr/dashboard")) == "fr"
    assert request_locale(_request(path="/en/dashboard")) == "en"


def test_request_locale_falls_back_to_accept_language_then_default() -> None:
    assert request_locale(_request(path="/v1/x", headers={"accept-language": "fr"})) == "fr"
    assert request_locale(_request(path="/v1/x")) == DEFAULT_LOCALE


def test_http_exception_handler_returns_problem_json() -> None:
    exc = StarletteHTTPException(status_code=404)
    response = asyncio.run(_handle_http_exception(_request(path="/fr/x"), exc))
    assert response.status_code == 404
    assert response.media_type == PROBLEM_JSON_MEDIA_TYPE
    body = json.loads(bytes(response.body))
    assert body["status"] == 404
    assert {"type", "title"}.issubset(body)


def test_validation_handler_returns_422_problem_with_fields() -> None:
    errors = _validation_errors()
    exc = RequestValidationError(errors)
    response = asyncio.run(_handle_validation_error(_request(), exc))
    assert response.status_code == 422
    body = json.loads(bytes(response.body))
    assert body["status"] == 422
    assert len(body["fields"]) == len(errors)


def test_unexpected_error_handler_returns_safe_500() -> None:
    boom = RuntimeError("SELECT * FROM users; secret connection string")
    response = asyncio.run(_handle_unexpected_error(_request(), boom))
    assert response.status_code == 500
    body_text = bytes(response.body).decode("utf-8")
    # The exception message (SQL / connection string) must not reach the body.
    assert "SELECT" not in body_text
    assert "secret connection string" not in body_text
    body = json.loads(body_text)
    assert body["status"] == 500


def test_status_type_lookup_maps_known_statuses() -> None:
    assert problem_type_for_status(422) is VALIDATION
    assert problem_type_for_status(404).status == 404


def test_create_app_installs_the_error_handlers() -> None:
    app = create_app()
    assert StarletteHTTPException in app.exception_handlers
    assert RequestValidationError in app.exception_handlers
    assert Exception in app.exception_handlers
