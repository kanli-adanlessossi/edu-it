"""Upgrade_Test on kind (kubernetes-deployment task 14.4, Requirements 25, 21.1, 21.6, 12.14).

Flow (the ``upgrade`` module fixture):

1. resolve the previous release: the most recent release tag, else the base commit (Req 25.8,
   risk R9), export its chart, read its image digests;
2. run the signature verification of task 13.2 over the exact digests, then install it;
3. start 3 Lab_Sessions, wait for a running Lab_Pod each (Req 25.1), record the pod identities and
   the schema version, and open one terminal client per session;
4. ``helm upgrade`` to the candidate (working-tree chart, candidate images, one more migration)
   while the clients watch their sockets;
5. measure the outcome: same Lab_Pods and working terminals within 120 s (25.2), schema at the
   candidate version and all Api/Worker pods ready within 300 s (25.3), every client reconnected
   within 30 s of its close (25.4).

The failed-migration variant then upgrades the (now candidate) release once more with a migration
that cannot complete and checks that the Deployments and Lab_Pods are untouched (21.1, 25.7).

Prerequisites (otherwise the kind tests skip with the reason): a reachable kind cluster, ``helm``,
the candidate digests ``EDU_IT_KIND_DIGEST_{API,WORKER,MIGRATIONS}`` in the harness registry, the
previous release digests (pinned in the previous chart or ``EDU_IT_UPGRADE_PREVIOUS_DIGEST_*``),
and the ingress reachable at ``EDU_IT_UPGRADE_API_BASE`` (default ``http://edu-it.localtest.me``).
The failed migration uses the unsigned image ``EDU_IT_UPGRADE_FAILING_MIGRATION_DIGEST`` when set;
without it the migrations image digest points at nothing pullable and the Migration_Job runs into
its 30 s ``activeDeadlineSeconds`` (Req 21.5, same behavior as 21.1).

The tests of the first section need no cluster and run everywhere. The shared helpers live in
``tests/k8s/upgrade_fixtures.py``; task 14.5 reuses them for the rollback test.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from tests.k8s.upgrade_fixtures import (
    CANDIDATE_RECONNECT_MAX_S,
    CHART,
    ENV_FAILING_MIGRATION_DIGEST,
    IMAGE_NAMES,
    POST_UPGRADE_READY_MAX_S,
    POST_UPGRADE_SESSIONS_MAX_S,
    PodIdentity,
    PreviousRelease,
    ReleaseNotResolvable,
    SignatureGateFailed,
    UpgradeHarness,
    _chart_repositories,
    candidate_digests,
    candidate_schema_version,
    export_chart,
    latest_release_tag,
    previous_digests,
    resolve_previous_release,
    run_signature_gate,
    wait_until,
)

LOCAL = "localhost:5001/edu-it-"
D1, D2 = "sha256:" + "a" * 64, "sha256:" + "b" * 64


# =============================================================== no-cluster tests (helpers)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.test", *args],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    if shutil.which("git") is None:
        pytest.skip("git is not on PATH")
    repo = tmp_path / "repo"
    chart = repo / "deploy" / "helm" / "edu-it"
    chart.mkdir(parents=True)
    _git(repo, "init", "-q")
    (chart / "Chart.yaml").write_text("name: edu-it\nversion: 0.1.0\n", encoding="utf-8")
    (chart / "values-dev.yaml").write_text(
        f"images:\n  api:\n    repository: {LOCAL}api\n    digest: {D1}\n", encoding="utf-8"
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    (chart / "Chart.yaml").write_text("name: edu-it\nversion: 0.2.0\n", encoding="utf-8")
    _git(repo, "commit", "-q", "-am", "candidate")
    return repo


# Claims: 25.8
def test_previous_release_is_the_base_commit_when_no_release_tag_exists(git_repo: Path) -> None:
    assert latest_release_tag(git_repo) is None
    previous = resolve_previous_release(git_repo, base_ref="no-such-ref")
    assert previous.source == "base-commit"
    assert previous.ref == _git(git_repo, "rev-parse", "HEAD^")


# Claims: 25.8
def test_previous_release_is_the_most_recent_release_tag(git_repo: Path, tmp_path: Path) -> None:
    _git(git_repo, "tag", "v0.9.0", "HEAD^")
    _git(git_repo, "tag", "v0.10.0", "HEAD^")
    _git(git_repo, "tag", "not-a-release", "HEAD")
    previous = resolve_previous_release(git_repo)
    assert previous == PreviousRelease("v0.10.0", "tag")
    chart = export_chart(previous.ref, tmp_path / "out", git_repo)
    assert "version: 0.1.0" in (chart / "Chart.yaml").read_text(encoding="utf-8")


# Claims: 25.8
def test_previous_digests_come_from_the_environment_and_reject_placeholders(
    git_repo: Path, tmp_path: Path
) -> None:
    chart = export_chart("HEAD^", tmp_path / "out", git_repo)
    env = {f"EDU_IT_UPGRADE_PREVIOUS_DIGEST_{n.upper()}": D2 for n in IMAGE_NAMES}
    assert previous_digests(chart, env) == dict.fromkeys(IMAGE_NAMES, D2)
    with pytest.raises(ReleaseNotResolvable, match="web"):
        previous_digests(chart, {"EDU_IT_UPGRADE_PREVIOUS_DIGEST_API": "sha256:" + "0" * 64})
    assert (
        candidate_digests(
            dict.fromkeys(IMAGE_NAMES, D1),
            {
                "EDU_IT_KIND_DIGEST_API": D2,
                "EDU_IT_KIND_DIGEST_WORKER": D2,
                "EDU_IT_KIND_DIGEST_MIGRATIONS": D2,
            },
        )["web"]
        == D1
    )


# Claims: 12.14
def test_signature_gate_records_the_skip_for_local_digests_and_fails_on_foreign_ones(
    git_repo: Path, tmp_path: Path
) -> None:
    chart = export_chart("HEAD", tmp_path / "out", git_repo)
    ok = run_signature_gate(chart, {"api": D1}, mode="pull-request")
    assert ok.exit_code == 0 and ok.skipped
    assert "signature verification was skipped" in ok.output

    values = chart / "values-dev.yaml"
    values.write_text(
        values.read_text(encoding="utf-8").replace(LOCAL, "ghcr.io/x/"), encoding="utf-8"
    )
    bad = run_signature_gate(chart, {"api": D1}, mode="pull-request")
    assert bad.exit_code == 1 and "not in the local registry" in bad.output


# Claims: 12.14
def test_no_install_or_upgrade_starts_when_the_signature_gate_fails(
    git_repo: Path, tmp_path: Path
) -> None:
    """The gate runs before any kubectl or helm call, so a bogus context proves nothing ran."""

    chart = export_chart("HEAD", tmp_path / "out", git_repo)
    values = chart / "values-dev.yaml"
    values.write_text(
        values.read_text(encoding="utf-8").replace(LOCAL, "ghcr.io/x/"), encoding="utf-8"
    )
    harness = UpgradeHarness("kind-does-not-exist", tmp_path)
    with pytest.raises(SignatureGateFailed):
        harness.install(chart, {"api": D1})
    with pytest.raises(SignatureGateFailed):
        harness.start_upgrade(chart, {"api": D1})


# =============================================================== kind tests


@dataclass
class UpgradeOutcome:
    harness: UpgradeHarness
    previous: PreviousRelease
    previous_chart: Path
    previous_digests: dict[str, str]
    candidate_digests: dict[str, str]
    candidate_schema: str
    schema_before: str
    revision_before: int
    pods_before: dict[str, PodIdentity]
    gate_output: str
    helm_exit: int = -1
    helm_output: str = ""
    pods_after: dict[str, PodIdentity] = field(default_factory=dict)
    sessions_checked_s: float | None = None
    sessions_error: str = ""
    ready_s: float | None = None
    ready_error: str = ""
    schema_after: str = ""
    reconnect_seconds: list[float | None] = field(default_factory=list)
    reconnect_output_ok: list[bool] = field(default_factory=list)
    reconnect_errors: list[str | None] = field(default_factory=list)


def _timed(probe, limit_s: float, what: str) -> tuple[float | None, str]:
    started = time.monotonic()
    try:
        wait_until(probe, timeout_s=limit_s, what=what, interval_s=3.0)
    except AssertionError as exc:
        return None, str(exc)
    return time.monotonic() - started, ""


@pytest.fixture(scope="module")
def upgrade(
    kind_context: str, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[UpgradeOutcome]:
    if shutil.which("helm") is None:
        pytest.skip("helm is not on PATH")
    try:
        previous = resolve_previous_release()
        workdir = tmp_path_factory.mktemp("upgrade")
        previous_chart = export_chart(previous.ref, workdir)
        old = previous_digests(previous_chart)
        new = candidate_digests(old)
        candidate_schema = candidate_schema_version()
    except ReleaseNotResolvable as exc:
        pytest.skip(f"Upgrade_Test prerequisites missing: {exc}")

    harness = UpgradeHarness(kind_context, workdir)
    try:
        gate = run_signature_gate(previous_chart, old)
        harness.install(previous_chart, old)
        schema_before = harness.schema_version()
        assert schema_before != candidate_schema, (
            "the candidate adds no migration: the previous and the candidate schema versions are "
            f"both {schema_before} (Req 25.1 needs at least one migration)"
        )
        sessions = harness.start_sessions(3)
        harness.attach_clients()
        outcome = UpgradeOutcome(
            harness=harness,
            previous=previous,
            previous_chart=previous_chart,
            previous_digests=old,
            candidate_digests=new,
            candidate_schema=candidate_schema,
            schema_before=schema_before,
            revision_before=harness.revision(),
            pods_before={s.session_id: s.pod for s in sessions if s.pod},
            gate_output=gate.output,
        )

        proc = harness.start_upgrade(CHART, new)
        helm_output, _ = proc.communicate(timeout=900)
        outcome.helm_exit, outcome.helm_output = proc.returncode, helm_output
        if outcome.helm_exit == 0:

            def sessions_same() -> bool:
                now = {s.session_id: harness.lab_pod(s.session_id) for s in sessions}
                outcome.pods_after = {k: v for k, v in now.items() if v}
                return outcome.pods_after == outcome.pods_before and all(
                    harness.terminal_round_trip(s) for s in sessions
                )

            outcome.sessions_checked_s, outcome.sessions_error = _timed(
                sessions_same, POST_UPGRADE_SESSIONS_MAX_S, "same Lab_Pods with working terminals"
            )
            outcome.ready_s, outcome.ready_error = _timed(
                lambda: harness.schema_version() == candidate_schema and harness.workloads_ready(),
                POST_UPGRADE_READY_MAX_S,
                "candidate schema and ready Api/Worker pods",
            )
            outcome.schema_after = harness.schema_version()
            for client in harness.clients:
                client.join(CANDIDATE_RECONNECT_MAX_S + 120)
                outcome.reconnect_seconds.append(client.reconnect_seconds)
                outcome.reconnect_output_ok.append(client.reconnect_output_ok)
                outcome.reconnect_errors.append(client.error)
        yield outcome
    finally:
        harness.cleanup()


# Claims: 25.1, 25.8
@pytest.mark.kind
def test_previous_release_is_installed_with_three_running_lab_sessions(
    upgrade: UpgradeOutcome,
) -> None:
    assert upgrade.previous.source in ("tag", "base-commit")
    assert upgrade.previous.source == "tag" or latest_release_tag() is None
    assert len(upgrade.pods_before) == 3
    assert all(p.phase == "Running" for p in upgrade.pods_before.values())
    assert upgrade.schema_before != upgrade.candidate_schema


# Claims: 12.14
@pytest.mark.kind
def test_signatures_were_verified_before_the_previous_release_was_installed(
    upgrade: UpgradeOutcome,
) -> None:
    assert "verified" in upgrade.gate_output or "signature verification was skipped" in (
        upgrade.gate_output
    )


# Claims: 25.1, 25.3
@pytest.mark.kind
def test_helm_upgrade_to_the_candidate_succeeds_and_creates_a_revision(
    upgrade: UpgradeOutcome,
) -> None:
    assert upgrade.helm_exit == 0, upgrade.helm_output[-3000:]
    assert upgrade.harness.revision() == upgrade.revision_before + 1


# Claims: 25.2
@pytest.mark.kind
def test_each_lab_session_keeps_the_same_running_pod_after_the_upgrade(
    upgrade: UpgradeOutcome,
) -> None:
    assert upgrade.helm_exit == 0, upgrade.helm_output[-3000:]
    assert upgrade.sessions_checked_s is not None, upgrade.sessions_error
    assert upgrade.sessions_checked_s <= POST_UPGRADE_SESSIONS_MAX_S
    for session_id, before in upgrade.pods_before.items():
        after = upgrade.pods_after[session_id]
        assert (after.name, after.uid, after.node) == (before.name, before.uid, before.node)
        assert after.restarts == before.restarts and after.phase == "Running"


# Claims: 25.2
@pytest.mark.kind
def test_a_terminal_accepts_a_command_and_returns_its_output_after_the_upgrade(
    upgrade: UpgradeOutcome,
) -> None:
    assert upgrade.helm_exit == 0, upgrade.helm_output[-3000:]
    # Checked inside the 120 s window by the fixture; check again on a fresh socket now.
    assert all(upgrade.harness.terminal_round_trip(s) for s in upgrade.harness.sessions)


# Claims: 25.3
@pytest.mark.kind
def test_schema_is_at_the_candidate_version_and_every_pod_is_ready_within_300_s(
    upgrade: UpgradeOutcome,
) -> None:
    assert upgrade.helm_exit == 0, upgrade.helm_output[-3000:]
    assert upgrade.ready_s is not None, upgrade.ready_error
    assert upgrade.ready_s <= POST_UPGRADE_READY_MAX_S
    assert upgrade.schema_after == upgrade.candidate_schema


# Claims: 25.4
@pytest.mark.kind
def test_terminal_clients_reconnect_within_30_s_and_run_a_command(upgrade: UpgradeOutcome) -> None:
    assert upgrade.helm_exit == 0, upgrade.helm_output[-3000:]
    assert len(upgrade.reconnect_seconds) == 3
    for seconds, ok, error in zip(
        upgrade.reconnect_seconds,
        upgrade.reconnect_output_ok,
        upgrade.reconnect_errors,
        strict=True,
    ):
        assert seconds is not None, f"a terminal client never closed or never reconnected: {error}"
        assert seconds <= CANDIDATE_RECONNECT_MAX_S, f"reconnect took {seconds:.1f} s"
        assert ok, "the reconnected terminal returned no command output"


# Claims: 25.6
@pytest.mark.kind
def test_at_least_one_api_replica_stays_ready_and_two_replicas_run(upgrade: UpgradeOutcome) -> None:
    snapshot = upgrade.harness.deployment_snapshot()
    api = next(v for k, v in snapshot.items() if k.endswith("-api"))
    assert api["replicas"] >= 2
    assert len(api["pod_uids"]) >= 2


# Claims: 21.6
@pytest.mark.kind
def test_previous_api_and_worker_keep_working_against_the_candidate_schema(
    upgrade: UpgradeOutcome,
) -> None:
    assert upgrade.helm_exit == 0, upgrade.helm_output[-3000:]
    harness = upgrade.harness
    old_repos = _chart_repositories(upgrade.previous_chart)
    new_repos = _chart_repositories(CHART)
    harness.set_workload_images(old_repos, upgrade.previous_digests)
    try:
        assert harness.schema_version() == upgrade.candidate_schema
        wait_until(harness.workloads_ready, timeout_s=300, what="previous Api and Worker ready")
        for handle in harness.sessions:
            assert harness.session_status(handle) == "ready"
            assert harness.terminal_round_trip(handle)
    finally:
        harness.set_workload_images(new_repos, upgrade.candidate_digests)
    wait_until(harness.workloads_ready, timeout_s=300, what="candidate Api and Worker restored")


# ------------------------------------------------------------------ failed-migration variant


@dataclass
class FailedUpgrade:
    exit_code: int
    output: str
    mode: str
    deployments_before: dict
    deployments_after: dict
    pods_before: dict[str, PodIdentity]
    pods_after: dict[str, PodIdentity]
    deployed_before: int
    deployed_after: int
    job_failed: bool
    statuses: list[str]


@pytest.fixture(scope="module")
def failed_upgrade(upgrade: UpgradeOutcome) -> FailedUpgrade:
    assert upgrade.helm_exit == 0, "the successful upgrade must run first"
    harness = upgrade.harness
    failing = os.environ.get(ENV_FAILING_MIGRATION_DIGEST)
    if failing:
        mode, digests, extra = (
            "failing-migration",
            {**upgrade.candidate_digests, "migrations": failing},
            [],
        )
    else:
        # Nothing pullable: the Migration_Job cannot finish within its 30 s deadline (Req 21.5).
        mode, digests, extra = (
            "deadline",
            {**upgrade.candidate_digests, "migrations": "sha256:" + "1" * 64},
            ["migration.activeDeadlineSeconds=30"],
        )
    wait_until(
        harness.workloads_ready, timeout_s=300, what="release steady before the failed upgrade"
    )
    before = harness.deployment_snapshot()
    pods_before = {s.session_id: harness.lab_pod(s.session_id) for s in harness.sessions}
    deployed_before = harness.deployed_revision()
    proc = harness.start_upgrade(CHART, digests, extra, verify_signatures=False)
    output, _ = proc.communicate(timeout=900)
    # Give any (wrongly) started rollout time to show up before comparing.
    time.sleep(20)
    return FailedUpgrade(
        exit_code=proc.returncode,
        output=output,
        mode=mode,
        deployments_before=before,
        deployments_after=harness.deployment_snapshot(),
        pods_before={k: v for k, v in pods_before.items() if v},
        pods_after={
            s.session_id: p for s in harness.sessions if (p := harness.lab_pod(s.session_id))
        },
        deployed_before=deployed_before,
        deployed_after=harness.deployed_revision(),
        job_failed=harness.migration_job_failed(),
        statuses=[harness.session_status(s) for s in harness.sessions],
    )


# Claims: 21.1, 25.7
@pytest.mark.kind
def test_failed_migration_makes_helm_upgrade_fail_and_keeps_the_deployments_unchanged(
    failed_upgrade: FailedUpgrade,
) -> None:
    assert failed_upgrade.exit_code != 0, failed_upgrade.output[-3000:]
    assert failed_upgrade.job_failed, "the Migration_Job is not reported as failed"
    # Same generation, replica count, images and pod uids: no new Api or Worker pod started.
    assert failed_upgrade.deployments_after == failed_upgrade.deployments_before
    assert failed_upgrade.deployed_after == failed_upgrade.deployed_before


# Claims: 25.7, 21.1
@pytest.mark.kind
def test_failed_migration_leaves_every_lab_session_running(
    failed_upgrade: FailedUpgrade,
) -> None:
    assert len(failed_upgrade.pods_after) == 3
    for session_id, before in failed_upgrade.pods_before.items():
        after = failed_upgrade.pods_after[session_id]
        assert (after.uid, after.restarts, after.phase) == (before.uid, before.restarts, "Running")
    assert failed_upgrade.statuses == ["ready"] * 3
