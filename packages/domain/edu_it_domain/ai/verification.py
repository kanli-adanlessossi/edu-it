"""Value types of the Verifier (ai-content Requirements 7 and 8).

Pure data. The application layer maps a generated draft onto these types, so the
domain and application layers never import the content package.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

__all__ = [
    "CommandOutput",
    "FindingKind",
    "LabCheckUnderVerification",
    "LabUnderVerification",
    "VerificationFinding",
]


class FindingKind(StrEnum):
    """Kinds of Verification_Finding raised by the Verifier."""

    CHECK_PASSES_ON_STARTING_STATE = "check_passes_on_starting_state"
    CHECK_FAILS_ON_REFERENCE_SOLUTION = "check_fails_on_reference_solution"
    CHECK_INCONCLUSIVE = "check_inconclusive"
    SETUP_FAILED = "setup_failed"
    REFERENCE_SOLUTION_FAILED = "reference_solution_failed"
    SANDBOX_UNAVAILABLE = "sandbox_unavailable"
    PHASE_DEADLINE_EXCEEDED = "phase_deadline_exceeded"
    DISAGREEMENT = "disagreement"
    ANSWER_UNAVAILABLE = "answer_unavailable"
    COMMAND_FAILED = "command_failed"
    DUPLICATE = "duplicate"
    UNCOVERED_CONCEPT = "uncovered_concept"
    READABILITY_OUT_OF_BOUNDS = "readability_out_of_bounds"
    SIMILARITY_EXCEEDED = "similarity_exceeded"


@dataclass(frozen=True, slots=True)
class VerificationFinding:
    """One finding. Findings are never removed or rewritten once recorded."""

    kind: FindingKind
    target: str
    """What the finding is about, for example a check id or the lab itself."""

    detail: str = ""


@dataclass(frozen=True, slots=True)
class CommandOutput:
    """Output of one lesson command produced in a Verification_Sandbox (data, not LLM text)."""

    command: str
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False


@dataclass(frozen=True, slots=True)
class LabCheckUnderVerification:
    """One generated lab check: a script and its declared timeout."""

    check_id: str
    script: str
    timeout_s: float


@dataclass(frozen=True, slots=True)
class LabUnderVerification:
    """A generated lab as the Verifier sees it."""

    lab_id: str
    image: str
    checks: tuple[LabCheckUnderVerification, ...]
    reference_solution: str
    setup_script: str | None = None
