"""AI content use cases."""

from __future__ import annotations

from edu_it_application.ai.advisor_input import (
    ADVISOR_PROMPT,
    AdvisorInput,
    AdvisorOrderSchema,
    EligibleItems,
    build_advisor_input,
)
from edu_it_application.ai.errors import (
    InvalidReviewActionError,
    RecommendationNotFoundError,
    ReviewerExpertiseRequiredError,
    ReviewTaskNotFoundError,
)
from edu_it_application.ai.path_advisor import (
    AdvisorOutcome,
    AdvisorRunResult,
    DismissRecommendation,
    DismissResult,
    GetRecommendation,
    Recommendation,
    RunPathAdvisor,
)
from edu_it_application.ai.purge_ai_data import PurgeAiData, PurgeAiDataResult
from edu_it_application.ai.quality_jobs import (
    QUALITY_EVALUATE_ITEM_JOB,
    enqueue_item_evaluation,
    evaluate_item_dedupe_key,
    item_from_payload,
    item_payload,
)
from edu_it_application.ai.quality_loop import (
    EvaluateFlags,
    FlagItem,
    FlagOutcome,
    FlagResult,
    FoldObservations,
    FoldResult,
)
from edu_it_application.ai.refill_variant_pool import (
    RefillOutcome,
    RefillResult,
    RefillVariantPool,
    VariantDraftSchema,
)
from edu_it_application.ai.review_tasks import (
    ListReviewTasks,
    ReviewTaskActions,
    ReviewTaskDetail,
)
from edu_it_application.ai.servable_catalog import (
    NoServableQuestionError,
    ResolvedQuestion,
    ServableCatalog,
)
from edu_it_application.ai.submit_problem_report import (
    InvalidProblemReportError,
    SubmitProblemReport,
    SubmitProblemReportRequest,
    SubmitProblemReportResult,
    decode_problem_report_result,
    encode_problem_report_result,
)
from edu_it_application.ai.verify_item import (
    ItemKind,
    ItemUnderVerification,
    QuestionUnderVerification,
    VerificationContext,
    VerifyItem,
)
from edu_it_application.ai.verify_lab import LabVerifier, VerifyLab

__all__ = [
    "ADVISOR_PROMPT",
    "QUALITY_EVALUATE_ITEM_JOB",
    "AdvisorInput",
    "AdvisorOrderSchema",
    "AdvisorOutcome",
    "AdvisorRunResult",
    "DismissRecommendation",
    "DismissResult",
    "EligibleItems",
    "EvaluateFlags",
    "FlagItem",
    "FlagOutcome",
    "FlagResult",
    "FoldObservations",
    "FoldResult",
    "GetRecommendation",
    "InvalidProblemReportError",
    "InvalidReviewActionError",
    "ItemKind",
    "ItemUnderVerification",
    "LabVerifier",
    "ListReviewTasks",
    "NoServableQuestionError",
    "PurgeAiData",
    "PurgeAiDataResult",
    "QuestionUnderVerification",
    "Recommendation",
    "RecommendationNotFoundError",
    "RefillOutcome",
    "RefillResult",
    "RefillVariantPool",
    "ResolvedQuestion",
    "ReviewTaskActions",
    "ReviewTaskDetail",
    "ReviewTaskNotFoundError",
    "ReviewerExpertiseRequiredError",
    "RunPathAdvisor",
    "ServableCatalog",
    "SubmitProblemReport",
    "SubmitProblemReportRequest",
    "SubmitProblemReportResult",
    "VariantDraftSchema",
    "VerificationContext",
    "VerifyItem",
    "VerifyLab",
    "build_advisor_input",
    "decode_problem_report_result",
    "encode_problem_report_result",
    "enqueue_item_evaluation",
    "evaluate_item_dedupe_key",
    "item_from_payload",
    "item_payload",
]
