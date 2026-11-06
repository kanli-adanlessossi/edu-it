"""``KubernetesSandboxProvider`` (kubernetes-deployment tasks 5.2 and 5.3).

Implements ``provision``, ``destroy``, ``list_sandboxes`` and ``health`` of the
``SandboxProvider`` port on top of :class:`K8sApiPort`. ``exec``, ``copy_in``, ``copy_out`` and
``attach_terminal`` delegate to :class:`k8s_exec.K8sExecAdapter`.

Time comes from the injected ``Clock``. Waiting calls the injected ``sleep`` callable, which
defaults to ``time.sleep`` in production; tests inject a sleeper that advances ``Test_Clock``, so
nothing sleeps on the wall clock. Readiness is polled (the design allows a polling fallback).

Raw API text (status, reason, message) is logged here and never put in an exception message.

Warm pool (Req 1.5 extension point): an optional :class:`WarmPool` is asked first in
``provision``. When it returns a handle that handle is used; the port is unchanged.

Requirements: 1.4, 1.5, 1.6, 1.7, 1.8, 2.11, 2.12, 6.1-6.5, 6.10, 22.1, 22.3, 22.8.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Final

from edu_it_domain import config as _cfg
from edu_it_domain.ports.sandbox import (
    ExecResult,
    HealthStatus,
    ProviderUnavailable,
    ProvisionCapacityError,
    ProvisionError,
    ProvisionTimeout,
    SandboxError,
    SandboxHandle,
    TerminalChannel,
    WarmPool,
)

from edu_it_infrastructure.sandbox.k8s_api import (
    PHASE_FAILED,
    PHASE_PENDING,
    PHASE_RUNNING,
    PHASE_SUCCEEDED,
    K8sApiError,
    K8sApiPort,
    PodView,
)
from edu_it_infrastructure.sandbox.k8s_errors import (
    CreateRejection,
    classify_create_rejection,
    map_api_exception,
)
from edu_it_infrastructure.sandbox.k8s_exec import K8sExecAdapter
from edu_it_infrastructure.sandbox.k8s_pod_spec import K8sSettings, build_lab_pod, pod_name

if TYPE_CHECKING:
    from edu_it_domain.labs.models import SandboxSpec
    from edu_it_domain.ports.clock import Clock

__all__ = [
    "LABEL_DESTROY",
    "LABEL_DESTROY_PENDING",
    "LABEL_MANAGED",
    "LABEL_SESSION",
    "PROVIDER_NAME",
    "READY_POLL_INTERVAL",
    "KubernetesSandboxProvider",
]

_LOG = logging.getLogger(__name__)

PROVIDER_NAME: Final[str] = "kubernetes"
LABEL_MANAGED: Final[str] = "edu-it.managed"
LABEL_SESSION: Final[str] = "edu-it.session"
LABEL_DESTROY: Final[str] = "edu-it.io/destroy"
LABEL_DESTROY_PENDING: Final[str] = "pending"

#: Interval between readiness / absence polls.
READY_POLL_INTERVAL: Final[timedelta] = timedelta(milliseconds=500)

_REJECTION_MESSAGES: Final[Mapping[CreateRejection, str]] = {
    CreateRejection.POD_SECURITY_VIOLATION: "sandbox pod was rejected by pod security",
    CreateRejection.RUNTIME_CLASS_MISSING: "sandbox runtime class is not available",
    CreateRejection.ALREADY_EXISTS: "sandbox pod already exists",
    CreateRejection.OTHER: "sandbox pod could not be created",
}

_NOT_READY_FAILURE = "sandbox pod failed before it became ready"


def _log_api_error(action: str, exc: BaseException) -> None:
    _LOG.warning("kubernetes %s failed: %s", action, exc, exc_info=True)


class KubernetesSandboxProvider:
    """Lifecycle of Lab_Pods through :class:`K8sApiPort`."""

    def __init__(
        self,
        api: K8sApiPort,
        settings: K8sSettings,
        clock: Clock,
        *,
        sleep: Callable[[float], None] = time.sleep,
        warm_pool: WarmPool | None = None,
        provision_timeout: timedelta = _cfg.LAB_PROVISION_TIMEOUT,
        ready_ceiling: timedelta = _cfg.K8S_READY_CEILING,
        destroy_timeout: timedelta = _cfg.LAB_DESTROY_TIMEOUT,
        poll_interval: timedelta = READY_POLL_INTERVAL,
    ) -> None:
        self._api = api
        self._settings = settings
        self._clock = clock
        self._sleep = sleep
        self._warm_pool = warm_pool
        self._ready_timeout = min(provision_timeout, ready_ceiling)
        self._destroy_timeout = destroy_timeout
        self._poll = poll_interval
        self._exec = K8sExecAdapter(api, settings.namespace, clock, sleep=sleep)

    # ------------------------------------------------------------------ provision

    def provision(self, spec: SandboxSpec) -> SandboxHandle:
        """Create the Lab_Pod and wait until it is Ready.

        Raises :class:`ProvisionCapacityError` (quota, or Pending Unschedulable at the timeout),
        :class:`ProvisionTimeout`, :class:`ProvisionError` or :class:`ProviderUnavailable`.
        """

        if self._warm_pool is not None:
            warm = self._warm_pool.acquire(spec)
            if warm is not None:
                return warm

        manifest = build_lab_pod(spec, self._settings)
        name = pod_name(spec.session_stable_id)
        namespace = self._settings.namespace
        session = dict(spec.labels).get(LABEL_SESSION, spec.session_stable_id)
        handle = SandboxHandle(name, spec.session_stable_id, PROVIDER_NAME)

        self._create_or_adopt(namespace, name, manifest, session)
        try:
            self._wait_ready(namespace, name)
        except ProvisionError as exc:
            raise self._cleanup_then(exc, handle) from None
        except Exception as exc:  # unexpected failure after creation: still clean up
            _log_api_error("wait for ready", exc)
            failure = ProvisionError(_NOT_READY_FAILURE)
            raise self._cleanup_then(failure, handle) from exc
        return handle

    def _create_or_adopt(
        self, namespace: str, name: str, manifest: Mapping[str, object], session: str
    ) -> None:
        try:
            self._api.create_pod(namespace, manifest)
            return
        except K8sApiError as exc:
            _LOG.warning(
                "kubernetes pod create rejected: status=%s reason=%s message=%s",
                exc.status,
                exc.reason,
                exc.message,
            )
            kind = classify_create_rejection(exc.status, exc.reason, exc.message)
            if kind is CreateRejection.CAPACITY:
                raise ProvisionCapacityError("sandbox capacity is exhausted") from None
            if kind is not CreateRejection.ALREADY_EXISTS:
                raise ProvisionError(_REJECTION_MESSAGES[kind]) from None
        except Exception as exc:
            _log_api_error("pod create", exc)
            mapped = map_api_exception(exc)
            if isinstance(mapped, ProviderUnavailable):
                raise mapped from None
            raise ProvisionError(_REJECTION_MESSAGES[CreateRejection.OTHER]) from None

        # 409: adopt only a pod that belongs to the same session.
        try:
            existing = self._api.read_pod(namespace, name)
        except Exception as exc:
            _log_api_error("read of existing pod", exc)
            mapped = map_api_exception(exc)
            if isinstance(mapped, ProviderUnavailable):
                raise mapped from None
            raise ProvisionError(_REJECTION_MESSAGES[CreateRejection.ALREADY_EXISTS]) from None
        if existing.labels.get(LABEL_SESSION) != session:
            _LOG.warning("pod %s exists for a different session; not adopting", name)
            raise ProvisionError(_REJECTION_MESSAGES[CreateRejection.ALREADY_EXISTS])
        _LOG.info("adopted existing pod %s for session %s", name, session)

    def _wait_ready(self, namespace: str, name: str) -> None:
        deadline = self._clock.now() + self._ready_timeout
        while True:
            try:
                pod = self._api.read_pod(namespace, name)
            except K8sApiError as exc:
                _log_api_error("read while waiting", exc)
                raise ProvisionError(_NOT_READY_FAILURE) from None
            except Exception as exc:
                _log_api_error("read while waiting", exc)
                mapped = map_api_exception(exc)
                if isinstance(mapped, ProviderUnavailable):
                    raise ProvisionError("sandbox provider is unavailable") from None
                raise ProvisionError(_NOT_READY_FAILURE) from None
            if pod.ready and pod.phase == PHASE_RUNNING and pod.deletion_timestamp is None:
                return
            if pod.phase in (PHASE_FAILED, PHASE_SUCCEEDED) or pod.deletion_timestamp is not None:
                _LOG.warning(
                    "pod %s ended before ready: phase=%s reason=%s message=%s",
                    name,
                    pod.phase,
                    pod.reason,
                    pod.message,
                )
                raise ProvisionError(_NOT_READY_FAILURE)
            remaining = deadline - self._clock.now()
            if remaining <= timedelta(0):
                self._raise_timeout(pod)
            self._sleep(min(self._poll, remaining).total_seconds())

    @staticmethod
    def _raise_timeout(pod: PodView) -> None:
        _LOG.warning(
            "pod %s not ready at timeout: phase=%s reason=%s message=%s",
            pod.name,
            pod.phase,
            pod.reason,
            pod.message,
        )
        if pod.phase == PHASE_PENDING and pod.unschedulable:
            raise ProvisionCapacityError("sandbox capacity is exhausted")
        raise ProvisionTimeout("sandbox did not become ready in time")

    def _cleanup_then(self, error: ProvisionError, handle: SandboxHandle) -> ProvisionError:
        """Destroy the pod and wait until it is absent; attach a partial handle on failure."""

        try:
            self.destroy(handle)
        except SandboxError:
            _LOG.warning("cleanup after failed provisioning of %s incomplete", handle.sandbox_id)
            error.partial_handle = handle
        return error

    # ------------------------------------------------------------------ destroy

    def destroy(self, handle: SandboxHandle) -> None:
        """Delete the pod and wait until it is absent; idempotent.

        On ``LAB_DESTROY_TIMEOUT`` the pod is labelled ``edu-it.io/destroy=pending`` for the
        reaper and ``SandboxError("destroy timeout")`` is raised.
        """

        if handle.provider != PROVIDER_NAME:
            return
        namespace = self._settings.namespace
        name = handle.sandbox_id
        try:
            self._api.delete_pod(namespace, name)
        except K8sApiError as exc:
            if exc.status == 404:
                return
            _log_api_error("pod delete", exc)
            raise SandboxError("sandbox operation failed") from None
        except Exception as exc:
            _log_api_error("pod delete", exc)
            raise map_api_exception(exc) from None

        deadline = self._clock.now() + self._destroy_timeout
        while True:
            try:
                self._api.read_pod(namespace, name)
            except K8sApiError as exc:
                if exc.status == 404:
                    return
                _log_api_error("read while destroying", exc)
                raise SandboxError("sandbox operation failed") from None
            except Exception as exc:
                _log_api_error("read while destroying", exc)
                raise map_api_exception(exc) from None
            remaining = deadline - self._clock.now()
            if remaining <= timedelta(0):
                self._mark_destroy_pending(namespace, name)
                raise SandboxError("destroy timeout")
            self._sleep(min(self._poll, remaining).total_seconds())

    def _mark_destroy_pending(self, namespace: str, name: str) -> None:
        try:
            self._api.label_pod(namespace, name, {LABEL_DESTROY: LABEL_DESTROY_PENDING})
        except Exception as exc:  # best effort; the reaper still lists managed pods
            _log_api_error("destroy-pending label", exc)

    # ------------------------------------------------------------------ inspection

    def health(self, handle: SandboxHandle) -> HealthStatus:
        """Healthy only when the pod is Running with every container ready."""

        try:
            pod = self._api.read_pod(self._settings.namespace, handle.sandbox_id)
        except K8sApiError as exc:
            if exc.status == 404:
                return HealthStatus(False, "gone")
            _log_api_error("health read", exc)
            raise SandboxError("sandbox operation failed") from None
        except Exception as exc:
            _log_api_error("health read", exc)
            raise map_api_exception(exc) from None
        if pod.deletion_timestamp is not None:
            return HealthStatus(False, "terminating")
        if pod.phase == PHASE_RUNNING and pod.ready:
            return HealthStatus(True)
        return HealthStatus(False, (pod.reason or pod.phase).lower())

    def list_sandboxes(self) -> Sequence[SandboxHandle]:
        """Handles of managed pods only (label ``edu-it.managed=1``)."""

        try:
            pods = self._api.list_pods(self._settings.namespace, f"{LABEL_MANAGED}=1")
        except Exception as exc:
            _log_api_error("pod list", exc)
            raise map_api_exception(exc) from None
        return tuple(
            SandboxHandle(pod.name, pod.labels.get(LABEL_SESSION, ""), PROVIDER_NAME)
            for pod in pods
        )

    # ------------------------------------------------------------------ exec, copy, terminal
    # Implemented in ``k8s_exec.K8sExecAdapter`` (task 5.3).

    def exec(
        self,
        handle: SandboxHandle,
        command: Sequence[str],
        *,
        as_user: str,
        env: Mapping[str, str],
        cwd: str,
        timeout_s: float | None = None,
    ) -> ExecResult:
        """Run one command; kill its process group on ``timeout_s`` and report ``timed_out``.

        Raises :class:`SandboxGone` when the pod is gone and :class:`SandboxError` for an
        unknown ``as_user``.
        """

        return self._exec.exec(
            handle, command, as_user=as_user, env=env, cwd=cwd, timeout_s=timeout_s
        )

    def attach_terminal(self, handle: SandboxHandle) -> TerminalChannel:
        """Open a TTY in the ``learner`` container; :class:`SandboxGone` if the pod is gone."""

        return self._exec.attach_terminal(handle)

    def copy_in(
        self,
        handle: SandboxHandle,
        source_ro: Path,
        dest: str,
        *,
        as_user: str | None = None,
    ) -> None:
        """Copy a read-only host source into the pod (``/grader`` through the owner identity)."""

        self._exec.copy_in(handle, source_ro, dest, as_user=as_user)

    def copy_out(self, handle: SandboxHandle, source: str, dest: Path) -> None:
        """Copy ``source`` out of the pod to the host path ``dest``."""

        self._exec.copy_out(handle, source, dest)
