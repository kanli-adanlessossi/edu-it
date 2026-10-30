"""Pure translation of a ``SandboxSpec`` into Docker container kwargs.

No Docker SDK import and no I/O, so the isolation settings can be property
tested without a daemon (Req 2.3, 3.1-3.5). The result is meant to be passed
to ``client.containers.create(**kwargs)`` and deliberately contains no binds,
volumes, devices, or Docker socket.
"""

from __future__ import annotations

import re
from typing import Any

from edu_it_domain.labs.models import SandboxSpec, TmpfsMount

#: Numeric uid:gid of the ``learner`` user baked into the Lab_Images.
LEARNER_UID_GID = "1000:1000"
_LEARNER_UID = 1000
_LEARNER_GID = 1000

_GRADER_PATH = "/grader"
_GRADER_GID = 1001
_NANO = 1_000_000_000

#: Repository prefix of the Lab_Images built from ``lab-images/<name>/`` (see ``docker-bake.hcl``).
LAB_IMAGE_REPOSITORY_PREFIX = "edu-it/lab-"
#: Tag the bake file gives locally built Lab_Images; override with ``LAB_IMAGE_TAG``.
DEFAULT_LAB_IMAGE_TAG = "dev"

_CATALOG_IMAGE_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_IMAGE_TAG = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}")

LABEL_MANAGED = "edu-it.managed"
LABEL_SESSION = "edu-it.session"


def _tmpfs_options(mount: TmpfsMount) -> str:
    """Docker tmpfs option string; ``/grader`` stays root-owned.

    ``/grader`` starts root:grader mode 0775. There is no CAP_CHOWN, so the Grader_User must create
    its own working directory ``/grader/run``; GradeRun does that after the verified freeze and
    then chmods ``/grader`` to 0755 before any Check runs. The learner is neither owner nor group
    member, and nothing is world-writable (Req 10.2)."""

    if mount.path == _GRADER_PATH:
        return f"size={mount.size_bytes},gid={_GRADER_GID},mode=0775,nosuid,nodev"
    return f"size={mount.size_bytes},uid={_LEARNER_UID},gid={_LEARNER_GID},mode=0755,nosuid,nodev"


def resolve_image(image: str, tag: str = DEFAULT_LAB_IMAGE_TAG) -> str:
    """Map a catalog image name to the Docker image reference.

    Content names a Lab_Image by its directory under ``lab-images/`` (``linux-base``, ``python``);
    the bake file tags it ``edu-it/lab-<name>:<tag>``. A value that is not a bare catalog name (it
    has a registry or repository path, a tag, or a digest) is already a Docker reference and is
    returned unchanged, so digest-pinned references keep working.
    """

    if not _CATALOG_IMAGE_NAME.fullmatch(image):
        return image
    if not _IMAGE_TAG.fullmatch(tag):
        raise ValueError(f"invalid lab image tag: {tag!r}")
    return f"{LAB_IMAGE_REPOSITORY_PREFIX}{image}:{tag}"


def to_container_kwargs(
    spec: SandboxSpec, image_tag: str = DEFAULT_LAB_IMAGE_TAG
) -> dict[str, Any]:
    """Translate ``spec`` into keyword arguments for container creation."""

    labels = dict(spec.labels)
    labels[LABEL_MANAGED] = labels.get(LABEL_MANAGED, "1")
    labels[LABEL_SESSION] = spec.session_stable_id

    return {
        "image": resolve_image(spec.image, image_tag),
        "user": LEARNER_UID_GID,
        "network_mode": "none",
        "nano_cpus": spec.cpu_limit * _NANO,
        "mem_limit": spec.memory_limit_bytes,
        "memswap_limit": spec.memory_limit_bytes,
        "pids_limit": spec.pid_limit,
        "read_only": True,
        "tmpfs": {m.path: _tmpfs_options(m) for m in spec.tmpfs_mounts},
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"],
        "privileged": False,
        "init": True,
        "labels": labels,
    }
