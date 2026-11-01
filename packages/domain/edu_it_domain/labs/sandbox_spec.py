"""Pure construction of the hardened ``SandboxSpec`` (Req 1.1, 1.6, 3.1-3.5).

No I/O and no framework: the Docker translation lives in infrastructure.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from edu_it_domain import config as _cfg
from edu_it_domain.labs.models import SandboxSpec, TmpfsMount, WritableMounts

LEARNER_USER = "learner"
WORKSPACE_PATH = "/workspace"
HOME_PATH = "/home/learner"
TMP_PATH = "/tmp"
GRADER_PATH = "/grader"

_QUOTA_WEIGHTS = (
    _cfg.SANDBOX_QUOTA_WORKSPACE,
    _cfg.SANDBOX_QUOTA_HOME,
    _cfg.SANDBOX_QUOTA_TMP,
    _cfg.SANDBOX_QUOTA_GRADER,
)


class HasImage(Protocol):
    """The part of a lab definition the spec needs."""

    @property
    def image(self) -> str: ...


@dataclass(frozen=True, slots=True)
class SandboxLimits:
    """The four resource limits, defaulting to the Config constants."""

    cpu: int = _cfg.SANDBOX_CPU_LIMIT
    memory_bytes: int = _cfg.SANDBOX_MEMORY_LIMIT
    pids: int = _cfg.SANDBOX_PID_LIMIT
    writable_quota_bytes: int = _cfg.SANDBOX_WRITABLE_QUOTA


def split_writable_quota(total_bytes: int) -> WritableMounts:
    """Split ``total_bytes`` across the four mounts; parts sum exactly to it.

    Proportions follow the Config split. Floor shares are computed and the
    remainder goes to ``/workspace``, so the sum is exact.
    """

    if total_bytes < 0:
        raise ValueError("total_bytes must be non-negative")
    weight_sum = sum(_QUOTA_WEIGHTS)
    home, tmp, grader = (total_bytes * w // weight_sum for w in _QUOTA_WEIGHTS[1:])
    workspace = total_bytes - home - tmp - grader
    return WritableMounts(workspace=workspace, home=home, tmp=tmp, grader=grader)


def build_sandbox_spec(
    lab: HasImage,
    session_id: str,
    config: SandboxLimits | None = None,
) -> SandboxSpec:
    """Build the hardened spec: non-root, no network, limits, no host mounts."""

    limits = config or SandboxLimits()
    mounts = split_writable_quota(limits.writable_quota_bytes)
    return SandboxSpec(
        session_stable_id=session_id,
        image=lab.image,
        user=LEARNER_USER,
        network_enabled=False,
        cpu_limit=limits.cpu,
        memory_limit_bytes=limits.memory_bytes,
        pid_limit=limits.pids,
        writable_quota_bytes=limits.writable_quota_bytes,
        drop_all_capabilities=True,
        no_new_privileges=True,
        read_only_root=True,
        host_mounts=(),
        tmpfs_mounts=(
            TmpfsMount(WORKSPACE_PATH, mounts.workspace),
            TmpfsMount(HOME_PATH, mounts.home),
            TmpfsMount(TMP_PATH, mounts.tmp),
            TmpfsMount(GRADER_PATH, mounts.grader),
        ),
        labels=(("edu-it.managed", "1"), ("edu-it.session", session_id)),
    )
