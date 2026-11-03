"""The ``VerifyItem`` use case (ai-content Requirements 7.1 to 7.12).

Runs the Verification_Checks that apply to one generated item and returns
``(findings, outputs)``. It never mutates the item, never removes or rewrites a
finding, and never turns a failed check into a silent pass.

* Independent answer: a blind ``VERIFY_ANSWER`` call (no key field exists) through an
  injected, possibly different, ``LLMProvider``. A different answer is a disagreement
  finding; the key is left as generated.
* Command outputs: lesson commands run only in a Verification_Sandbox created through
  the ``SandboxProvider`` port. Outputs are returned as data, never LLM text.
* Labs are delegated to an injected ``LabVerifier`` (``VerifyLab``).
* Duplicate (exact, normalised), coverage, readability and similarity are pure checks.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Final

from edu_it_domain import config as domain_config
from edu_it_domain.ai.checks import (
    coverage,
    is_duplicate,
    readability_en,
    readability_fr,
    similarity,
)
from edu_it_domain.ai.inputs import InputKind
from edu_it_domain.ai.verification import (
    CommandOutput,
    FindingKind,
    LabUnderVerification,
    VerificationFinding,
)
from edu_it_domain.labs.sandbox_spec import LEARNER_USER, WORKSPACE_PATH, build_sandbox_spec
from edu_it_domain.ports.llm_provider import LLMInput, MalformedOutputError, PromptRef

if TYPE_CHECKING:
    from edu_it_domain.ports.llm_provider import LLMProvider
    from edu_it_domain.ports.sandbox import SandboxHandle, SandboxProvider

    from edu_it_application.ai.verify_lab import LabVerifier

__all__ = [
    "ItemKind",
    "ItemUnderVerification",
    "QuestionUnderVerification",
    "VerificationContext",
    "VerifyItem",
]

_LOG = logging.getLogger(__name__)

_BASH: Final = "/bin/bash"
_ANSWER_PROMPT: Final = PromptRef("verify_answer", "1")
_READABILITY: Final = {"en": readability_en, "fr": readability_fr}


class ItemKind(StrEnum):
    """What the Verifier is looking at."""

    QUESTION = "question"
    LESSON = "lesson"
    LAB = "lab"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class QuestionUnderVerification:
    """A generated question; ``key`` is the generated answer and is never sent out."""

    prompt: str
    options: tuple[str, ...]
    key: tuple[int, ...]
    locale: str = "en"


@dataclass(frozen=True, slots=True)
class ItemUnderVerification:
    """A generated item as the Verifier sees it."""

    item_id: str
    kind: ItemKind
    texts: Mapping[str, str] = field(default_factory=dict)
    """Learner-facing text per locale, used by the text checks."""

    question: QuestionUnderVerification | None = None
    commands: tuple[str, ...] = ()
    """Lesson command lines. Their outputs are produced by the Verifier."""

    image: str = ""
    """Sandbox image for the lesson commands."""

    lab: LabUnderVerification | None = None
    concepts: tuple[str, ...] = ()
    """Track concepts this item covers."""


@dataclass(frozen=True, slots=True)
class VerificationContext:
    """Corpora and track data the checks compare against."""

    existing_texts: tuple[str, ...] = ()
    source_corpus: tuple[str, ...] = ()
    track_concepts: tuple[str, ...] = ()
    covered_concepts: frozenset[str] = frozenset()
    """Concepts already covered by other items of the track."""


class _AnswerSchema:
    """Validates the ``{"selected": [int, ...]}`` answer of the independent answerer."""

    def __init__(self, option_count: int) -> None:
        self._count = option_count

    def validate(self, text: str) -> Mapping[str, object]:
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError("answer is not JSON") from exc
        selected = data.get("selected") if isinstance(data, dict) else None
        if not isinstance(selected, list) or not all(
            isinstance(i, int) and not isinstance(i, bool) and 0 <= i < self._count
            for i in selected
        ):
            raise ValueError("selected must be a list of valid option indexes")
        return {"selected": tuple(selected)}


class VerifyItem:
    """Verify one generated item before it is offered for review."""

    def __init__(
        self,
        *,
        independent_llm: LLMProvider,
        sandbox: SandboxProvider,
        lab_verifier: LabVerifier,
        independent_is_distinct: bool = False,
        config: object = domain_config,
    ) -> None:
        """Wire the ports.

        ``independent_is_distinct`` is true when ``LLM_VERIFIER_MODEL`` (or a second
        binding) makes the answerer a different model; it is recorded in findings.
        """
        self._llm = independent_llm
        self._sandbox = sandbox
        self._labs = lab_verifier
        self._distinct = independent_is_distinct
        self._config = config

    def execute(
        self, item: ItemUnderVerification, context: VerificationContext | None = None
    ) -> tuple[tuple[VerificationFinding, ...], tuple[CommandOutput, ...]]:
        """Run the applicable checks; return ``(findings, outputs)`` in a stable order."""
        ctx = context or VerificationContext()
        findings: list[VerificationFinding] = []
        outputs: list[CommandOutput] = []

        if item.question is not None:
            findings.extend(self._check_answer(item.item_id, item.question))
        if item.commands:
            found, outs = self._run_commands(item)
            findings.extend(found)
            outputs.extend(outs)
        if item.lab is not None:
            findings.extend(self._labs.execute(item.lab))
        findings.extend(self._check_duplicate(item, ctx))
        findings.extend(self._check_coverage(item, ctx))
        findings.extend(self._check_readability(item))
        findings.extend(self._check_similarity(item, ctx))
        return tuple(findings), tuple(outputs)

    # -- independent answer ------------------------------------------------------

    def _check_answer(
        self, item_id: str, q: QuestionUnderVerification
    ) -> list[VerificationFinding]:
        request = LLMInput(
            InputKind.VERIFY_ANSWER.value,
            {
                "question": q.prompt,
                "options": json.dumps(list(q.options), ensure_ascii=False),
                "locale": q.locale,
            },
        )
        try:
            result = self._llm.complete(
                _ANSWER_PROMPT,
                request,
                _AnswerSchema(len(q.options)),
                domain_config.LLM_MAX_RETRIES,
            )
        except MalformedOutputError:
            return [
                VerificationFinding(
                    FindingKind.ANSWER_UNAVAILABLE,
                    item_id,
                    "the independent answer was malformed after every attempt",
                )
            ]
        selected = tuple(sorted(set(result.output["selected"])))  # type: ignore[call-overload]
        if selected == tuple(sorted(set(q.key))):
            return []
        who = "different model" if self._distinct else "same model as generation"
        return [
            VerificationFinding(
                FindingKind.DISAGREEMENT,
                item_id,
                f"key={sorted(set(q.key))} answerer={list(selected)} model={result.model} ({who})",
            )
        ]

    # -- command outputs -----------------------------------------------------------

    def _run_commands(
        self, item: ItemUnderVerification
    ) -> tuple[list[VerificationFinding], list[CommandOutput]]:
        findings: list[VerificationFinding] = []
        outputs: list[CommandOutput] = []
        handle: SandboxHandle | None = None
        try:
            spec = build_sandbox_spec(_Image(item.image), f"verify-{item.item_id}-commands")
            try:
                handle = self._sandbox.provision(spec)
            except Exception as exc:
                partial = getattr(exc, "partial_handle", None)
                if partial is not None:
                    handle = partial
                _LOG.warning("verification sandbox provisioning failed: %s", type(exc).__name__)
                return [
                    VerificationFinding(
                        FindingKind.SANDBOX_UNAVAILABLE,
                        item.item_id,
                        "the command sandbox could not be provisioned",
                    )
                ], []
            bound: float = self._config.USABLE_RUN_BUDGET.total_seconds()  # type: ignore[attr-defined]
            for command in item.commands:
                try:
                    res = self._sandbox.exec(
                        handle,
                        [_BASH, "-c", command],
                        as_user=LEARNER_USER,
                        env={},
                        cwd=WORKSPACE_PATH,
                        timeout_s=bound,
                    )
                except Exception as exc:
                    _LOG.warning("verification exec failed: %s", type(exc).__name__)
                    findings.append(
                        VerificationFinding(
                            FindingKind.SANDBOX_UNAVAILABLE,
                            item.item_id,
                            f"{command}: {type(exc).__name__}",
                        )
                    )
                    break
                outputs.append(
                    CommandOutput(
                        command,
                        res.exit_code,
                        res.stdout.decode("utf-8", "replace"),
                        res.stderr.decode("utf-8", "replace"),
                        res.timed_out,
                    )
                )
                if res.timed_out or res.exit_code != 0:
                    findings.append(
                        VerificationFinding(
                            FindingKind.COMMAND_FAILED,
                            item.item_id,
                            f"{command}: "
                            + ("timed out" if res.timed_out else f"exit {res.exit_code}"),
                        )
                    )
        finally:
            if handle is not None:
                try:
                    self._sandbox.destroy(handle)
                except Exception:  # the reaper collects orphans
                    _LOG.warning("destroying sandbox %s failed", handle.sandbox_id, exc_info=True)
        return findings, outputs

    # -- pure text checks ------------------------------------------------------------

    @staticmethod
    def _check_duplicate(
        item: ItemUnderVerification, ctx: VerificationContext
    ) -> list[VerificationFinding]:
        return [
            VerificationFinding(FindingKind.DUPLICATE, item.item_id, locale)
            for locale, text in sorted(item.texts.items())
            if is_duplicate(text, ctx.existing_texts)
        ]

    @staticmethod
    def _check_coverage(
        item: ItemUnderVerification, ctx: VerificationContext
    ) -> list[VerificationFinding]:
        if not ctx.track_concepts:
            return []
        result = coverage(ctx.track_concepts, ctx.covered_concepts | set(item.concepts))
        return [
            VerificationFinding(
                FindingKind.UNCOVERED_CONCEPT, concept, f"coverage={result.share:.2f}"
            )
            for concept in result.uncovered
        ]

    def _check_readability(self, item: ItemUnderVerification) -> list[VerificationFinding]:
        if item.kind is not ItemKind.LESSON:
            return []
        low, high = self._config.READABILITY_BOUNDS  # type: ignore[attr-defined]
        findings: list[VerificationFinding] = []
        for locale, text in sorted(item.texts.items()):
            score_fn = _READABILITY.get(locale)
            if score_fn is None:
                continue
            score = score_fn(text)
            if not low <= score <= high:
                findings.append(
                    VerificationFinding(
                        FindingKind.READABILITY_OUT_OF_BOUNDS,
                        item.item_id,
                        f"{locale}: {score:.1f} outside [{low}, {high}]",
                    )
                )
        return findings

    def _check_similarity(
        self, item: ItemUnderVerification, ctx: VerificationContext
    ) -> list[VerificationFinding]:
        limit: float = self._config.SIMILARITY_LIMIT  # type: ignore[attr-defined]
        findings: list[VerificationFinding] = []
        for locale, text in sorted(item.texts.items()):
            score = similarity(text, ctx.source_corpus)
            if score > limit:
                findings.append(
                    VerificationFinding(
                        FindingKind.SIMILARITY_EXCEEDED,
                        item.item_id,
                        f"{locale}: {score:.2f} > {limit}",
                    )
                )
        return findings


class _Image:
    """Adapter giving ``build_sandbox_spec`` the one attribute it reads."""

    def __init__(self, image: str) -> None:
        self.image = image
