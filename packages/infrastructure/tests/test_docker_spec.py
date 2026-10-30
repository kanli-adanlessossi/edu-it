"""Unit tests for the pure Docker spec translator (Req 2.3, 3.1-3.5)."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from edu_it_domain.labs.sandbox_spec import build_sandbox_spec
from edu_it_infrastructure.sandbox.docker_spec import resolve_image, to_container_kwargs


@dataclass(frozen=True)
class _Lab:
    image: str = "edu-it/linux-base@sha256:abc"


@pytest.mark.parametrize(
    ("catalog", "ref"),
    [("linux-base", "edu-it/lab-linux-base:dev"), ("python", "edu-it/lab-python:dev")],
)
def test_catalog_image_names_map_to_the_bake_tags(catalog: str, ref: str) -> None:
    assert resolve_image(catalog) == ref
    assert to_container_kwargs(build_sandbox_spec(_Lab(catalog), "s"))["image"] == ref


def test_catalog_image_uses_the_configured_tag() -> None:
    kw = to_container_kwargs(build_sandbox_spec(_Lab("python"), "s"), image_tag="2024-01")
    assert kw["image"] == "edu-it/lab-python:2024-01"


@pytest.mark.parametrize(
    "ref",
    [
        "edu-it/lab-linux-base:dev",
        "edu-it/linux-base@sha256:abc",
        "registry.example/x/y:1",
        "python:3.12",
    ],
)
def test_full_docker_references_pass_through(ref: str) -> None:
    assert resolve_image(ref, "other") == ref


def test_invalid_tag_is_rejected_for_catalog_names() -> None:
    with pytest.raises(ValueError):
        resolve_image("python", "bad tag/..")


def test_kwargs_are_locked_down() -> None:
    spec = build_sandbox_spec(_Lab(), "sess-1")
    kw = to_container_kwargs(spec)

    assert kw["user"] == "1000:1000"
    assert kw["network_mode"] == "none"
    assert kw["nano_cpus"] == spec.cpu_limit * 1_000_000_000
    assert kw["mem_limit"] == kw["memswap_limit"] == spec.memory_limit_bytes
    assert kw["pids_limit"] == spec.pid_limit
    assert kw["read_only"] is True
    assert kw["cap_drop"] == ["ALL"]
    assert kw["security_opt"] == ["no-new-privileges:true"]
    assert kw["privileged"] is False
    assert kw["init"] is True
    assert kw["labels"]["edu-it.managed"] == "1"
    assert kw["labels"]["edu-it.session"] == "sess-1"
    for forbidden in ("binds", "volumes", "devices", "volumes_from"):
        assert forbidden not in kw


def test_tmpfs_sizes_sum_to_quota_and_grader_is_root_owned() -> None:
    spec = build_sandbox_spec(_Lab(), "sess-1")
    tmpfs = to_container_kwargs(spec)["tmpfs"]

    assert set(tmpfs) == {"/workspace", "/home/learner", "/tmp", "/grader"}
    total = sum(int(o.split(",")[0].removeprefix("size=")) for o in tmpfs.values())
    assert total == spec.writable_quota_bytes
    assert "uid=1000" in tmpfs["/workspace"]
    assert "uid=" not in tmpfs["/grader"]
    assert all("nosuid" in o and "nodev" in o for o in tmpfs.values())
