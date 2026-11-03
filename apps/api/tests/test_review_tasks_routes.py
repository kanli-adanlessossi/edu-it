"""Route tests for the reviewer queue API (ai-content task 13.2).

**Validates: Requirements 15.2, 15.8, 15.10**
"""

from __future__ import annotations

from typing import Any

from edu_it_domain.ai.lifecycle import VariantState
from edu_it_domain.ports.review_tasks import NewReviewTask, TargetKind, TaskKind
from edu_it_domain.ports.role_assignment import ROLE_REVIEWER, RoleAssignment
from edu_it_domain.ports.variants import NewRevision, NewVariant

TRACK = "trk-a"
BODY = '{"prompt": {"en": "Q?", "fr": "Q ?"}}'


def _seed(served: Any, name: str = "v1") -> str:
    served.adapters.variants.add(NewVariant(name, "q", TRACK, VariantState.PENDING_REVIEW))
    served.adapters.variants.add_revision(NewRevision(name, "c1", BODY, origin="ai", model="m"))
    task = served.adapters.review_tasks.add(
        NewReviewTask(
            f"task-{name}",
            TaskKind.NEW_VARIANT,
            TargetKind.VARIANT,
            name,
            TRACK,
            revision_content_version="c1",
        )
    )
    return str(task.stable_id)


def _reviewer(served: Any, track: str = TRACK) -> Any:
    browser, _ = served.signed_in()
    served.adapters.roles.set_role(RoleAssignment(served.user_id(browser), ROLE_REVIEWER, track))
    return browser


def test_list_detail_and_approve(served: Any) -> None:
    task_id = _seed(served)
    reviewer = _reviewer(served)

    listed = reviewer.get("/v1/review-tasks")
    assert listed.status == 200
    assert [t["stable_id"] for t in listed.json()["items"]] == [task_id]
    assert listed.json()["next_cursor"] is None

    detail = reviewer.get(f"/v1/review-tasks/{task_id}")
    assert detail.status == 200
    assert detail.json()["revision"]["body"]["prompt"]["fr"] == "Q ?"
    assert detail.json()["revision"]["model"] == "m"

    done = reviewer.post(f"/v1/review-tasks/{task_id}/approve", {})
    assert done.status == 200
    assert done.json()["state"] == "approved"
    assert done.json()["reviewer_id"] is not None
    assert reviewer.get("/v1/review-tasks").json()["items"] == []


def test_second_decision_is_a_translated_409(served: Any) -> None:
    task_id = _seed(served)
    reviewer = _reviewer(served)
    assert reviewer.post(f"/v1/review-tasks/{task_id}/reject", {}).status == 200
    for locale, title in (("fr", "Tâche déjà traitée"), ("en", "Review task already decided")):
        reply = reviewer.post(f"/v1/review-tasks/{task_id}/approve", {}, query=f"locale={locale}")
        assert reply.status == 409
        assert reply.is_problem
        assert reply.json()["type"].endswith("review-task-already-decided")
        assert reply.json()["title"] == title


def test_other_track_and_learner_get_403_and_nothing_changes(served: Any) -> None:
    task_id = _seed(served)
    outsider = _reviewer(served, track="trk-b")
    learner, _ = served.signed_in()
    for browser in (outsider, learner):
        for reply in (
            browser.get("/v1/review-tasks"),
            browser.get(f"/v1/review-tasks/{task_id}"),
            browser.post(f"/v1/review-tasks/{task_id}/approve", {}),
        ):
            if reply.status == 200:  # the outsider's own (empty) listing
                assert reply.json()["items"] == []
                continue
            assert reply.status == 403
            assert reply.json()["type"].endswith("reviewer-expertise-required")
    assert served.adapters.review_tasks.get(task_id).state.value == "open"


def test_unknown_task_is_404_and_empty_comment_is_422(served: Any) -> None:
    task_id = _seed(served)
    reviewer = _reviewer(served)
    assert reviewer.get("/v1/review-tasks/nope").status == 404
    assert reviewer.post(f"/v1/review-tasks/{task_id}/request-changes", {}).status == 422
    assert (
        reviewer.post(f"/v1/review-tasks/{task_id}/request-changes", {"comment": "fix"}).status
        == 200
    )


def test_edit_returns_the_new_task_and_requires_csrf(served: Any) -> None:
    task_id = _seed(served)
    reviewer = _reviewer(served)
    edited = {"body": {"prompt": {"en": "Better?", "fr": "Mieux ?"}}}
    assert reviewer.post(f"/v1/review-tasks/{task_id}/edit", edited, csrf=False).status == 403
    reply = reviewer.post(f"/v1/review-tasks/{task_id}/edit", edited)
    assert reply.status == 201
    assert reply.json()["kind"] == "edited_revision"
    assert reply.header("location") == f"/v1/review-tasks/{reply.json()['stable_id']}"
