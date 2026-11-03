"""``build_served_worker`` returns both workers and ``ServedWorkers`` starts and stops both.

Building touches no datastore (engines and clients are lazy), so this runs without services.

**Validates: Requirements 13.1, 13.5**
"""

from __future__ import annotations

from pathlib import Path

import pytest
from edu_it_worker.ai.advisor_jobs import ADVISE_CYCLE_JOB, ADVISE_USER_JOB
from edu_it_worker.ai.jobs import REFILL_CYCLE_JOB, REFILL_PARENT_JOB
from edu_it_worker.ai.quality_jobs import QUALITY_CYCLE_JOB, QUALITY_EVALUATE_ITEM_JOB
from edu_it_worker.ai.retention_jobs import RETAIN_JOB
from edu_it_worker.serve import ServedWorkers, build_served_worker


class _Recorder:
    def __init__(self, name: str, log: list[str], *, fail_start: bool = False) -> None:
        self._name = name
        self._log = log
        self._fail_start = fail_start

    def start(self) -> None:
        if self._fail_start:
            raise RuntimeError("cannot start")
        self._log.append(f"start {self._name}")

    def stop(self) -> None:
        self._log.append(f"stop {self._name}")


def test_build_served_worker_returns_the_lab_and_the_ai_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content_root = Path(__file__).resolve().parents[3] / "content"
    monkeypatch.setenv("EDU_IT_CONTENT_ROOT", str(content_root))

    workers = build_served_worker()

    assert set(workers.ai.consumers) == {
        REFILL_CYCLE_JOB,
        REFILL_PARENT_JOB,
        QUALITY_CYCLE_JOB,
        QUALITY_EVALUATE_ITEM_JOB,
        ADVISE_CYCLE_JOB,
        ADVISE_USER_JOB,
        RETAIN_JOB,
    }
    assert workers.lab.consumers


def test_served_workers_start_and_stop_both() -> None:
    log: list[str] = []
    workers = ServedWorkers(lab=_Recorder("lab", log), ai=_Recorder("ai", log))  # type: ignore[arg-type]

    workers.start()
    workers.stop()

    assert log == ["start lab", "start ai", "stop ai", "stop lab"]


def test_a_failing_ai_start_stops_the_lab_worker() -> None:
    log: list[str] = []
    workers = ServedWorkers(
        lab=_Recorder("lab", log),  # type: ignore[arg-type]
        ai=_Recorder("ai", log, fail_start=True),  # type: ignore[arg-type]
    )

    with pytest.raises(RuntimeError):
        workers.start()

    assert log == ["start lab", "stop lab"]
