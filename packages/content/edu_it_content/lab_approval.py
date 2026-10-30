"""Approval flow for Seed_Labs (task 19, Req 23.2-23.9).

On approval the reviewer is recorded, the recording is read back, and only then
are the labs added to the Golden_Set. Without an :class:`Approval` (rejection)
nothing is recorded, synced or marked.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from edu_it_content.content_store import ContentStore
from edu_it_content.sync import Approval, SyncReport, sync_content
from edu_it_content.validation import LoadedContent

__all__ = ["LabApprovalError", "LabApprovalResult", "approve_labs"]


class LabApprovalError(RuntimeError):
    """Recording the reviewer failed; nothing was added to the Golden_Set."""


@dataclass(frozen=True, slots=True)
class LabApprovalResult:
    """Outcome of a confirmed approval."""

    report: SyncReport
    golden: frozenset[str]


def _lab_ids(content: LoadedContent) -> frozenset[str]:
    ids: set[str] = set()
    for raw in content.raw_items.get("lab", ()):
        provenance = raw.get("provenance")
        if isinstance(provenance, Mapping):
            ids.add(str(provenance["stable_id"]))
    return frozenset(ids)


def _recorded_in_provenance(content: LoadedContent, approval: Approval) -> bool:
    """True when every lab's provenance carries the approving reviewer."""
    for raw in content.raw_items.get("lab", ()):
        provenance = raw.get("provenance")
        if not isinstance(provenance, Mapping):
            return False
        if provenance.get("reviewed_by") != approval.reviewer:
            return False
    return True


def approve_labs(
    store: ContentStore, content: LoadedContent, approval: Approval | None
) -> LabApprovalResult | None:
    """Record ``approval`` for every lab, confirm it, then add the labs to the Golden_Set.

    ``approval is None`` is the rejection path: returns ``None`` and touches
    nothing (no reviewer, no sync, no golden entry; Req 23.4). If recording or
    its read-back fails, raises :class:`LabApprovalError` and the Golden_Set is
    left unchanged (Req 23.2, 23.3).
    """
    if approval is None:
        return None
    labs = _lab_ids(content)
    if not labs:
        raise LabApprovalError("no labs to approve")
    if not _recorded_in_provenance(content, approval):
        raise LabApprovalError("reviewer is not recorded in every lab's provenance")
    try:
        report = sync_content(store, content)
        for stable_id in sorted(labs):
            store.record_review(stable_id, approval.reviewer, approval.reviewed_at)
        confirmed = all(
            store.review_of(stable_id) == (approval.reviewer, approval.reviewed_at)
            for stable_id in labs
        )
    except Exception as exc:
        raise LabApprovalError(f"recording the reviewer failed: {exc}") from exc
    if not confirmed:
        raise LabApprovalError("read-back did not confirm the recorded reviewer")
    store.mark_golden(labs)
    return LabApprovalResult(report=report, golden=labs)
