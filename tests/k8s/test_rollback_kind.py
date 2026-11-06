"""Helm rollback verification on kind (kubernetes-deployment task 14.5, Req 21.7, 21.8, 25.9).

Flow (the ``rollback_run`` module fixture, helpers shared with the Upgrade_Test through
``tests/k8s/upgrade_fixtures.py``):

1. install the previous release, start 3 Lab_Sessions, record the baseline Helm revision, the
   count of active Lab_Sessions and the Lab_Pod identities;
2. upgrade to the candidate (one more migration), wait until it is verified (same Lab_Pods, schema
   at the candidate version, Api/Worker pods ready), then ``helm rollback`` to the baseline;
3. upgrade once more with a migration that cannot complete (the upgrade fails, nothing rolls out),
   then ``helm rollback`` to the last deployed revision: the rollback path of the Deployment_Guide.

After each rollback the tests check the release (Helm records ``Rollback to <baseline>``, status
deployed, the Deployments run the baseline images again), the count of active Lab_Sessions (equal
to the count recorded before the upgrade) and that each Lab_Session still has its Lab_Pod and an
attachable terminal. ``helm rollback`` always creates a new revision number; "the release revision
returned to the previous one" means the new revision is a rollback to the baseline revision.

Prerequisites are those of ``test_upgrade_kind.py``. Without a reachable kind cluster the kind
tests skip with the reason; the pure helper tests at the top need no cluster.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import pytest

from tests.k8s.upgrade_fixtures import (
    CHART,
    ENV_FAILING_MIGRATION_DIGEST,
    PLATFORM_NAMESPACE,
    POST_UPGRADE_READY_MAX_S,
    POST_UPGRADE_SESSIONS_MAX_S,
    RELEASE,
    PodIdentity,
    ReleaseNotResolvable,
    UpgradeHarness,
    candidate_digests,
    candidate_schema_version,
    export_chart,
    previous_digests,
    resolve_previous_release,
    wait_until,
)

D_OLD, D_NEW = "sha256:" + "a" * 64, "sha256:" + "b" * 64


# =============================================================== pure helpers (no cluster)


def rollback_entry(history: Sequence[Mapping[str, Any]], target: int) -> Mapping[str, Any] | None:
    """The newest ``deployed`` history entry that records a rollback to revision ``target``."""

    for entry in sorted(history, key=lambda e: int(e["revision"]), reverse=True):
        if entry.get("status") == "deployed" and (
            str(entry.get("description", "")).strip() == f"Rollback to {target}"
        ):
            return entry
    return None


def images_match(snapshot: Mapping[str, Mapping[str, Any]], digests: Mapping[str, str]) -> bool:
    """Every Api and Worker Deployment runs the digest of ``digests`` for its component."""

    checked = 0
    for name, deployment in snapshot.items():
        component = name.rsplit("-", 1)[-1]
        if component not in ("api", "worker") or component not in digests:
            continue
        checked += 1
        if not all(image.endswith(f"@{digests[component]}") for image in deployment["images"]):
            return False
    return checked > 0


# Claims: 21.7
def test_rollback_entry_finds_the_rollback_to_the_baseline_revision() -> None:
    history = [
        {"revision": 1, "status": "superseded", "description": "Install complete"},
        {"revision": 2, "status": "superseded", "description": "Upgrade complete"},
        {"revision": 3, "status": "deployed", "description": "Rollback to 1"},
    ]
    found = rollback_entry(history, 1)
    assert found is not None and found["revision"] == 3
    assert rollback_entry(history, 2) is None
    assert rollback_entry([{**history[2], "status": "failed"}], 1) is None


# Claims: 21.7
def test_images_match_requires_the_baseline_digest_on_api_and_worker() -> None:
    def snap(digest: str) -> dict[str, dict[str, Any]]:
        return {
            f"{RELEASE}-api": {"images": [f"localhost:5001/edu-it-api@{digest}"]},
            f"{RELEASE}-worker": {"images": [f"localhost:5001/edu-it-worker@{digest}"]},
            f"{RELEASE}-web": {"images": ["web@sha256:" + "c" * 64]},
        }

    digests = {"api": D_OLD, "worker": D_OLD, "web": D_NEW}
    assert images_match(snap(D_OLD), digests)
    assert not images_match(snap(D_NEW), digests)
    assert not images_match({}, digests)


# =============================================================== kind tests


@dataclass
class RollbackStage:
    """The observed state after one ``helm rollback``."""

    exit_code: int
    output: str
    baseline_revision: int
    history: list[dict[str, Any]]
    deployed_revision: int
    deployments: dict[str, dict[str, Any]]
    ready_s: float | None
    ready_error: str
    pods: dict[str, PodIdentity]
    statuses: list[str]
    attachable: list[bool]
    active_count: int


@dataclass
class RollbackRun:
    harness: UpgradeHarness
    previous_digests: dict[str, str]
    candidate_schema: str
    schema_baseline: str
    revision_baseline: int
    active_before: int
    pods_before: dict[str, PodIdentity]
    upgrade_exit: int = -1
    upgrade_output: str = ""
    upgrade_verified: bool = False
    upgrade_error: str = ""
    after_upgrade: RollbackStage | None = None
    failed_upgrade_exit: int = -1
    failed_upgrade_output: str = ""
    failed_upgrade_mode: str = ""
    failed_revision_status: str = ""
    active_before_failed: int = 0
    after_failed: RollbackStage | None = None
    notes: list[str] = field(default_factory=list)


def _active_count(harness: UpgradeHarness) -> int:
    return sum(1 for s in harness.sessions if harness.session_status(s) == "ready")


def _rollback(
    harness: UpgradeHarness,
    baseline: int,
) -> RollbackStage:
    """``helm rollback`` to ``baseline`` and observe the result."""

    done = harness.helm(
        "rollback",
        RELEASE,
        str(baseline),
        "-n",
        PLATFORM_NAMESPACE,
        "--wait",
        "--timeout",
        "600s",
        check=False,
        timeout=700,
    )
    started = time.monotonic()
    ready_error = ""
    try:
        wait_until(
            harness.workloads_ready,
            timeout_s=POST_UPGRADE_READY_MAX_S,
            what="Api and Worker ready after the rollback",
            interval_s=3.0,
        )
    except AssertionError as exc:
        ready_error = str(exc)
    ready_s = None if ready_error else time.monotonic() - started
    history = json.loads(
        harness.helm("history", RELEASE, "-n", PLATFORM_NAMESPACE, "-o", "json").stdout
    )
    attachable: list[bool] = []
    for handle in harness.sessions:
        try:
            attachable.append(
                wait_until(
                    lambda h=handle: harness.terminal_round_trip(h),
                    timeout_s=POST_UPGRADE_SESSIONS_MAX_S,
                    what=f"attachable terminal of {handle.session_id}",
                )
            )
        except AssertionError:
            attachable.append(False)
    return RollbackStage(
        exit_code=done.returncode,
        output=done.stdout + done.stderr,
        baseline_revision=baseline,
        history=history,
        deployed_revision=harness.deployed_revision(),
        deployments=harness.deployment_snapshot(),
        ready_s=ready_s,
        ready_error=ready_error,
        pods={s.session_id: p for s in harness.sessions if (p := harness.lab_pod(s.session_id))},
        statuses=[harness.session_status(s) for s in harness.sessions],
        attachable=attachable,
        active_count=_active_count(harness),
    )


@pytest.fixture(scope="module")
def rollback_run(
    kind_context: str, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[RollbackRun]:
    if shutil.which("helm") is None:
        pytest.skip("helm is not on PATH")
    try:
        previous = resolve_previous_release()
        workdir = tmp_path_factory.mktemp("rollback")
        previous_chart = export_chart(previous.ref, workdir)
        old = previous_digests(previous_chart)
        new = candidate_digests(old)
        candidate_schema = candidate_schema_version()
    except ReleaseNotResolvable as exc:
        pytest.skip(f"rollback test prerequisites missing: {exc}")

    harness = UpgradeHarness(kind_context, workdir)
    try:
        harness.install(previous_chart, old)
        schema_baseline = harness.schema_version()
        harness.start_sessions(3)
        run = RollbackRun(
            harness=harness,
            previous_digests=old,
            candidate_schema=candidate_schema,
            schema_baseline=schema_baseline,
            revision_baseline=harness.deployed_revision(),
            active_before=_active_count(harness),
            pods_before={s.session_id: s.pod for s in harness.sessions if s.pod},
        )

        # ---- verified upgrade, then rollback to the baseline revision (Req 25.9)
        proc = harness.start_upgrade(CHART, new)
        run.upgrade_output, _ = proc.communicate(timeout=900)
        run.upgrade_exit = proc.returncode
        if run.upgrade_exit == 0:
            try:
                wait_until(
                    lambda: (
                        harness.schema_version() == candidate_schema
                        and harness.workloads_ready()
                        and {s.session_id: harness.lab_pod(s.session_id) for s in harness.sessions}
                        == run.pods_before
                    ),
                    timeout_s=POST_UPGRADE_READY_MAX_S,
                    what="verified upgrade (candidate schema, ready pods, same Lab_Pods)",
                    interval_s=3.0,
                )
                run.upgrade_verified = True
            except AssertionError as exc:
                run.upgrade_error = str(exc)
        if run.upgrade_verified:
            run.after_upgrade = _rollback(harness, run.revision_baseline)

            # ---- failed upgrade, then rollback (Req 21.7 rollback path of the Deployment_Guide)
            run.active_before_failed = _active_count(harness)
            baseline_two = harness.deployed_revision()
            failing = os.environ.get(ENV_FAILING_MIGRATION_DIGEST)
            if failing:
                run.failed_upgrade_mode = "failing-migration"
                digests, extra = {**new, "migrations": failing}, []
            else:
                run.failed_upgrade_mode = "deadline"
                digests = {**new, "migrations": "sha256:" + "1" * 64}
                extra = ["migration.activeDeadlineSeconds=30"]
            proc = harness.start_upgrade(CHART, digests, extra, verify_signatures=False)
            run.failed_upgrade_output, _ = proc.communicate(timeout=900)
            run.failed_upgrade_exit = proc.returncode
            history = json.loads(
                harness.helm("history", RELEASE, "-n", PLATFORM_NAMESPACE, "-o", "json").stdout
            )
            latest = max(history, key=lambda e: int(e["revision"]))
            run.failed_revision_status = str(latest.get("status", ""))
            run.after_failed = _rollback(harness, baseline_two)
        yield run
    finally:
        harness.cleanup()


def _assert_stage(run: RollbackRun, stage: RollbackStage, active_expected: int) -> None:
    assert stage.exit_code == 0, stage.output[-3000:]
    entry = rollback_entry(stage.history, stage.baseline_revision)
    assert entry is not None, f"no rollback to {stage.baseline_revision}: {stage.history}"
    assert int(entry["revision"]) == stage.deployed_revision
    assert images_match(stage.deployments, run.previous_digests)
    assert stage.ready_s is not None, stage.ready_error
    assert stage.active_count == active_expected
    assert stage.statuses == ["ready"] * len(run.harness.sessions)
    assert all(stage.attachable), "a Lab_Session terminal is not attachable after the rollback"
    assert stage.pods == run.pods_before, "a Lab_Pod was replaced or restarted by the rollback"


# Claims: 25.9
@pytest.mark.kind
def test_the_upgrade_is_verified_before_it_is_rolled_back(rollback_run: RollbackRun) -> None:
    assert rollback_run.active_before == 3
    assert rollback_run.upgrade_exit == 0, rollback_run.upgrade_output[-3000:]
    assert rollback_run.upgrade_verified, rollback_run.upgrade_error


# Claims: 21.7, 25.9
@pytest.mark.kind
def test_helm_rollback_returns_the_release_to_the_baseline_revision(
    rollback_run: RollbackRun,
) -> None:
    stage = rollback_run.after_upgrade
    assert stage is not None, "the verified upgrade did not complete"
    assert stage.exit_code == 0, stage.output[-3000:]
    entry = rollback_entry(stage.history, rollback_run.revision_baseline)
    assert entry is not None, stage.history
    assert int(entry["revision"]) == stage.deployed_revision
    assert images_match(stage.deployments, rollback_run.previous_digests)
    assert stage.ready_s is not None, stage.ready_error


# Claims: 21.7, 25.9
@pytest.mark.kind
def test_active_lab_session_count_equals_the_count_recorded_before_the_upgrade(
    rollback_run: RollbackRun,
) -> None:
    stage = rollback_run.after_upgrade
    assert stage is not None, "the verified upgrade did not complete"
    assert stage.active_count == rollback_run.active_before


# Claims: 21.8, 25.9
@pytest.mark.kind
def test_each_lab_session_stays_active_and_attachable_after_the_rollback(
    rollback_run: RollbackRun,
) -> None:
    stage = rollback_run.after_upgrade
    assert stage is not None, "the verified upgrade did not complete"
    _assert_stage(rollback_run, stage, rollback_run.active_before)


# Claims: 21.7
@pytest.mark.kind
def test_a_failed_upgrade_leaves_a_failed_revision_and_all_sessions_active(
    rollback_run: RollbackRun,
) -> None:
    assert rollback_run.after_upgrade is not None, "the verified upgrade did not complete"
    assert rollback_run.failed_upgrade_exit != 0, rollback_run.failed_upgrade_output[-3000:]
    assert rollback_run.failed_revision_status == "failed"
    assert rollback_run.active_before_failed == rollback_run.active_before


# Claims: 21.7
@pytest.mark.kind
def test_helm_rollback_after_a_failed_upgrade_restores_the_release_and_the_session_count(
    rollback_run: RollbackRun,
) -> None:
    stage = rollback_run.after_failed
    assert stage is not None, "the failed-upgrade rollback did not run"
    _assert_stage(rollback_run, stage, rollback_run.active_before_failed)
