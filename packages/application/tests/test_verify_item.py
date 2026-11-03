"""Unit tests for VerifyItem (ai-content task 7.1).

**Validates: Requirements 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7, 7.8, 7.9, 7.10, 7.11, 7.12**
"""

from __future__ import annotations

import json
from collections.abc import Sequence

from edu_it_application.ai import (
    ItemKind,
    ItemUnderVerification,
    QuestionUnderVerification,
    VerificationContext,
    VerifyItem,
)
from edu_it_domain.ai.verification import (
    FindingKind,
    LabUnderVerification,
    VerificationFinding,
)
from edu_it_domain.ports.sandbox import ExecResult
from edu_it_infrastructure.fakes import Fake_LLMProvider, Fake_SandboxProvider, Test_Clock
from edu_it_infrastructure.fakes.sandbox_provider import FakeSandbox, ProvisionMode


class _Labs:
    def __init__(self) -> None:
        self.seen: list[LabUnderVerification] = []

    def execute(self, lab: LabUnderVerification) -> tuple[VerificationFinding, ...]:
        self.seen.append(lab)
        return (VerificationFinding(FindingKind.CHECK_INCONCLUSIVE, "c1"),)


def _env(selected: list[int]) -> tuple[VerifyItem, Fake_LLMProvider, Fake_SandboxProvider, _Labs]:
    llm = Fake_LLMProvider({"verify_answer": json.dumps({"selected": selected})})
    sandbox = Fake_SandboxProvider(Test_Clock())
    labs = _Labs()
    return VerifyItem(independent_llm=llm, sandbox=sandbox, lab_verifier=labs), llm, sandbox, labs


def _question(key: tuple[int, ...]) -> ItemUnderVerification:
    return ItemUnderVerification(
        "q1",
        ItemKind.QUESTION,
        question=QuestionUnderVerification("What does ls do?", ("list", "delete"), key),
    )


def test_matching_answer_has_no_finding_and_input_is_blind() -> None:
    verify, llm, _, _ = _env([0])
    assert verify.execute(_question((0,))) == ((), ())
    ((_, sent),) = llm.calls
    assert sent.kind == "verify_answer"
    assert set(sent.fields) == {"question", "options", "locale"}


def test_different_answer_is_one_disagreement_and_key_unchanged() -> None:
    verify, _, _, _ = _env([1])
    item = _question((0,))
    findings, _ = verify.execute(item)
    assert [(f.kind, f.target) for f in findings] == [(FindingKind.DISAGREEMENT, "q1")]
    assert item.question is not None
    assert item.question.key == (0,)


def test_malformed_answer_is_a_finding_not_a_pass() -> None:
    verify, _, _, _ = _env([9])  # out-of-range index never validates
    findings, _ = verify.execute(_question((0,)))
    assert [f.kind for f in findings] == [FindingKind.ANSWER_UNAVAILABLE]


def _lesson(*commands: str) -> ItemUnderVerification:
    return ItemUnderVerification("l1", ItemKind.LESSON, commands=commands, image="img")


def test_lesson_outputs_come_from_the_sandbox_and_it_is_destroyed() -> None:
    verify, _, sandbox, _ = _env([])

    def handler(sb: FakeSandbox, cmd: Sequence[str], user: str) -> ExecResult | None:
        return ExecResult(0 if cmd[2] == "ok" else 2, cmd[2].encode(), b"", False)

    sandbox.exec_handler = handler
    findings, outputs = verify.execute(_lesson("ok", "bad"))
    assert [(o.command, o.exit_code, o.stdout) for o in outputs] == [
        ("ok", 0, "ok"),
        ("bad", 2, "bad"),
    ]
    assert [f.kind for f in findings] == [FindingKind.COMMAND_FAILED]
    assert sandbox.list_sandboxes() == ()


def test_provision_failure_is_a_finding() -> None:
    verify, _, sandbox, _ = _env([])
    sandbox.script_provision(ProvisionMode.FAIL)
    findings, outputs = verify.execute(_lesson("ls"))
    assert [f.kind for f in findings] == [FindingKind.SANDBOX_UNAVAILABLE]
    assert outputs == ()
    assert sandbox.list_sandboxes() == ()


def test_lab_is_delegated() -> None:
    verify, _, _, labs = _env([])
    lab = LabUnderVerification("lab1", "img", (), "")
    findings, _ = verify.execute(ItemUnderVerification("lab1", ItemKind.LAB, lab=lab))
    assert labs.seen == [lab]
    assert [f.kind for f in findings] == [FindingKind.CHECK_INCONCLUSIVE]


def test_duplicate_is_exact_on_normalised_text() -> None:
    verify, _, _, _ = _env([])
    ctx = VerificationContext(existing_texts=("Hello   World",))
    dup, _ = verify.execute(ItemUnderVerification("a", ItemKind.OTHER, {"en": "hello world"}), ctx)
    near, _ = verify.execute(
        ItemUnderVerification("a", ItemKind.OTHER, {"en": "hello world!"}), ctx
    )
    assert [f.kind for f in dup] == [FindingKind.DUPLICATE]
    assert near == ()


def test_coverage_names_every_uncovered_concept() -> None:
    verify, _, _, _ = _env([])
    ctx = VerificationContext(track_concepts=("a", "b", "c"), covered_concepts=frozenset({"a"}))
    findings, _ = verify.execute(ItemUnderVerification("i", ItemKind.OTHER, concepts=("b",)), ctx)
    assert [(f.kind, f.target) for f in findings] == [(FindingKind.UNCOVERED_CONCEPT, "c")]


def test_readability_flags_lessons_per_locale() -> None:
    verify, _, _, _ = _env([])
    hard = " ".join(["incomprehensibilities"] * 40) + "."
    fr_ok = (
        "La commande ls liste les fichiers du dossier courant "
        "et montre aussi les droits associés à chacun."
    )
    item = ItemUnderVerification("l", ItemKind.LESSON, {"en": hard, "fr": fr_ok})
    findings, _ = verify.execute(item)
    assert [(f.kind, f.detail.split(":")[0]) for f in findings] == [
        (FindingKind.READABILITY_OUT_OF_BOUNDS, "en")
    ]


def test_similarity_above_limit_is_a_finding() -> None:
    verify, _, _, _ = _env([])
    text = "the quick brown fox jumps over the lazy dog today"
    ctx = VerificationContext(source_corpus=(text,))
    findings, _ = verify.execute(ItemUnderVerification("s", ItemKind.OTHER, {"en": text}), ctx)
    assert [f.kind for f in findings] == [FindingKind.SIMILARITY_EXCEEDED]
