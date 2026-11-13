"""The normative list of V1_App routes (api-route-wiring design, "Route table").

``ROUTE_TABLE`` mirrors the design's Route_Table entry for entry. It feeds the CORS
method list (:func:`edu_it_api.cors.install_cors`) and the completeness tests that
compare it with the OpenAPI document and the real Lab_Router. ``/health`` and
``/ready`` are served outside ``/v1`` and are not listed. Later tasks append their
own entries so the table and the mounted routes never diverge.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

__all__ = ["ROUTE_TABLE", "RouteEntry"]

Authentication = Literal["none", "optional", "cookie"]


@dataclass(frozen=True, slots=True)
class RouteEntry:
    """One route of the V1_App."""

    method: str
    """``GET``, ``POST``, ``PUT``, ``DELETE``, or ``WS`` for a WebSocket upgrade."""

    path: str
    """Path relative to ``/v1``."""

    authentication: Authentication
    use_case: str
    milestone: Literal[1, 2]
    condition: str = "always"


def _e(
    method: str,
    path: str,
    authentication: Authentication,
    use_case: str,
    milestone: Literal[1, 2] = 1,
    condition: str = "always",
) -> RouteEntry:
    return RouteEntry(method, path, authentication, use_case, milestone, condition)


_LAB = "existing"
_OAUTH = "OAuth configured"

ROUTE_TABLE: Final[list[RouteEntry]] = [
    _e("GET", "/auth/csrf", "none", "none (token issuer)"),
    _e("POST", "/auth/sign-up", "none", "SignUpUseCase"),
    _e("POST", "/auth/verify-email", "none", "VerifyEmailUseCase"),
    _e("POST", "/auth/verify-email/resend", "none", "ResendVerificationUseCase"),
    _e("POST", "/auth/sign-in", "none", "AuthenticateUseCase"),
    _e("POST", "/auth/password-reset", "none", "RequestPasswordResetUseCase", 2),
    _e("POST", "/auth/password-reset/complete", "none", "CompletePasswordResetUseCase", 2),
    _e("GET", "/auth/github/authorize", "none", "GitHubOAuthClient.authorize_url", 2, _OAUTH),
    _e("GET", "/auth/github/callback", "none", "CompleteOAuthCallbackUseCase", 2, _OAUTH),
    _e("POST", "/auth/github/confirm", "none", "ConfirmOAuthLinkUseCase", 2, _OAUTH),
    _e("POST", "/auth/github/decline", "none", "DeclineOAuthLinkUseCase", 2, _OAUTH),
    _e("POST", "/auth/sign-out", "optional", "SignOutUseCase"),
    _e("GET", "/auth/session", "cookie", "AuthenticateSessionUseCase"),
    _e("POST", "/account/export", "cookie", "ExportUserDataUseCase", 2),
    _e("GET", "/account/export/download", "cookie", "ObjectStorage.get", 2),
    _e("POST", "/account/deletion", "cookie", "DeleteAccountUseCase", 2),
    _e("POST", "/enrollments", "cookie", "EnrollUseCase"),
    _e("GET", "/enrollments", "cookie", "ListEnrollmentsUseCase"),
    _e("GET", "/progress", "cookie", "GetProgressUseCase"),
    _e("GET", "/tracks", "cookie", "ListTracksUseCase"),
    _e("GET", "/lessons/{stable_id}", "cookie", "GetLessonUseCase"),
    _e("POST", "/quiz-attempts", "cookie", "AttemptQuizUseCase"),
    _e("GET", "/quiz-attempts", "cookie", "ListQuizAttemptsUseCase"),
    _e("POST", "/hints", "cookie", "RevealHintUseCase"),
    _e("GET", "/reviews", "cookie", "StartReviewUseCase"),
    _e("POST", "/reviews", "cookie", "SubmitReviewUseCase"),
    _e("POST", "/project-submissions", "cookie", "SubmitProjectUseCase"),
    _e("GET", "/quizzes/{stable_id}", "cookie", "GetQuizUseCase"),
    _e("GET", "/projects/{stable_id}", "cookie", "GetProjectUseCase"),
    _e("GET", "/labs/{lab_id}", "cookie", _LAB),
    _e("POST", "/lab-sessions", "cookie", _LAB),
    _e("GET", "/lab-sessions", "cookie", _LAB),
    _e("GET", "/lab-sessions/{session_id}", "cookie", _LAB),
    _e("DELETE", "/lab-sessions/{session_id}", "cookie", _LAB),
    _e("POST", "/lab-sessions/{session_id}/reset", "cookie", _LAB),
    _e("PUT", "/lab-sessions/{session_id}/files", "cookie", _LAB),
    _e("POST", "/lab-sessions/{session_id}/hints", "cookie", _LAB),
    _e("POST", "/lab-sessions/{session_id}/grading-runs", "cookie", _LAB),
    _e("GET", "/lab-sessions/{session_id}/grading-runs/{run_id}", "cookie", _LAB),
    _e("GET", "/lab-sessions/{session_id}/grading-runs/{run_id}/events", "cookie", _LAB),
    _e("WS", "/lab-sessions/{session_id}/terminal", "cookie", _LAB),
    _e("GET", "/review-tasks", "cookie", "ListReviewTasks"),
    _e("GET", "/review-tasks/{task_id}", "cookie", "ReviewTaskActions.get"),
    _e("POST", "/review-tasks/{task_id}/approve", "cookie", "ReviewTaskActions.approve"),
    _e("POST", "/review-tasks/{task_id}/reject", "cookie", "ReviewTaskActions.reject"),
    _e(
        "POST",
        "/review-tasks/{task_id}/request-changes",
        "cookie",
        "ReviewTaskActions.request_changes",
    ),
    _e("POST", "/review-tasks/{task_id}/edit", "cookie", "ReviewTaskActions.edit"),
    _e("POST", "/problem-reports", "cookie", "SubmitProblemReport"),
    _e("GET", "/path-recommendation", "cookie", "GetRecommendation"),
    _e(
        "POST",
        "/path-recommendation/{recommendation_id}/dismissal",
        "cookie",
        "DismissRecommendation",
    ),
]
