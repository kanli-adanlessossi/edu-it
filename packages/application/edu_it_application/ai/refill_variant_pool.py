"""The ``RefillVariantPool`` use case (ai-content Requirements 13.2 to 13.7).

One run refills the Variant_Pool of one Parent_Question ahead of time:

1. The parent must be Approved and carry no active flag (13.6); otherwise nothing happens.
2. The shortfall is ``VARIANT_POOL_THRESHOLD`` minus the servable variants, capped by
   ``VARIANTS_PER_REFILL_MAX``.
3. For each missing variant the model drafts one (budget gate and retry bound are those of
   the ``LLMProvider``, 13.5), and ``VerifyItem`` checks it (13.2).
4. No finding: the variant is stored ``PendingReview`` with one Review_Task (13.3). Any
   finding: it is stored ``Flagged`` with the findings and no task (13.4).

Malformed output, an unavailable provider or a reached Budget stops the run. Nothing is
created for the failed draft, nothing servable is ever created, no job is enqueued (the use
case has no queue), and nothing is raised or escalated (13.7 to 13.10). Variants stored
before the stop stay as they are. A variant is never servable here: only a reviewer's
approval moves it to ``Approved``.

The writes of one variant (variant, revision, task) go through three ports, in that order.
A failure between them leaves a non-servable variant, never a servable one.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Final, cast

from edu_it_domain import config as domain_config
from edu_it_domain.ai.inputs import InputKind
from edu_it_domain.ai.lifecycle import VariantState
from edu_it_domain.ai.verification import VerificationFinding
from edu_it_domain.ports.item_flags import ItemKey, ItemKind
from edu_it_domain.ports.llm_provider import (
    BudgetReachedError,
    LLMInput,
    MalformedOutputError,
    MissingFixtureError,
    PersonalDataInRequestError,
    PromptRef,
    ProviderBindingError,
    ProviderUnavailableError,
)
from edu_it_domain.ports.review_tasks import NewReviewTask, TargetKind, TaskKind
from edu_it_domain.ports.variants import NewRevision, NewVariant, RevisionState

from edu_it_application.ai.review_tasks import revision_content_version
from edu_it_application.ai.verify_item import (
    ItemKind as VerifiedItemKind,
)
from edu_it_application.ai.verify_item import (
    ItemUnderVerification,
    QuestionUnderVerification,
    VerificationContext,
)

if TYPE_CHECKING:
    from edu_it_domain.ports.clock import Clock
    from edu_it_domain.ports.id_generator import IdGenerator
    from edu_it_domain.ports.item_flags import ItemFlagReader
    from edu_it_domain.ports.llm_provider import LLMProvider
    from edu_it_domain.ports.parent_questions import ParentQuestion, ParentQuestionSource
    from edu_it_domain.ports.review_tasks import ReviewTaskRepository
    from edu_it_domain.ports.variants import VariantRepository

    from edu_it_application.ai.verify_item import VerifyItem

__all__ = ["RefillOutcome", "RefillResult", "RefillVariantPool", "VariantDraftSchema"]

_LOG = logging.getLogger(__name__)

#: The pinned variant prompt (``prompts/variant__v1.md``).
VARIANT_PROMPT: Final = PromptRef("variant", "1")

_LOCALES: Final = ("en", "fr")
_STOPS: Final = (
    BudgetReachedError,
    ProviderUnavailableError,
    ProviderBindingError,
    MissingFixtureError,
    MalformedOutputError,
    PersonalDataInRequestError,
)


class RefillOutcome(StrEnum):
    """How a refill run ended."""

    NOT_APPROVED = "not_approved"
    """The parent is unknown, not Approved or flagged: no model call, nothing stored."""

    POOL_FULL = "pool_full"
    """The pool is at or above the Pool_Threshold: no model call."""

    COMPLETED = "completed"
    """Every missing variant was drafted and stored."""

    STOPPED = "stopped"
    """Malformed output, an unavailable provider or a reached Budget ended the run."""


@dataclass(frozen=True, slots=True)
class RefillResult:
    """What a refill run did."""

    outcome: RefillOutcome
    pending: tuple[str, ...] = ()
    """``stable_id`` of each variant stored ``PendingReview``."""

    flagged: tuple[str, ...] = ()
    """``stable_id`` of each variant stored ``Flagged``."""

    stop_reason: str | None = None
    """Exception type that stopped the run, when ``outcome`` is ``STOPPED``."""

    findings: Mapping[str, tuple[VerificationFinding, ...]] = field(default_factory=dict)
    """Findings per flagged variant."""


def _localized(value: object, where: str) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValueError(f"{where} must be an object with en and fr")
    result: dict[str, str] = {}
    for locale in _LOCALES:
        text = value.get(locale)
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"{where}.{locale} must be a non-empty string")
        result[locale] = text
    return result


class VariantDraftSchema:
    """Validates the raw JSON of one drafted variant.

    The application layer cannot import the content models, so this checks the same shape
    as ``QuestionVariant`` minus provenance: bilingual prompt and explanation, and at least
    two bilingual options of which at least one is correct. The mapping returned holds
    exactly those fields; nothing is patched or defaulted.
    """

    def validate(self, text: str) -> Mapping[str, object]:
        """Return the validated mapping, or raise ``ValueError``."""
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError("variant draft is not JSON") from exc
        if not isinstance(data, dict):
            raise ValueError("variant draft must be an object")
        raw_options = data.get("options")
        if not isinstance(raw_options, list) or len(raw_options) < 2:
            raise ValueError("a variant needs at least two options")
        options: list[dict[str, object]] = []
        for index, raw in enumerate(raw_options):
            if not isinstance(raw, dict) or not isinstance(raw.get("is_correct"), bool):
                raise ValueError(f"options[{index}] needs a boolean is_correct")
            options.append(
                {
                    "text": _localized(raw.get("text"), f"options[{index}].text"),
                    "is_correct": raw["is_correct"],
                }
            )
        if not any(option["is_correct"] for option in options):
            raise ValueError("a variant needs at least one correct option")
        return {
            "prompt_text": _localized(data.get("prompt_text"), "prompt_text"),
            "options": options,
            "explanation": _localized(data.get("explanation"), "explanation"),
        }


class RefillVariantPool:
    """Draft, verify and store the missing variants of one approved parent question."""

    def __init__(
        self,
        *,
        llm: LLMProvider,
        verifier: VerifyItem,
        parents: ParentQuestionSource,
        variants: VariantRepository,
        tasks: ReviewTaskRepository,
        flags: ItemFlagReader,
        clock: Clock,
        ids: IdGenerator,
        glossary: str,
        config: object = domain_config,
    ) -> None:
        """Wire the ports. ``glossary`` is the JSON term-pair text sent with every draft."""
        self._llm = llm
        self._verifier = verifier
        self._parents = parents
        self._variants = variants
        self._tasks = tasks
        self._flags = flags
        self._clock = clock
        self._ids = ids
        self._glossary = glossary
        self._config = config

    def execute(self, parent_stable_id: str) -> RefillResult:
        """Refill the pool of ``parent_stable_id``; never raises for a model or budget stop."""
        parent = self._approved_parent(parent_stable_id)
        if parent is None:
            return RefillResult(RefillOutcome.NOT_APPROVED)
        threshold: int = self._config.VARIANT_POOL_THRESHOLD  # type: ignore[attr-defined]
        cap: int = self._config.VARIANTS_PER_REFILL_MAX  # type: ignore[attr-defined]
        missing = threshold - self._variants.count_approved(parent.stable_id)
        shortfall = min(max(missing, 0), cap)
        if shortfall == 0:
            return RefillResult(RefillOutcome.POOL_FULL)

        pending: list[str] = []
        flagged: list[str] = []
        found: dict[str, tuple[VerificationFinding, ...]] = {}
        drafted: list[str] = []
        for _ in range(shortfall):
            try:
                stored = self._one(parent, drafted)
            except _STOPS as error:
                reason = type(error).__name__
                _LOG.warning("variant refill of %s stopped: %s", parent.stable_id, reason)
                return RefillResult(
                    RefillOutcome.STOPPED, tuple(pending), tuple(flagged), reason, found
                )
            variant_id, findings = stored
            if findings:
                flagged.append(variant_id)
                found[variant_id] = findings
            else:
                pending.append(variant_id)
        return RefillResult(RefillOutcome.COMPLETED, tuple(pending), tuple(flagged), None, found)

    # -- steps -------------------------------------------------------------------------------

    def _approved_parent(self, stable_id: str) -> ParentQuestion | None:
        parent = self._parents.get(stable_id)
        if parent is None or not parent.approved:
            return None
        if self._flags.active_flags([ItemKey(ItemKind.QUESTION, stable_id)]):
            return None
        return parent

    def _one(
        self, parent: ParentQuestion, drafted: list[str]
    ) -> tuple[str, tuple[VerificationFinding, ...]]:
        """Draft, verify and store one variant; return its id and its findings."""
        request = LLMInput(
            InputKind.VARIANT.value,
            {
                "parent_question": parent.prompts.get("en", ""),
                "parent_options": json.dumps(list(parent.options), ensure_ascii=False),
                "concept": parent.concept_title,
                "locale": "en",
                "glossary": self._glossary,
                "count": "1",
            },
        )
        result = self._llm.complete(
            VARIANT_PROMPT,
            request,
            VariantDraftSchema(),
            domain_config.LLM_MAX_RETRIES,
        )
        draft = result.output
        variant_id = self._ids.new_id()

        findings, _ = self._verifier.execute(
            self._subject(variant_id, parent, draft),
            VerificationContext(
                existing_texts=(*parent.prompts.values(), *drafted),
            ),
        )
        drafted.extend(self._texts(draft).values())

        body = {
            "parent_question_stable_id": parent.stable_id,
            "prompt_text": draft["prompt_text"],
            "options": draft["options"],
            "explanation": draft["explanation"],
        }
        version = revision_content_version(body)
        passed = not findings
        self._variants.add(
            NewVariant(
                stable_id=variant_id,
                parent_question_stable_id=parent.stable_id,
                track_stable_id=parent.track_stable_id,
                state=VariantState.PENDING_REVIEW if passed else VariantState.FLAGGED,
            )
        )
        self._variants.add_revision(
            NewRevision(
                variant_stable_id=variant_id,
                content_version=version,
                body_json=json.dumps(body, sort_keys=True, ensure_ascii=False),
                state=RevisionState.PENDING_REVIEW if passed else RevisionState.REJECTED,
                origin="ai",
                model=result.model,
                prompt_id=result.prompt.prompt_id,
                prompt_version=result.prompt.prompt_version,
                generation_date=self._clock.now(),
                findings_json=json.dumps(
                    [
                        {"kind": f.kind.value, "target": f.target, "detail": f.detail}
                        for f in findings
                    ],
                    ensure_ascii=False,
                ),
            )
        )
        if passed:
            self._tasks.add(
                NewReviewTask(
                    stable_id=self._ids.new_id(),
                    kind=TaskKind.NEW_VARIANT,
                    target_kind=TargetKind.VARIANT,
                    target_stable_id=variant_id,
                    track_stable_id=parent.track_stable_id,
                    revision_content_version=version,
                )
            )
        return variant_id, tuple(findings)

    @staticmethod
    def _texts(draft: Mapping[str, object]) -> dict[str, str]:
        prompt = cast("Mapping[str, str]", draft["prompt_text"])
        return dict(prompt)

    @classmethod
    def _subject(
        cls, variant_id: str, parent: ParentQuestion, draft: Mapping[str, object]
    ) -> ItemUnderVerification:
        options = cast("list[dict[str, Any]]", draft["options"])
        texts: list[str] = []
        key: list[int] = []
        for index, option in enumerate(options):
            texts.append(option["text"]["en"])
            if option["is_correct"]:
                key.append(index)
        return ItemUnderVerification(
            item_id=variant_id,
            kind=VerifiedItemKind.QUESTION,
            texts=cls._texts(draft),
            question=QuestionUnderVerification(
                prompt=cls._texts(draft)["en"],
                options=tuple(texts),
                key=tuple(key),
                locale="en",
            ),
            concepts=(parent.concept_stable_id,),
        )
