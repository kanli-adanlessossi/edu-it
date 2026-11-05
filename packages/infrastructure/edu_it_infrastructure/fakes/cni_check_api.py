"""In-memory :class:`CniCheckApi` for the CNI check tests (kubernetes-deployment task 12.8).

Models a cluster whose CNI either enforces NetworkPolicy or ignores it. Time is a counter that
``sleep`` advances, so no test waits in real time.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from edu_it_infrastructure.cni_check import CniCheckError, ProbePod

__all__ = ["FakeCniCheckApi"]


class FakeCniCheckApi:
    """Fake cluster.

    ``enforcing``: connections follow the created policies. When false every connection works.
    ``ready_after_s``: seconds on the fake clock before probe pods report Ready (``None``: never).
    ``reject_pods`` / ``reject_policies``: creation raises :class:`CniCheckError`.
    """

    def __init__(
        self,
        *,
        enforcing: bool = True,
        ready_after_s: float | None = 0.0,
        reject_pods: bool = False,
        reject_policies: bool = False,
    ) -> None:
        self.enforcing = enforcing
        self.ready_after_s = ready_after_s
        self.reject_pods = reject_pods
        self.reject_policies = reject_policies
        self.now = 0.0
        self.pods: dict[str, dict[str, Any]] = {}
        self.policies: dict[str, dict[str, Any]] = {}
        self.created_pod_names: list[str] = []
        self._created_at: dict[str, float] = {}
        self._ips: dict[str, str] = {}
        self._next_ip = 0

    # fake clock ---------------------------------------------------------------------
    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds

    # CniCheckApi ----------------------------------------------------------------------
    def create_pod(self, namespace: str, manifest: Mapping[str, object]) -> None:
        if self.reject_pods:
            raise CniCheckError("create pod failed: 403 Forbidden")
        name = str(manifest["metadata"]["name"])  # type: ignore[index]
        self.pods[name] = dict(manifest)
        self.created_pod_names.append(name)
        self._created_at[name] = self.now
        self._next_ip += 1
        self._ips[name] = f"10.0.0.{self._next_ip}"

    def create_network_policy(self, namespace: str, manifest: Mapping[str, object]) -> None:
        if self.reject_policies:
            raise CniCheckError("create NetworkPolicy failed: 403 Forbidden")
        self.policies[str(manifest["metadata"]["name"])] = dict(manifest)  # type: ignore[index]

    def read_pod(self, namespace: str, name: str) -> ProbePod | None:
        if name not in self.pods:
            return None
        ready = (
            self.ready_after_s is not None
            and self.now - self._created_at[name] >= self.ready_after_s
        )
        return ProbePod(name, ready, self._ips[name] if ready else None)

    def delete_pod(self, namespace: str, name: str) -> None:
        self.pods.pop(name, None)

    def delete_network_policy(self, namespace: str, name: str) -> None:
        self.policies.pop(name, None)

    def can_connect(self, ip: str, port: int, timeout_s: float) -> bool:
        if not self.enforcing:
            return True
        name = next((n for n, address in self._ips.items() if address == ip), None)
        if name is None or name not in self.pods:
            return False
        role = self.pods[name]["metadata"]["labels"]["edu-it.io/cni-probe"]
        return role == "allowed" and "cni-check-allow" in self.policies
