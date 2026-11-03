"""Milestone B invariant proof, API side (ai-content task 17.1).

The invariant: learners only ever see content an expert approved.

Variants in every lifecycle state (and every revision state, flagged or not) are requested
through every serving endpoint of the learning router, over the real use cases and the
real ``ServableCatalog``. No endpoint returns, scores against or pins a variant that is not
Approved or Served with an Approved revision and no active flag. A Rejected, retired
(Withdrawn) or Flagged variant (flag set directly through the ``item_flag`` repository) is
never returned after the change. Copies already served are not recalled: a response that
was already returned is not altered by a later change.

**Validates: Requirements 14.3, 14.4, 14.5, 22.2, 22.5, 22.6, 22.7**
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import MutableMapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from edu_it_api.errors.learning import install_learning_error_handlers
from edu_it_api.learning import LearningServices, build_learning_router
from edu_it_api.versioning import build_version_app
from edu_it_application.ai import ServableCatalog
from edu_it_application.learning import (
    AttemptQuizUseCase,
    EnrollUseCase,
    GetLessonUseCase,
    GetProgressUseCase,
    RevealHintUseCase,
    StartReviewUseCase,
    SubmitProjectUseCase,
    SubmitReviewUseCase,
)
from edu_it_domain.ai.lifecycle import SERVABLE_STATES, VariantState
from edu_it_domain.learning.models import (
    Mastery,
    MasteryState,
    QuestionOption,
    ReviewSchedule,
)
from edu_it_domain.ports.catalog import ModuleRecord, QuestionRecord, QuizRecord
from edu_it_domain.ports.item_flags import FlagReason, FlagReasonKind, ItemKey, ItemKind
from edu_it_domain.ports.lessons import LessonRecord, LessonRepository
from edu_it_domain.ports.variants import NewRevision, NewVariant, RevisionState
from edu_it_infrastructure.fakes import (
    Fake_ProjectReviewer,
    In_Memory_AttemptRepository,
    In_Memory_CatalogRepository,
    In_Memory_EnrollmentRepository,
    In_Memory_IdempotencyStore,
    In_Memory_ItemFlagRepository,
    In_Memory_MasteryRepository,
    In_Memory_ProgressRepository,
    In_Memory_ReviewRepository,
    In_Memory_VariantRepository,
    Sequential_IdGenerator,
    Test_Clock,
)
from fastapi import FastAPI

_T0 = datetime(2024, 6, 1, 9, 0, 0, tzinfo=UTC)
_TRACK = "trk-linux"
_QUIZ = "qz-1"
_CONCEPT = "con-1"
_QUESTIONS = ("qn-1", "qn-2", "qn-3", "qn-4")  # one more than the pool minimum of 3
_USERS = ("usr-a", "usr-b", "usr-c")
# The parent's correct option is 1. A variant body is read with 0-based option ids, so with
# the first option correct a variant's correct answer is 0 and answering 1 is wrong.
_VARIANT_BODY = json.dumps({"options": [{"is_correct": True}, {"is_correct": False}]})


class _Lessons(LessonRepository):
    def get_lesson(self, stable_id: str) -> LessonRecord | None:
        return None


class _World:
    """The learning router over the real ``ServableCatalog`` and in-memory ports."""

    def __init__(self) -> None:
        self.clock = Test_Clock(start=_T0)
        ids = Sequential_IdGenerator("id")
        self.variants = In_Memory_VariantRepository(self.clock)
        self.flags = In_Memory_ItemFlagRepository()
        self.reviews = In_Memory_ReviewRepository()
        self.mastery = In_Memory_MasteryRepository()
        catalog = In_Memory_CatalogRepository()
        catalog.add_module(ModuleRecord(stable_id="mod-1", track_id=_TRACK, ordinal=1))
        catalog.add_quiz(
            QuizRecord(
                stable_id=_QUIZ,
                lesson_id="les-1",
                questions=tuple(
                    QuestionRecord(
                        stable_id=name,
                        options=(QuestionOption(1, True), QuestionOption(2, False)),
                        concept_ids=(_CONCEPT,),
                    )
                    for name in _QUESTIONS
                ),
                content_version="v1",
            )
        )
        for user in _USERS:
            self.reviews.save(
                ReviewSchedule(
                    user_id=user,
                    concept_id=_CONCEPT,
                    due_at=_T0 - timedelta(days=1),
                    interval=timedelta(days=1),
                )
            )
            self.mastery.save(
                Mastery(user_id=user, concept_id=_CONCEPT, score=60, state=MasteryState.PRACTICED)
            )
        servable = ServableCatalog(catalog, self.flags, self.variants)
        enrollments = In_Memory_EnrollmentRepository()
        progress = In_Memory_ProgressRepository()
        attempts = In_Memory_AttemptRepository()
        services = LearningServices(
            current_user=lambda request: request.headers["x-user"],
            enroll=EnrollUseCase(
                catalog=catalog,
                enrollments=enrollments,
                progress=progress,
                mastery=self.mastery,
                clock=self.clock,
                ids=ids,
            ),
            progress=GetProgressUseCase(
                catalog=catalog, enrollments=enrollments, progress=progress, mastery=self.mastery
            ),
            lessons=GetLessonUseCase(lessons=_Lessons()),
            attempt_quiz=AttemptQuizUseCase(
                catalog=servable,
                attempts=attempts,
                mastery=self.mastery,
                reviews=self.reviews,
                idempotency=In_Memory_IdempotencyStore(),
                clock=self.clock,
                ids=ids,
            ),
            reveal_hint=RevealHintUseCase(catalog=servable, mastery=self.mastery, clock=self.clock),
            start_review=StartReviewUseCase(
                catalog=servable, reviews=self.reviews, clock=self.clock, resolver=servable
            ),
            submit_review=SubmitReviewUseCase(
                catalog=servable,
                reviews=self.reviews,
                mastery=self.mastery,
                clock=self.clock,
                resolver=servable,
            ),
            submit_project=SubmitProjectUseCase(
                catalog=catalog,
                attempts=attempts,
                reviewer=Fake_ProjectReviewer(default_points=10, pending=True),
                ids=ids,
            ),
        )
        self.app: FastAPI = build_version_app(1, [build_learning_router(services)])
        install_learning_error_handlers(self.app)
        self._counter = 0

    # -- seeding --------------------------------------------------------

    def add_variant(
        self,
        name: str,
        state: VariantState,
        revision_state: RevisionState = RevisionState.APPROVED,
        *,
        parent: str = "qn-1",
        flagged: bool = False,
    ) -> None:
        """Insert straight into the repository, whatever the state."""
        self.variants.add(NewVariant(name, parent, _TRACK, state))
        self.variants.add_revision(NewRevision(name, "c1", _VARIANT_BODY, state=revision_state))
        stored = self.variants.get(name)
        assert stored is not None
        self.variants.save(replace(stored, approved_content_version="c1"))
        if flagged:
            self.flag_variant(name)

    def flag_variant(self, name: str) -> None:
        self.flags.flag(ItemKey(ItemKind.VARIANT, name), FlagReason(FlagReasonKind.REPORTS))

    def set_state(self, name: str, state: VariantState) -> None:
        stored = self.variants.get(name)
        assert stored is not None
        self.variants.save(replace(stored, state=state))

    # -- requests -------------------------------------------------------

    def call(
        self, method: str, path: str, user: str, *, body: Any = None, query: str = ""
    ) -> tuple[int, Any]:
        self._counter += 1
        raw = json.dumps(body).encode() if body is not None else b""
        headers = [
            (b"content-type", b"application/json"),
            (b"x-user", user.encode()),
            (b"idempotency-key", f"k-{self._counter}".encode()),
        ]
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": method,
            "path": path,
            "raw_path": path.encode(),
            "query_string": query.encode(),
            "headers": headers,
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

            await self.app(scope, receive, send)

        asyncio.run(run())
        status = next(m["status"] for m in sent if m["type"] == "http.response.start")
        payload = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
        return status, json.loads(payload) if payload else None

    def served_refs(self) -> set[str]:
        """Every variant reference ``GET /reviews`` returns, across several learners."""
        refs: set[str] = set()
        for user in _USERS:
            status, body = self.call("GET", "/reviews", user, query=f"concept_id={_CONCEPT}")
            assert status == 200, body
            for question in body["questions"]:
                if question["variant_ref"] is not None:
                    refs.add(question["variant_ref"])
        return refs

    def submit_pinned(self, user: str, refs: dict[str, str]) -> tuple[int, Any]:
        """Answer option 1 (the parent's correct option) with ``refs`` as pinned variants."""
        return self.call(
            "POST",
            "/reviews",
            user,
            body={
                "concept_id": _CONCEPT,
                "selections": dict.fromkeys(_QUESTIONS, 1),
                "variant_refs": refs,
            },
        )


_NOT_SERVABLE = [
    (state, revision_state, flagged)
    for state in VariantState
    for revision_state in RevisionState
    for flagged in (False, True)
    if not (state in SERVABLE_STATES and revision_state is RevisionState.APPROVED and not flagged)
]


def _id(case: tuple[VariantState, RevisionState, bool]) -> str:
    state, revision_state, flagged = case
    return f"{state.value}-{revision_state.value}{'-flagged' if flagged else ''}"


@pytest.mark.parametrize("case", _NOT_SERVABLE, ids=_id)
def test_get_reviews_never_returns_a_variant_that_is_not_approved(
    case: tuple[VariantState, RevisionState, bool],
) -> None:
    """Every non-servable (state, revision, flag) combination is absent from ``GET /reviews``.

    **Validates: Requirements 14.3, 22.2, 22.5**
    """
    state, revision_state, flagged = case
    w = _World()
    w.add_variant("v-bad", state, revision_state, flagged=flagged)
    assert w.served_refs() == set()  # falls back to the authored parents

    status, body = w.call("GET", "/reviews", _USERS[0], query=f"concept_id={_CONCEPT}")
    assert status == 200
    assert {q["stable_id"] for q in body["questions"]} == set(_QUESTIONS)
    assert "v-bad" not in json.dumps(body)


@pytest.mark.parametrize("case", _NOT_SERVABLE, ids=_id)
def test_post_reviews_neither_scores_nor_pins_a_variant_that_is_not_approved(
    case: tuple[VariantState, RevisionState, bool],
) -> None:
    """A pinned reference to a non-servable variant is ignored by ``POST /reviews``.

    Answering option 1 is right for the parent and wrong for the variant, so a perfect score
    proves the parent was scored, not the variant.

    **Validates: Requirements 14.4, 22.5, 22.6**
    """
    state, revision_state, flagged = case
    w = _World()
    w.add_variant("v-bad", state, revision_state, flagged=flagged)

    status, body = w.submit_pinned(_USERS[0], {"qn-1": "v-bad@c1"})

    assert status == 200, body
    assert body["score"] == 100


def test_approved_and_served_variants_are_the_only_ones_returned_alongside_the_rest() -> None:
    """With one variant in every state, only the Approved or Served ones appear.

    **Validates: Requirements 14.3, 22.2**
    """
    w = _World()
    allowed: set[str] = set()
    for index, (state, revision_state, flagged) in enumerate(
        (s, r, f) for s in VariantState for r in RevisionState for f in (False, True)
    ):
        name = f"v-{index:02d}"
        w.add_variant(name, state, revision_state, flagged=flagged)
        if state in SERVABLE_STATES and revision_state is RevisionState.APPROVED and not flagged:
            allowed.add(f"{name}@c1")

    served = w.served_refs()

    assert allowed
    assert served
    assert served <= allowed


@pytest.mark.parametrize(
    ("start", "target"),
    [
        (VariantState.PENDING_REVIEW, VariantState.REJECTED),
        (VariantState.SERVED, VariantState.WITHDRAWN),
    ],
)
def test_a_rejected_or_retired_variant_is_never_returned_after_the_change(
    start: VariantState, target: VariantState
) -> None:
    """A Rejected or retired (Withdrawn) variant is never returned once the change is made.

    The response obtained before the change is a copy already served and is not recalled.

    **Validates: Requirements 14.3, 14.4, 22.5, 22.6**
    """
    w = _World()
    w.add_variant("v-1", start)
    status, before = w.call("GET", "/reviews", _USERS[0], query=f"concept_id={_CONCEPT}")
    assert status == 200
    was_served = "v-1@c1" in {q["variant_ref"] for q in before["questions"]}
    assert was_served is (start is VariantState.SERVED)
    snapshot = json.dumps(before, sort_keys=True)

    w.set_state("v-1", target)

    assert w.served_refs() == set()
    status, body = w.submit_pinned(_USERS[0], {"qn-1": "v-1@c1"})
    assert status == 200
    assert body["score"] == 100  # scored against the parent, not the withdrawn variant
    assert json.dumps(before, sort_keys=True) == snapshot  # the earlier copy is untouched


def test_a_flagged_variant_set_through_the_item_flag_repository_is_never_returned_after() -> None:
    """A flag written directly to ``item_flag`` removes the variant from the next request.

    **Validates: Requirements 14.3, 14.4, 22.5, 22.6**
    """
    w = _World()
    w.add_variant("v-1", VariantState.APPROVED)
    assert w.served_refs() == {"v-1@c1"}

    w.flag_variant("v-1")

    assert w.served_refs() == set()
    status, body = w.submit_pinned(_USERS[1], {"qn-1": "v-1@c1"})
    assert status == 200
    assert body["score"] == 100


def test_a_flagged_question_is_not_served_by_any_endpoint() -> None:
    """A flagged parent serves neither itself nor its variants through any serving endpoint.

    **Validates: Requirements 14.3, 22.2, 22.7**
    """
    w = _World()
    w.add_variant("v-1", VariantState.APPROVED)
    w.flags.flag(ItemKey(ItemKind.QUESTION, "qn-1"), FlagReason(FlagReasonKind.REPORTS))

    # reviews: neither the flagged parent nor its variant is shown
    assert w.served_refs() == set()
    status, body = w.call("GET", "/reviews", _USERS[0], query=f"concept_id={_CONCEPT}")
    assert status == 200
    assert "qn-1" not in {q["stable_id"] for q in body["questions"]}

    # quiz attempts: the flagged question is not part of the quiz any more
    status, body = w.call(
        "POST",
        "/quiz-attempts",
        _USERS[0],
        body={"quiz_id": _QUIZ, "selections": {"qn-1": 1}},
    )
    assert status >= 400
    assert "v-1" not in json.dumps(body)

    # hints: the flagged question cannot be hinted
    status, body = w.call(
        "POST", "/hints", _USERS[0], body={"quiz_id": _QUIZ, "question_id": "qn-1"}
    )
    assert status >= 400


@pytest.mark.parametrize("case", _NOT_SERVABLE, ids=_id)
def test_quiz_attempt_and_hint_responses_never_carry_a_variant(
    case: tuple[VariantState, RevisionState, bool],
) -> None:
    """Graded quiz attempts and hints serve authored questions only (decision D4).

    **Validates: Requirements 14.3, 22.2**
    """
    state, revision_state, flagged = case
    w = _World()
    w.add_variant("v-bad", state, revision_state, flagged=flagged)

    status, attempt = w.call(
        "POST",
        "/quiz-attempts",
        _USERS[0],
        body={"quiz_id": _QUIZ, "selections": dict.fromkeys(_QUESTIONS, 1)},
    )
    assert status == 200
    assert attempt["attempt"]["score"] == 100
    status, hint = w.call(
        "POST", "/hints", _USERS[0], body={"quiz_id": _QUIZ, "question_id": "qn-1"}
    )
    assert status == 200
    assert "v-bad" not in json.dumps(attempt) + json.dumps(hint)
