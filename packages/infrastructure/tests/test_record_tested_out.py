"""Unit tests for recording ``tested_out`` on ``module_progress`` (Req 21.3)."""

from __future__ import annotations

from edu_it_domain.learning.unlocking import record_tested_out
from edu_it_domain.ports.progress import (
    MODULE_STATE_IN_PROGRESS,
    MODULE_STATE_UNLOCKED,
    ModuleProgressRecord,
)
from edu_it_infrastructure.fakes import In_Memory_ProgressRepository


def test_tested_out_creates_progress_row_when_none_exists() -> None:
    progress = In_Memory_ProgressRepository()

    record = record_tested_out(progress, enrollment_id="en_1", module_id="md_1")

    assert record.tested_out is True
    assert record.state == MODULE_STATE_UNLOCKED
    assert progress.get_module_progress("en_1", "md_1") == record


def test_tested_out_preserves_existing_state_and_is_idempotent() -> None:
    progress = In_Memory_ProgressRepository()
    progress.upsert_module_progress(
        ModuleProgressRecord(enrollment_id="en_1", module_id="md_1", state=MODULE_STATE_IN_PROGRESS)
    )

    record_tested_out(progress, enrollment_id="en_1", module_id="md_1")
    record_tested_out(progress, enrollment_id="en_1", module_id="md_1")

    rows = progress.list_module_progress("en_1")
    assert len(rows) == 1
    assert rows[0].state == MODULE_STATE_IN_PROGRESS
    assert rows[0].tested_out is True
