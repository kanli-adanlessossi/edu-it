"""The ``ai.quality.evaluate_item`` job contract (ai-content Req 18.4, 19.1, 19.2; task 21.2).

The API (``SubmitProblemReport``) produces this job and the AI worker consumes it, so the job
name, the payload shape and the dedupe key live here, where both sides may import them. The
dedupe key is ``ai.quality.evaluate_item:<kind>:<stable_id>``: while an evaluation of an item is
pending, further reports of the same item add no second job.

Enqueuing is best-effort: the periodic ``ai.quality.cycle`` re-evaluates items whose statistics
changed, and a stored report is never lost because the queue was unavailable.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Final

from edu_it_domain.ports.item_flags import ItemKey, ItemKind

if TYPE_CHECKING:
    from collections.abc import Mapping

    from edu_it_domain.ports.task_queue import TaskQueue

__all__ = [
    "QUALITY_EVALUATE_ITEM_JOB",
    "enqueue_item_evaluation",
    "evaluate_item_dedupe_key",
    "item_from_payload",
    "item_payload",
]

_LOG = logging.getLogger(__name__)

QUALITY_EVALUATE_ITEM_JOB: Final = "ai.quality.evaluate_item"

_KIND: Final = "item_kind"
_ID: Final = "item_id"


def evaluate_item_dedupe_key(key: ItemKey) -> str:
    """Dedupe key of the pending evaluation of one item."""
    return f"{QUALITY_EVALUATE_ITEM_JOB}:{key.kind.value}:{key.stable_id}"


def item_payload(key: ItemKey) -> dict[str, object]:
    """The JSON payload of an evaluation job."""
    return {_KIND: key.kind.value, _ID: key.stable_id}


def item_from_payload(payload: Mapping[str, object]) -> ItemKey:
    """Read the item of an evaluation job; ``ValueError`` when the payload is malformed."""
    kind = payload.get(_KIND)
    stable_id = payload.get(_ID)
    if not isinstance(kind, str) or not isinstance(stable_id, str) or not stable_id:
        raise ValueError(f"payload needs {_KIND!r} and {_ID!r}")
    return ItemKey(ItemKind(kind), stable_id)


def enqueue_item_evaluation(queue: TaskQueue, key: ItemKey) -> bool:
    """Enqueue the evaluation of ``key``; ``False`` (logged) when the queue refuses it."""
    try:
        queue.enqueue(
            QUALITY_EVALUATE_ITEM_JOB, item_payload(key), dedupe_key=evaluate_item_dedupe_key(key)
        )
    except Exception:
        _LOG.warning("could not enqueue an evaluation for %s", key, exc_info=True)
        return False
    return True
