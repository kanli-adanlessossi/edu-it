"""Shared ``SandboxProvider`` contract suite (hands-on-labs task 2.6; Req 1.2, 1.4, 10.1).

One suite, run against ``Fake_SandboxProvider`` and (when a daemon and the local
``edu-it/lab-linux-base:dev`` image are available) ``DockerSandboxProvider``.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest
from edu_it_domain.labs.sandbox_spec import build_sandbox_spec
from edu_it_domain.ports.sandbox import SandboxGone, SandboxHandle, SandboxProvider
from edu_it_infrastructure.fakes import Fake_SandboxProvider, Test_Clock
from edu_it_infrastructure.sandbox.docker_provider import DockerSandboxProvider

IMAGE = "edu-it/lab-linux-base:dev"


@dataclass(frozen=True)
class _Lab:
    image: str = IMAGE


def _docker_ready() -> bool:
    try:
        import docker

        client = docker.from_env(timeout=3)
        client.ping()
        client.images.get(IMAGE)
    except Exception:
        return False
    return True


_DOCKER_READY = _docker_ready()

PROVIDERS = [
    pytest.param("fake", id="fake"),
    pytest.param(
        "docker",
        id="docker",
        marks=[
            pytest.mark.docker,
            pytest.mark.skipif(not _DOCKER_READY, reason="no Docker daemon or lab image"),
        ],
    ),
]


@pytest.fixture(params=PROVIDERS)
def kind(request: pytest.FixtureRequest) -> str:
    return str(request.param)


@pytest.fixture
def provider(kind: str) -> SandboxProvider:
    if kind == "docker":
        return DockerSandboxProvider()
    return Fake_SandboxProvider(Test_Clock(start=datetime(2024, 1, 1, tzinfo=UTC)))


@pytest.fixture
def sandbox(provider: SandboxProvider) -> Iterator[SandboxHandle]:
    handle = provider.provision(build_sandbox_spec(_Lab(), "contract-sess"))
    try:
        yield handle
    finally:
        provider.destroy(handle)


def test_provision_returns_handle_for_session(
    provider: SandboxProvider, sandbox: SandboxHandle, kind: str
) -> None:
    assert sandbox.provider == kind
    assert sandbox.session_stable_id == "contract-sess"
    assert sandbox in provider.list_sandboxes()
    assert provider.health(sandbox).healthy


def test_copy_in_to_grader_as_root_and_copy_out_round_trip(
    provider: SandboxProvider, sandbox: SandboxHandle, tmp_path: Path, kind: str
) -> None:
    payload = bytes(range(256))
    src = tmp_path / "check.bin"
    src.write_bytes(payload)

    provider.copy_in(sandbox, src, "/grader/check.bin", as_user="root")

    if kind == "docker":
        # /grader is a root-owned tmpfs and the copied file is root-owned.
        mounts = provider.exec(
            sandbox, ["/bin/cat", "/proc/mounts"], as_user="root", env={}, cwd="/"
        )
        assert any(
            line.split()[1:3] == ["/grader", "tmpfs"]
            for line in mounts.stdout.decode().splitlines()
            if len(line.split()) > 2
        )
        owner = provider.exec(
            sandbox, ["/bin/ls", "-l", "/grader/check.bin"], as_user="root", env={}, cwd="/"
        )
        assert owner.stdout.split()[2] == b"root"

    back = tmp_path / "out" / "check.bin"
    provider.copy_out(sandbox, "/grader/check.bin", back)
    assert back.read_bytes() == payload


def test_copy_out_missing_source_raises_file_not_found(
    provider: SandboxProvider, sandbox: SandboxHandle, tmp_path: Path
) -> None:
    with pytest.raises(FileNotFoundError):
        provider.copy_out(sandbox, "/workspace/missing", tmp_path / "nope")


def test_operations_after_destroy_raise_sandbox_gone(
    provider: SandboxProvider, tmp_path: Path
) -> None:
    handle = provider.provision(build_sandbox_spec(_Lab(), "contract-gone"))
    provider.destroy(handle)

    with pytest.raises(SandboxGone):
        provider.exec(handle, ["/bin/true"], as_user="learner", env={}, cwd="/workspace")
    with pytest.raises(SandboxGone):
        provider.attach_terminal(handle)
    assert not provider.health(handle).healthy


def test_destroy_is_idempotent(provider: SandboxProvider) -> None:
    handle = provider.provision(build_sandbox_spec(_Lab(), "contract-idem"))
    provider.destroy(handle)
    provider.destroy(handle)
    assert handle not in provider.list_sandboxes()


def test_list_sandboxes_tracks_live_sandboxes(provider: SandboxProvider) -> None:
    first = provider.provision(build_sandbox_spec(_Lab(), "contract-list-1"))
    second = provider.provision(build_sandbox_spec(_Lab(), "contract-list-2"))
    try:
        listed = provider.list_sandboxes()
        assert first in listed
        assert second in listed
        provider.destroy(first)
        listed = provider.list_sandboxes()
        assert first not in listed
        assert second in listed
    finally:
        provider.destroy(first)
        provider.destroy(second)
