"""Docker isolation and resource-limit tests for the Sandbox (hands-on-labs task 8.1).

Requirements 3.1 to 3.10, evidenced against a real kernel through ``SandboxProvider``.
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
from edu_it_domain.ports.sandbox import SandboxHandle, SandboxProvider

from tests.labs.sandbox_backend import (
    LEARNER_UID,
    backend_name,
    is_kubernetes,
    make_provider,
    provider_ready,
    skip_reason,
)

IMAGE = "edu-it/lab-linux-base:dev"
MIB = 1024 * 1024
#: Kubernetes: PIDs of the pod outside the learner container (pause, grader and owner helpers) plus
#: slack for processes that race the ceiling.
POD_OTHER_PIDS = 10
#: Devices the kernel adds to every network namespace once the matching tunnel module is loaded.
KERNEL_FALLBACK_TUNNELS = frozenset(
    {"tunl0", "sit0", "ip6tnl0", "gre0", "gretap0", "erspan0", "ip_vti0", "ip6_vti0", "ip6gre0"}
)


@dataclass(frozen=True)
class _Lab:
    image: str = IMAGE


def _daemon_ready() -> bool:
    return provider_ready(IMAGE)


pytestmark = [
    pytest.mark.docker,
    pytest.mark.skipif(not _daemon_ready(), reason=skip_reason()),
]


@pytest.fixture(scope="module")
def provider() -> SandboxProvider:
    return make_provider()


def _new(provider: SandboxProvider, name: str) -> SandboxHandle:
    return provider.provision(build_sandbox_spec(_Lab(), name))


@pytest.fixture
def sandbox(provider: SandboxProvider) -> Iterator[SandboxHandle]:
    handle = _new(provider, "pytest-iso")
    try:
        yield handle
    finally:
        provider.destroy(handle)


def _sh(
    provider: SandboxProvider,
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


def _assert_usable(provider: SandboxProvider, handle: SandboxHandle) -> None:
    assert provider.health(handle).healthy
    assert _sh(provider, handle, "echo ok").stdout.strip() == b"ok"


# -- 3.1 non-root -------------------------------------------------------------


def test_runs_as_non_root(provider: SandboxProvider, sandbox: SandboxHandle) -> None:
    assert (
        _sh(provider, sandbox, "id -u").stdout.strip() == str(LEARNER_UID[backend_name()]).encode()
    )
    # Root filesystem is read-only.
    assert _sh(provider, sandbox, "touch /etc/pwned").exit_code != 0


# -- 3.2 no network -----------------------------------------------------------


def test_outbound_connect_fails(provider: SandboxProvider, sandbox: SandboxHandle) -> None:
    res = _sh(provider, sandbox, "exec 3<>/dev/tcp/1.1.1.1/53", timeout_s=15)
    assert res.exit_code != 0
    ifaces = _sh(provider, sandbox, "ls /sys/class/net").stdout.split()
    if is_kubernetes():
        # Docker's ``network none`` has no Kubernetes equivalent: a pod always has its own
        # interface (and Calico a tunnel device). The equivalent control is the default-deny
        # NetworkPolicy of the labs namespace, so nothing leaves the pod: not the internet (above)
        # and not the cluster either (the API server's cluster IP below).
        assert set(ifaces) <= {b"lo", b"eth0", b"tunl0"}
        cluster = _sh(provider, sandbox, "exec 3<>/dev/tcp/10.96.0.1/443", timeout_s=15)
        assert cluster.exit_code != 0
    else:
        # ``network none``: only ``lo``. A host kernel with a tunnel module loaded (for example
        # Calico's ipip module, when a kind cluster shares the Docker VM kernel) adds its
        # fallback device (``tunl0``...) to every new network namespace; it is down, has no
        # address and no route, so it is not a network path. Anything else, or any of them up,
        # fails.
        assert b"lo" in ifaces
        flags = _sh(
            provider, sandbox, "for i in /sys/class/net/*; do echo ${i##*/} $(cat $i/flags); done"
        ).stdout.decode()
        for line in flags.splitlines():
            name, value = line.split()
            if name != "lo":
                assert name in KERNEL_FALLBACK_TUNNELS, name
                assert int(value, 16) & 0x1 == 0, f"{name} is up"


# -- 3.3 PID limit ------------------------------------------------------------


def test_fork_bomb_is_contained_by_pid_limit(
    provider: SandboxProvider, sandbox: SandboxHandle
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
    if is_kubernetes():
        # The Kubernetes limit is the kubelet ``podPidsLimit`` on the POD cgroup (the annotation
        # ``edu-it.io/pid-limit`` records it; ``test_lab_nodes_enforce_the_pod_pid_limit...`` in
        # tests/k8s proves the node setting). The learner container only sees its own cgroup, whose ``pids.max`` and
        # ``pids.events`` are not the pod's, so they are not asserted. The pod ceiling shows in the
        # count: the bomb asked for 3 x limit processes, and the pod stops it at the limit, less
        # the pod's own other processes (pause, grader and owner helpers).
        assert limit - POD_OTHER_PIDS <= int(out["cur"]) <= limit
        return
    assert int(out["max"]) == limit
    assert int(out["cur"]) <= limit
    assert int(out["cur"]) >= limit - 2  # the bomb really ran into the ceiling
    assert (
        int(out["ev_max"]) > 0
    )  # the kernel refused forks at the limit    _assert_usable(provider, sandbox)


# -- 3.4 memory limit ---------------------------------------------------------


def test_memory_loop_is_contained(provider: SandboxProvider, sandbox: SandboxHandle) -> None:
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
    provider: SandboxProvider, sandbox: SandboxHandle, path: str, quota: int
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


def test_cpu_spin_is_held_to_cpu_limit(provider: SandboxProvider, sandbox: SandboxHandle) -> None:
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
    provider: SandboxProvider, sandbox: SandboxHandle
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


def test_no_docker_socket_or_host_mounts(provider: SandboxProvider, sandbox: SandboxHandle) -> None:
    res = _sh(
        provider,
        sandbox,
        "[ ! -e /var/run/docker.sock ] && [ ! -e /run/docker.sock ] && "
        "find / -xdev -name 'docker.sock' 2>/dev/null; echo done",
    )
    assert res.stdout.strip() == b"done"
    if is_kubernetes():
        _assert_pod_has_no_host_access_and_limits(provider, sandbox)
    else:
        _assert_container_has_no_host_access_and_limits(provider, sandbox)


def _assert_container_has_no_host_access_and_limits(
    provider: SandboxProvider, sandbox: SandboxHandle
) -> None:
    """Docker: inspect the container (the daemon is Docker-specific)."""
    docker_client = provider._docker()  # type: ignore[attr-defined]
    inspect = docker_client.api.inspect_container(sandbox.sandbox_id)
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


def _assert_pod_has_no_host_access_and_limits(
    provider: SandboxProvider, sandbox: SandboxHandle
) -> None:
    """Kubernetes: the manifest the provider submits (admitted by restricted Pod Security)."""
    from edu_it_infrastructure.fakes.k8s_api import parse_quantity
    from edu_it_infrastructure.sandbox.k8s_pod_spec import build_lab_pod

    spec = build_sandbox_spec(_Lab(), sandbox.session_stable_id)
    pod = build_lab_pod(spec, provider._settings)  # type: ignore[attr-defined]
    pod_spec = pod["spec"]
    assert not any("hostPath" in v for v in pod_spec["volumes"])
    for flag in ("hostNetwork", "hostPID", "hostIPC"):
        assert not pod_spec.get(flag)
    learner = next(c for c in pod_spec["containers"] if c["name"] == "learner")
    assert not learner["securityContext"].get("privileged")
    limits = learner["resources"]["limits"]
    assert parse_quantity("memory", limits["memory"]) == cfg.SANDBOX_MEMORY_LIMIT
    assert parse_quantity("cpu", limits["cpu"]) == cfg.SANDBOX_CPU_LIMIT * 1000
    assert pod["metadata"]["annotations"]["edu-it.io/pid-limit"] == str(cfg.SANDBOX_PID_LIMIT)


# -- 3.9 / 3.10 other sandboxes and the host are unaffected -------------------


def test_abuse_does_not_affect_other_sandboxes_or_host(provider: SandboxProvider) -> None:
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
        if not is_kubernetes():  # the Docker daemon is the host-side service to check
            assert provider._docker().ping()  # type: ignore[attr-defined]
        assert victim in provider.list_sandboxes()
        assert abuser in provider.list_sandboxes()
    finally:
        provider.destroy(victim)
        provider.destroy(abuser)
    assert socket.gethostname()  # host process is alive and functional
