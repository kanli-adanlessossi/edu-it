"""Worker job handler for AI data retention (ai-content Req 21.4; task 23.1).

``ai.retain`` is periodic (``AI_RETENTION_INTERVAL``). Its dedupe key is the scheduler's bucket
key ``ai.retain:tick:<n>``, so several replicas, or a restart inside one bucket, enqueue it once
while it is pending. The handler runs ``PurgeAiData``; the purge is idempotent, and a failure is
logged and swallowed because the next bucket runs it again.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from edu_it_worker.ai.jobs import guarded_handler

if TYPE_CHECKING:
    from edu_it_application.ai import PurgeAiData
    from edu_it_domain.ports.task_queue import JobHandler

__all__ = ["RETAIN_JOB", "retain_handler"]

RETAIN_JOB: Final = "ai.retain"


def retain_handler(use_case: PurgeAiData) -> JobHandler:
    """``ai.retain``: periodic, no payload."""
    return guarded_handler(RETAIN_JOB, lambda _p: use_case.execute())
