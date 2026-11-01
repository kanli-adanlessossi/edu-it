"""Docker lifecycle tests: idle expiry, TTL and the reaper (hands-on-labs task 8.2).

Requirements 7.2, 7.3, 7.9, 8.6 against ``DockerSandboxProvider``. The real ``ProvisionSandbox``
and ``ReapSessions`` use cases run over in-memory repositories and the ``Test_Clock``; the
container is real. Numbers come from ``edu_it_domain.config``. Tests carry the ``docker`` marker
and skip when no daemon or no local ``edu-it/lab-linux-base:dev`` image is available.

The reaper destroys every listed sandbox it does not know, so the provider handed to it is scoped
to the containers this module created; other suites' containers are never touched.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from datetime import timedelta

import pytest
from edu_it_application.labs.provision_sandbox import ProvisionOutcome, ProvisionSandbox
from edu_it_application.labs.reap_sessions import ReapSessions
from edu_it_domain import config as cfg
from edu_it_domain.labs.models import LabSession, SessionStatus
from edu_it_domain.ports.lab_catalog import LabDefinition
from edu_it_domain.ports.sandbox import SandboxHandle
from edu_it_infrastructure.fakes import (
    Fake_LabCatalog,
    In_Memory_GradingRunRepository,
    In_Memory_LabSessionRepository,
    Test_Clock,
)
from edu_it_infrastructure.sandbox.docker_provider import DockerSandboxProvider

IMAGE = "edu-it/lab-linux-base:dev"
LAB_ID = "lifecycle-lab"
_LAB = LabDefinition(
    lab_id=LAB_ID, kind="terminal", image=IMAGE, concept_ids=(), tasks=(), checks=()
)


def _daemon_ready() -> bool:
    try:
        import docker

        client = docker.from_env(timeout=3)
        client.ping()
        client.images.get(IMAGE)
    except Exception:
        return False
    return True


pytestmark = [
    pytest.mark.docker,
    pytest.mark.skipif(not _daemon_ready(), reason="no Docker daemon or lab image"),
]


class _ScopedDockerProvider(DockerSandboxProvider):
    """Docker provider whose ``list_sandboxes`` only reports containers it provisioned."""

    def __init__(self) -> None:
        super().__init__()
        self.mine: set[str] = set()

    def provision(self, spec):  # type: ignore[no-untyped-def]
        handle = super().provision(spec)
        self.mine.add(handle.sandbox_id)
        return handle

    def list_sandboxes(self) -> Sequence[SandboxHandle]:
        return tuple(h for h in super().list_sandboxes() if h.sandbox_id in self.mine)


class _Env:
    def __init__(self, provider: _ScopedDockerProvider) -> None:
        self.clock = Test_Clock()
        self.provider = provider
        self.runs = In_Memory_GradingRunRepository()
        self.sessions = In_Memory_LabSessionRepository(self.runs)
        self.provisioner = ProvisionSandbox(
            sessions=self.sessions,
            catalog=Fake_LabCatalog([_LAB]),
            sandbox=provider,
            clock=self.clock,
        )
        self.reaper = ReapSessions(
            sessions=self.sessions, runs=self.runs, sandbox=provider, clock=self.clock
        )

    def start(self, sid: str, user: str) -> LabSession:
        now = self.clock.now()
        self.sessions.add_if_no_active(
            LabSession(
                stable_id=sid,
                user_id=user,
                lab_id=LAB_ID,
                status=SessionStatus.PROVISIONING,
                created_at=now,
                ttl_at=now + cfg.LAB_SESSION_TTL,
            )
        )
        assert self.provisioner.execute(sid) is ProvisionOutcome.READY
        return self.get(sid)

    def get(self, sid: str) -> LabSession:
        session = self.sessions.get(sid)
        assert session is not None
        return session

    def container_gone(self, container_id: str) -> bool:
        import docker

        try:
            self.provider._docker().containers.get(container_id)
        except docker.errors.NotFound:
            return True
        return False


@pytest.fixture(scope="module")
def provider() -> DockerSandboxProvider:
    return _ScopedDockerProvider()


@pytest.fixture
def env(provider: _ScopedDockerProvider) -> Iterator[_Env]:
    e = _Env(provider)
    try:
        yield e
    finally:  # leave no managed container behind, whatever the test did
        for container_id in list(provider.mine):
            provider.destroy(SandboxHandle(container_id, "cleanup", "docker"))
        provider.mine.clear()


def _assert_nothing_left(env: _Env, *container_ids: str) -> None:
    assert env.provider.list_sandboxes() == ()
    for cid in container_ids:
        assert env.container_gone(cid)


def test_idle_timeout_destroys_the_real_container(env: _Env) -> None:
    session = env.start("idle1", "u1")
    cid = session.sandbox_ref
    assert cid is not None and not env.container_gone(cid)

    # Just before the Idle_Timeout nothing happens (7.2 boundary).
    env.clock.advance(cfg.LAB_IDLE_TIMEOUT - timedelta(microseconds=1))
    report = env.reaper.execute()
    assert report.idle_expired == 0
    assert env.get("idle1").status is SessionStatus.READY
    assert not env.container_gone(cid)

    env.clock.advance(timedelta(microseconds=1))
    report = env.reaper.execute()
    assert report.idle_expired == 1
    assert env.get("idle1").status is SessionStatus.IDLE_EXPIRED
    _assert_nothing_left(env, cid)


def test_ttl_destroys_the_real_container_even_when_active(env: _Env) -> None:
    session = env.start("ttl1", "u1")
    cid = session.sandbox_ref
    assert cid is not None

    # Keep the learner active so only the TTL decides (7.3); at exactly ttl_at nothing is reaped.
    env.clock.set(session.ttl_at)
    env.sessions.touch_activity("ttl1", env.clock.now())
    env.reaper.execute()
    assert env.get("ttl1").status is SessionStatus.READY
    assert not env.container_gone(cid)

    env.clock.advance(timedelta(microseconds=1))
    env.sessions.touch_activity("ttl1", env.clock.now())
    report = env.reaper.execute()
    assert report.expired == 1
    assert env.get("ttl1").status is SessionStatus.EXPIRED
    _assert_nothing_left(env, cid)


def test_reaper_destroys_orphans_and_is_idempotent(env: _Env) -> None:
    ended = env.start("end1", "u1")
    live = env.start("live1", "u2")
    ended_cid, live_cid = ended.sandbox_ref, live.sandbox_ref
    assert ended_cid is not None and live_cid is not None
    env.sessions.transition("end1", expected={SessionStatus.READY}, to=SessionStatus.ENDED)

    first = env.reaper.execute()
    assert first.destroyed == 1
    assert env.container_gone(ended_cid) and not env.container_gone(live_cid)
    assert env.get("live1").status is SessionStatus.READY

    second = env.reaper.execute()  # a second tick changes nothing
    assert second.destroyed == 0 and second.destroy_failures == 0
    assert not env.container_gone(live_cid)

    env.clock.advance(cfg.LAB_IDLE_TIMEOUT)
    env.reaper.execute()
    _assert_nothing_left(env, ended_cid, live_cid)


def test_reaper_destroys_container_of_a_failed_session(env: _Env) -> None:
    session = env.start("gone1", "u1")
    cid = session.sandbox_ref
    assert cid is not None
    # The session is no longer active: the container is an orphan the reaper finds via the runtime.
    env.sessions.transition("gone1", expected={SessionStatus.READY}, to=SessionStatus.FAILED)

    report = env.reaper.execute()
    assert report.destroyed == 1
    _assert_nothing_left(env, cid)
