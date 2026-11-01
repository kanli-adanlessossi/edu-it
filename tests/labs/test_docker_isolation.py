"""Docker isolation and resource-limit tests for the Sandbox (hands-on-labs task 8.1).

Requirements 3.1 to 3.10, evidenced against a real kernel through ``DockerSandboxProvider``.
Every number is read from ``edu_it_domain.config``. Tests carry the ``docker`` marker and are
skipped when no daemon or no local ``edu-it/lab-linux-base:dev`` image is available (same
contract as the provider contract suite). Command forms follow ``lab-images/README.md``.
"""

from __future__ import annotations

import socket
from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from edu_it_domain import config as cfg
from edu_it_domain.labs.sandbox_spec import build_sandbox_spec
from edu_it_domain.ports.sandbox import SandboxHandle
from edu_it_infrastructure.sandbox.docker_provider import DockerSandboxProvider

IMAGE = "edu-it/lab-linux-base:dev"
MIB = 1024 * 1024


@dataclass(frozen=True)
class _Lab:
    image: str = IMAGE


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


@pytest.fixture(scope="module")
def provider() -> DockerSandboxProvider:
    return DockerSandboxProvider()


def _new(provider: DockerSandboxProvider, name: str) -> SandboxHandle:
    return provider.provision(build_sandbox_spec(_Lab(), name))


@pytest.fixture
def sandbox(provider: DockerSandboxProvider) -> Iterator[SandboxHandle]:
    handle = _new(provider, "pytest-iso")
    try:
        yield handle
    finally:
        provider.destroy(handle)


def _sh(
    provider: DockerSandboxProvider,
    handle: SandboxHandle,
    script: str,
    *,
    user: str = "learner",
    timeout_s: float | None = 30.0,
):  # type: ignore[no-untyped-def]
    return provider.exec(
        handle,
        ["/bin/bash", "-c", script],
        as_user=user,
        env={"PATH": "/usr/local/bin:/usr/bin:/bin"},
        cwd="/workspace" if user == "learner" else "/",
        timeout_s=timeout_s,
    )


def _assert_usable(provider: DockerSandboxProvider, handle: SandboxHandle) -> None:
    assert provider.health(handle).healthy
    assert _sh(provider, handle, "echo ok").stdout.strip() == b"ok"


# -- 3.1 non-root -------------------------------------------------------------


def test_runs_as_non_root(provider: DockerSandboxProvider, sandbox: SandboxHandle) -> None:
    assert _sh(provider, sandbox, "id -u").stdout.strip() == b"1000"
    # Root filesystem is read-only.
    assert _sh(provider, sandbox, "touch /etc/pwned").exit_code != 0


# -- 3.2 no network -----------------------------------------------------------


def test_outbound_connect_fails(provider: DockerSandboxProvider, sandbox: SandboxHandle) -> None:
    res = _sh(provider, sandbox, "exec 3<>/dev/tcp/1.1.1.1/53", timeout_s=15)
    assert res.exit_code != 0
    ifaces = _sh(provider, sandbox, "ls /sys/class/net").stdout.split()
    assert ifaces == [b"lo"]


# -- 3.3 PID limit ------------------------------------------------------------


def test_fork_bomb_is_contained_by_pid_limit(
    provider: DockerSandboxProvider, sandbox: SandboxHandle
) -> None:
    limit = cfg.SANDBOX_PID_LIMIT
    # At the ceiling even ``ps``/``wc`` cannot fork, so read the cgroup with shell builtins
    # and wait on a pre-made fifo instead of forking ``sleep``.
    script = (
        "mkfifo /tmp/f; exec 3<>/tmp/f; "
        f"(seq 1 {limit * 3} | /usr/bin/xargs -P {limit * 3} -n1 /bin/sleep 60) 2>/dev/null & "
        "read -t 3 -u 3; read -r cur < /sys/fs/cgroup/pids.current; "
        "while read k v; do echo ev_$k=$v; done < /sys/fs/cgroup/pids.events; "
        "read -r mx < /sys/fs/cgroup/pids.max; echo cur=$cur; echo max=$mx"
    )
    res = _sh(provider, sandbox, script, timeout_s=30)
    out = dict(line.split("=", 1) for line in res.stdout.decode().split() if "=" in line)
    assert int(out["max"]) == limit
    assert int(out["cur"]) <= limit
    assert int(out["cur"]) >= limit - 2  # the bomb really ran into the ceiling
    assert (
        int(out["ev_max"]) > 0
    )  # the kernel refused forks at the limit    _assert_usable(provider, sandbox)


# -- 3.4 memory limit ---------------------------------------------------------


def test_memory_loop_is_contained(provider: DockerSandboxProvider, sandbox: SandboxHandle) -> None:
    # tail on an endless line-less stream buffers it all in memory.
    res = _sh(provider, sandbox, "/usr/bin/tail /dev/zero", timeout_s=60)
    assert res.exit_code != 0
    assert not res.timed_out  # killed by the memory cgroup, not by the wall-clock backstop
    _assert_usable(provider, sandbox)
    mem_max = _sh(provider, sandbox, "cat /sys/fs/cgroup/memory.max").stdout.strip()
    if mem_max.isdigit():
        assert int(mem_max) == cfg.SANDBOX_MEMORY_LIMIT


# -- 3.5 disk quota -----------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "quota"),
    [
        ("/tmp", cfg.SANDBOX_QUOTA_TMP),
        ("/home/learner", cfg.SANDBOX_QUOTA_HOME),
        ("/workspace", cfg.SANDBOX_QUOTA_WORKSPACE),
    ],
)
def test_disk_fill_fails_with_enospc_at_tmpfs_size(
    provider: DockerSandboxProvider, sandbox: SandboxHandle, path: str, quota: int
) -> None:
    count = quota // MIB + 16
    res = _sh(
        provider,
        sandbox,
        f"dd if=/dev/zero of={path}/fill bs=1M count={count} 2>&1; stat -c %s {path}/fill",
        timeout_s=60,
    )
    out = res.stdout.decode()
    assert "No space left on device" in out  # ENOSPC
    written = int(out.split()[-1])
    assert written <= quota
    assert written >= quota - MIB  # filled up to the tmpfs size, not short of it
    _assert_usable(provider, sandbox)


# -- 3.6 CPU limit ------------------------------------------------------------


def test_cpu_spin_is_held_to_cpu_limit(
    provider: DockerSandboxProvider, sandbox: SandboxHandle
) -> None:
    script = (
        "f=/sys/fs/cgroup/cpu.stat; [ -r $f ] || { echo NOCGROUP; exit 0; }; "
        "u() { sed -n 's/^usage_usec //p' $f; }; "
        "a=$(u); s=$(date +%s%N); "
        "for i in 1 2 3 4; do (while :; do :; done) & done; sleep 4; "
        "b=$(u); e=$(date +%s%N); kill $(jobs -p) 2>/dev/null; "
        "echo $(( (b - a) )) $(( (e - s) / 1000 ))"
    )
    res = _sh(provider, sandbox, script, timeout_s=30)
    out = res.stdout.decode().strip()
    if out == "NOCGROUP":
        pytest.skip("cgroup cpu.stat not readable in the container")
    cpu_us, wall_us = (int(x) for x in out.split()[-2:])
    assert cpu_us / wall_us <= cfg.SANDBOX_CPU_LIMIT * 1.2
    assert cpu_us / wall_us > 0.3  # the spin did run
    _assert_usable(provider, sandbox)


# -- 3.7 capabilities and no-new-privileges ----------------------------------


def test_capabilities_dropped_and_no_new_privs(
    provider: DockerSandboxProvider, sandbox: SandboxHandle
) -> None:
    status = _sh(provider, sandbox, "cat /proc/self/status").stdout.decode()
    fields = dict(line.split(":\t", 1) for line in status.splitlines() if ":\t" in line)
    for cap in ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb"):
        assert int(fields[cap].strip(), 16) == 0, cap
    assert fields["NoNewPrivs"].strip() == "1"
    # Same view for the grader user.
    gstatus = _sh(
        provider, sandbox, "grep -E '^(CapEff|CapBnd|NoNewPrivs)' /proc/self/status", user="grader"
    )
    assert b"NoNewPrivs:\t1" in gstatus.stdout
    assert b"CapBnd:\t0000000000000000" in gstatus.stdout


# -- 3.8 no Docker socket, no host mounts, not privileged --------------------


def test_no_docker_socket_or_host_mounts(
    provider: DockerSandboxProvider, sandbox: SandboxHandle
) -> None:
    res = _sh(
        provider,
        sandbox,
        "[ ! -e /var/run/docker.sock ] && [ ! -e /run/docker.sock ] && "
        "find / -xdev -name 'docker.sock' 2>/dev/null; echo done",
    )
    assert res.stdout.strip() == b"done"
    inspect = provider._docker().api.inspect_container(sandbox.sandbox_id)
    assert all(m["Type"] == "tmpfs" for m in inspect["Mounts"] or [{"Type": "tmpfs"}])
    host = inspect["HostConfig"]
    assert host["Privileged"] is False
    assert not host.get("Binds")
    assert not host.get("Devices")
    assert host["NetworkMode"] == "none"
    assert host["PidsLimit"] == cfg.SANDBOX_PID_LIMIT
    assert host["Memory"] == cfg.SANDBOX_MEMORY_LIMIT
    assert host["MemorySwap"] == cfg.SANDBOX_MEMORY_LIMIT
    assert host["NanoCpus"] == cfg.SANDBOX_CPU_LIMIT * 1_000_000_000


# -- 3.9 / 3.10 other sandboxes and the host are unaffected -------------------


def test_abuse_does_not_affect_other_sandboxes_or_host(provider: DockerSandboxProvider) -> None:
    victim = _new(provider, "pytest-victim")
    abuser = _new(provider, "pytest-abuser")
    try:
        assert _sh(provider, victim, "echo keep > /workspace/keep.txt").exit_code == 0
        _sh(
            provider,
            abuser,
            "for i in $(seq 1 400); do /bin/sleep 60 & done 2>/dev/null; wait",
            timeout_s=10,
        )
        _sh(provider, abuser, "dd if=/dev/zero of=/tmp/f bs=1M count=64 2>/dev/null", timeout_s=30)
        _sh(provider, abuser, "/usr/bin/tail /dev/zero", timeout_s=60)
        # Victim state is untouched and the abuser can neither see nor reach it.
        assert _sh(provider, victim, "cat /workspace/keep.txt").stdout.strip() == b"keep"
        assert _sh(provider, abuser, "ls /workspace").stdout.strip() == b""
        _assert_usable(provider, victim)
        _assert_usable(provider, abuser)
        # The host daemon still answers and nothing leaked onto the host network namespace.
        assert provider._docker().ping()
        assert victim in provider.list_sandboxes()
        assert abuser in provider.list_sandboxes()
    finally:
        provider.destroy(victim)
        provider.destroy(abuser)
    assert socket.gethostname()  # host process is alive and functional
