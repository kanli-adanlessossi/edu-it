"""Property test: sandbox specifications are always locked down."""

from __future__ import annotations

from dataclasses import dataclass

from edu_it_domain.labs.sandbox_spec import SandboxLimits, build_sandbox_spec
from edu_it_infrastructure.sandbox.docker_spec import resolve_image, to_container_kwargs
from hypothesis import given, settings
from hypothesis import strategies as st


@dataclass(frozen=True)
class _Lab:
    image: str


_images = st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789/-_.:@", min_size=1, max_size=60)
_session_ids = st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789-", min_size=1, max_size=40)
_limits = st.builds(
    SandboxLimits,
    cpu=st.integers(min_value=1, max_value=64),
    memory_bytes=st.integers(min_value=1, max_value=2**40),
    pids=st.integers(min_value=1, max_value=100_000),
    writable_quota_bytes=st.integers(min_value=0, max_value=2**40),
)


# Feature: hands-on-labs, Property 1: Sandbox specifications are always locked down
# **Validates: Requirements 1.6, 2.3, 3.1, 3.2, 3.3, 3.4, 3.5**
@settings(max_examples=200, deadline=None)
@given(image=_images, session_id=_session_ids, limits=_limits)
def test_sandbox_specs_are_always_locked_down(
    image: str, session_id: str, limits: SandboxLimits
) -> None:
    spec = build_sandbox_spec(_Lab(image), session_id, limits)
    kw = to_container_kwargs(spec)

    # Non-root, loopback-only, unprivileged, hardened.
    assert kw["user"] not in ("root", "0", "0:0")
    assert not kw["user"].startswith("0")
    assert kw["network_mode"] == "none"
    assert kw["privileged"] is False
    assert kw["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in kw["security_opt"]
    assert kw["read_only"] is True

    # Exactly the four Config limits.
    assert kw["nano_cpus"] == limits.cpu * 1_000_000_000
    assert kw["mem_limit"] == limits.memory_bytes
    assert kw["pids_limit"] == limits.pids
    tmpfs_total = sum(
        int(opts.split(",")[0].removeprefix("size=")) for opts in kw["tmpfs"].values()
    )
    assert tmpfs_total == limits.writable_quota_bytes

    # No host mounts, devices, or Docker socket.
    assert spec.host_mounts == ()
    for forbidden in ("binds", "volumes", "devices", "volumes_from", "mounts"):
        assert forbidden not in kw
    assert "docker.sock" not in repr(kw)
    assert kw["image"] == resolve_image(image)
    assert kw["labels"]["edu-it.session"] == session_id
