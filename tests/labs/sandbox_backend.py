"""Backend selection for the real-sandbox lab suites (kubernetes-deployment task 5.4).

The labs isolation, grading-isolation, tamper-resistance and Check-abuse suites run against a
real sandbox backend. They build their provider here, so the same test bodies run with
``DockerSandboxProvider`` (default) or ``KubernetesSandboxProvider`` substituted:

- ``EDU_IT_LAB_SUITE_PROVIDER`` is ``docker`` (default) or ``kubernetes``.
- For ``kubernetes``, ``EDU_IT_LAB_SUITE_K8S_API`` names a ``module:callable`` that returns a
  ``K8sApiPort`` for the kind cluster (supplied by tests/k8s, task 6.22), and
  ``EDU_IT_LAB_SUITE_K8S_NAMESPACE`` (default ``labs``) names the Labs_Namespace.

Readiness is decided here, so the suites keep exactly one skip condition: the same
``skipif(not provider_ready(IMAGE))`` they had for the Docker daemon. A Kubernetes run therefore
skips precisely where the Docker run skips (no backend reachable) and adds no skip of its own.
The real-cluster run is task 6.23. Nothing here is used by the fake-API run.
"""

from __future__ import annotations

import importlib
import os
from typing import Final

from edu_it_domain.ports.sandbox import SandboxProvider

__all__ = [
    "BACKEND_ENV",
    "GRADER_UID",
    "K8S_API_ENV",
    "K8S_NAMESPACE_ENV",
    "LEARNER_UID",
    "OWNER_UID",
    "backend_name",
    "is_kubernetes",
    "make_provider",
    "provider_ready",
    "skip_reason",
]

BACKEND_ENV: Final[str] = "EDU_IT_LAB_SUITE_PROVIDER"
K8S_API_ENV: Final[str] = "EDU_IT_LAB_SUITE_K8S_API"
K8S_NAMESPACE_ENV: Final[str] = "EDU_IT_LAB_SUITE_K8S_NAMESPACE"

#: UIDs of the identities per backend: Docker image users, and pod layout B (ADR 0009), where
#: ``root`` of ``as_user`` is the non-root ``owner`` container.
LEARNER_UID: Final[dict[str, int]] = {"docker": 1000, "kubernetes": 10000}
GRADER_UID: Final[dict[str, int]] = {"docker": 1001, "kubernetes": 10001}
OWNER_UID: Final[dict[str, int]] = {"docker": 0, "kubernetes": 10002}

_BACKENDS: Final[tuple[str, ...]] = ("docker", "kubernetes")


def backend_name() -> str:
    """The selected backend; an unknown value is an error, never a silent fallback."""

    name = os.environ.get(BACKEND_ENV, "docker")
    if name not in _BACKENDS:
        raise ValueError(f"{BACKEND_ENV}={name!r}; expected one of {_BACKENDS}")
    return name


def is_kubernetes() -> bool:
    return backend_name() == "kubernetes"


def _k8s_api() -> object:
    target = os.environ.get(K8S_API_ENV, "")
    module_name, sep, attr = target.partition(":")
    if not sep or not module_name or not attr:
        raise LookupError(f"{K8S_API_ENV} must look like 'module:callable'")
    return getattr(importlib.import_module(module_name), attr)()


def provider_ready(image: str) -> bool:
    """True when the selected backend is reachable and (for Docker) has ``image`` locally."""

    if is_kubernetes():
        try:
            api = _k8s_api()
            api.list_pods(os.environ.get(K8S_NAMESPACE_ENV, "labs"))  # type: ignore[attr-defined]
        except Exception:
            return False
        return True
    try:
        import docker

        client = docker.from_env(timeout=3)
        client.ping()
        client.images.get(image)
    except Exception:
        return False
    return True


def skip_reason() -> str:
    return (
        "no Kubernetes cluster for the lab suites"
        if is_kubernetes()
        else "no Docker daemon or lab image"
    )


def make_provider() -> SandboxProvider:
    """Build the selected provider."""

    if is_kubernetes():
        from edu_it_infrastructure.clock import System_Clock
        from edu_it_infrastructure.composition import CompositionSettings, build_sandbox_provider

        settings = CompositionSettings.from_env(
            {
                **os.environ,
                "SANDBOX_PROVIDER": "kubernetes",
                "K8S_LABS_NAMESPACE": os.environ.get(K8S_NAMESPACE_ENV, "labs"),
            }
        )
        return build_sandbox_provider(
            settings,
            clock=System_Clock(),
            k8s_api=_k8s_api(),  # type: ignore[arg-type]
        )
    from edu_it_infrastructure.sandbox.docker_provider import DockerSandboxProvider

    return DockerSandboxProvider()
