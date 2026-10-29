"""End-to-end tests for project-submission validation and review routing (task 18.9).

Drives ``POST /project-submissions`` through the real router, use case, and error
handlers: incomplete submissions yield RFC 9457 problem details and record
nothing; complete ones are recorded, routed through the ``ProjectReviewer``, and
expose a distinct ``pending`` status until the reviewer completes.

**Validates: Requirements 20.1, 20.2, 20.3, 20.4**
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import MutableMapping
from typing import Any

import pytest
from edu_it_api.errors.handlers import install_error_handlers
from edu_it_api.errors.learning import install_learning_error_handlers
from edu_it_api.learning import LearningServices, build_learning_router
from edu_it_api.versioning import build_version_app
from edu_it_application.learning import SubmitProjectUseCase
from edu_it_domain.learning.models import Rubric, RubricCriterion
from edu_it_infrastructure.fakes import (
    Fake_ProjectReviewer,
    In_Memory_AttemptRepository,
    In_Memory_CatalogRepository,
    Sequential_IdGenerator,
)
from fastapi import FastAPI

_USER = "usr-lea"
_PROJECT = "prj-1"


def _app(*, pending: bool) -> tuple[FastAPI, In_Memory_AttemptRepository]:
    catalog = In_Memory_CatalogRepository()
    attempts = In_Memory_AttemptRepository()
    catalog.add_rubric(
        Rubric(
            stable_id="rub-1",
            project_id=_PROJECT,
            criteria=(
                RubricCriterion(stable_id="crit-1", max_points=10),
                RubricCriterion(stable_id="crit-2", max_points=10),
            ),
        )
    )
    submit = SubmitProjectUseCase(
        catalog=catalog,
        attempts=attempts,
        reviewer=Fake_ProjectReviewer(default_points=10, pending=pending),
        ids=Sequential_IdGenerator("id"),
    )
    services = LearningServices(
        current_user=lambda _r: _USER,
        enroll=None,  # type: ignore[arg-type]
        progress=None,  # type: ignore[arg-type]
        lessons=None,  # type: ignore[arg-type]
        attempt_quiz=None,  # type: ignore[arg-type]
        reveal_hint=None,  # type: ignore[arg-type]
        start_review=None,  # type: ignore[arg-type]
        submit_review=None,  # type: ignore[arg-type]
        submit_project=submit,
    )
    app = build_version_app(1, [build_learning_router(services)])
    install_error_handlers(app)
    install_learning_error_handlers(app)
    return app, attempts


def _post(app: FastAPI, body: dict[str, Any], locale: str = "en") -> tuple[int, str, Any]:
    raw = json.dumps(body).encode()
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "path": "/project-submissions",
        "raw_path": b"/project-submissions",
        "query_string": b"",
        "headers": [
            (b"content-type", b"application/json"),
            (b"accept-language", locale.encode()),
        ],
        "scheme": "http",
        "server": ("test", 80),
        "client": ("test", 1),
        "root_path": "",
    }
    sent: list[MutableMapping[str, Any]] = []

    async def run() -> None:
        done = False

        async def receive() -> dict[str, Any]:
            nonlocal done
            if done:
                return {"type": "http.disconnect"}
            done = True
            return {"type": "http.request", "body": raw, "more_body": False}

        async def send(message: MutableMapping[str, Any]) -> None:
            sent.append(message)

        await app(scope, receive, send)

    asyncio.run(run())
    start = next(m for m in sent if m["type"] == "http.response.start")
    ctype = next(v for k, v in start["headers"] if k == b"content-type").decode()
    payload = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return start["status"], ctype, json.loads(payload)


_COMPLETE: dict[str, Any] = {
    "project_id": _PROJECT,
    "self_assessment": {"crit-1": 8, "crit-2": 9},
    "evidence": ["https://example.test/evidence"],
}


@pytest.mark.parametrize(
    "overrides",
    [
        {"self_assessment": {}},
        {"self_assessment": {"crit-1": 8}},
        {"evidence": []},
    ],
    ids=["missing-self-assessment", "partial-criteria", "missing-evidence"],
)
def test_incomplete_submission_is_rejected_with_problem_details_and_no_record(
    overrides: dict[str, Any],
) -> None:
    app, attempts = _app(pending=True)

    status, ctype, body = _post(app, {**_COMPLETE, **overrides})

    assert status == 422
    assert ctype.startswith("application/problem+json")
    assert str(body["type"]).endswith("/invalid-project-submission")
    assert "nothing was recorded" in body["detail"]
    assert attempts.list_project_submissions(_USER) == ()


def test_rejection_detail_is_translated() -> None:
    app, _ = _app(pending=True)
    _, _, body = _post(app, {**_COMPLETE, "evidence": []}, locale="fr")
    assert "preuve" in body["detail"]


def test_complete_submission_is_recorded_and_pending_until_reviewed() -> None:
    app, attempts = _app(pending=True)

    status, _, body = _post(app, _COMPLETE)

    assert status == 201
    assert body["status"] == "pending"
    assert body["review_outcome"] is None
    stored = attempts.list_project_submissions(_USER)
    assert [s.stable_id for s in stored] == [body["stable_id"]]


def test_completed_review_is_reported_distinctly_from_pending() -> None:
    app, _ = _app(pending=False)

    status, _, body = _post(app, _COMPLETE)

    assert status == 201
    assert body["status"] == "reviewed"
    assert body["review_outcome"] == "passed"
