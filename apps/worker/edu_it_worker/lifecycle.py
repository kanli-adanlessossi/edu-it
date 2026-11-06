"""Worker startup checks and graceful shutdown (kubernetes-deployment task 8.8).

Startup (:func:`run_startup_checks`) runs before the Worker consumes a job and refuses to
start, so the process exits non-zero, when:

* ``SANDBOX_PROVIDER`` is not supported (``parse_sandbox_backend``, Req 1.3);
* the Lab_RuntimeClass resolves to plain ``runc`` in production (``runtime_guard``, Req 2.10).
  Outside production ``runc`` logs the Runc_Warning once (Req 2.9);
* a required secret is missing (``check_required_secrets``, Req 23.2). The check runs where
  the secrets are mounted: with the Kubernetes provider, or when ``EDU_IT_SECRETS_ROOT`` is set.

Shutdown (:class:`ShutdownCoordinator`) handles SIGTERM (Req 14.10, 14.11): it stops consuming,
lets in-flight jobs finish, and at ``grace - 5 s`` after the signal re-queues every job whose
handler has not returned. The payload is moved untouched, so the attempt count is unchanged.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING, Final, Protocol

from edu_it_domain import config as _config
from edu_it_domain.labs.k8s_rules import runtime_guard
from edu_it_domain.labs.k8s_types import RuntimeDecision
from edu_it_infrastructure.composition import (
    CompositionSettings,
    SandboxBackend,
    parse_sandbox_backend,
)
from edu_it_infrastructure.secrets import (
    DEFAULT_SECRETS_ROOT,
    FileSecretSource,
    check_required_secrets,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from edu_it_infrastructure.secrets import SecretSource

__all__ = [
    "REQUIRED_SECRET_KEYS",
    "ShutdownCoordinator",
    "StartupRefused",
    "WorkerRuntime",
    "run_startup_checks",
    "termination_grace_seconds",
]

_LOG = logging.getLogger(__name__)

#: Secret keys (``<mounted secret>/<key>``) the Worker cannot run without.
REQUIRED_SECRET_KEYS: Final[tuple[str, ...]] = ("database/url", "redis/url")


class StartupRefused(RuntimeError):
    """The Worker must not start; the entrypoint logs the reason and exits non-zero."""


def run_startup_checks(
    env: Mapping[str, str], secrets: SecretSource | None = None
) -> CompositionSettings:
    """Validate the start-up conditions and return the settings; raise to refuse the start.

    Raises ``ProviderSelectionError`` for an unsupported ``SANDBOX_PROVIDER`` and
    :class:`StartupRefused` for a production ``runc`` runtime or a missing secret.
    """
    backend = parse_sandbox_backend(env.get("SANDBOX_PROVIDER", SandboxBackend.FAKE.value))
    settings = CompositionSettings.from_env(env)

    if backend is SandboxBackend.KUBERNETES:
        decision = runtime_guard(settings.sandbox_runtime_kind, settings.environment.value)
        if decision is RuntimeDecision.REFUSE:
            raise StartupRefused(
                "refusing to start: the lab RuntimeClass resolves to plain runc "
                "(SANDBOX_RUNTIME_KIND=runc) in production; use gVisor or Kata"
            )
        if decision is RuntimeDecision.WARN:
            _LOG.warning(
                "Runc_Warning: lab pods run on plain runc (SANDBOX_RUNTIME_KIND=runc), "
                "which does not isolate learner code; use it for development only"
            )

    secrets_root = env.get("EDU_IT_SECRETS_ROOT")
    if backend is SandboxBackend.KUBERNETES or secrets_root:
        source = (
            secrets
            if secrets is not None
            else FileSecretSource(secrets_root or DEFAULT_SECRETS_ROOT)
        )
        missing = check_required_secrets(source, REQUIRED_SECRET_KEYS)
        if missing:
            raise StartupRefused(
                "refusing to start: required secrets are missing: " + ", ".join(missing)
            )
    return settings


def termination_grace_seconds(env: Mapping[str, str]) -> int:
    """``WORKER_TERMINATION_GRACE_SECONDS`` (set by the Chart), or the configured default."""
    raw = env.get("WORKER_TERMINATION_GRACE_SECONDS", "")
    try:
        value = int(raw)
    except ValueError:
        return _config.WORKER_TERMINATION_GRACE_DEFAULT_SECONDS
    return value if value > 0 else _config.WORKER_TERMINATION_GRACE_DEFAULT_SECONDS


class WorkerRuntime(Protocol):
    """What the coordinator needs from the running workers."""

    def stop_consuming(self) -> None:
        """Stop taking new jobs; running handlers are not interrupted."""
        ...

    def in_flight(self) -> bool:
        """Whether a handler is still running."""
        ...

    def requeue_in_flight(self) -> int:
        """Put every job whose handler has not returned back on its queue; return the count."""
        ...


class ShutdownCoordinator:
    """Drain the Worker on SIGTERM within ``grace - 5 s``."""

    def __init__(
        self,
        runtime: WorkerRuntime,
        *,
        grace_s: float,
        margin_s: float = _config.WORKER_SHUTDOWN_MARGIN_SECONDS,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        poll_s: float = 0.1,
    ) -> None:
        """Bind the runtime and the time sources (injected so tests never wait)."""
        self._runtime = runtime
        self._grace = grace_s
        self._margin = margin_s
        self._monotonic = monotonic
        self._sleep = sleep
        self._poll = poll_s
        self._requested = threading.Event()
        self._requested_at = 0.0
        self._done = threading.Event()
        self._requeued = 0

    @property
    def requested(self) -> bool:
        """Whether a shutdown has been requested."""
        return self._requested.is_set()

    def request(self, *_signal_args: object) -> None:
        """Start the drain on its own thread; idempotent and safe in a signal handler."""
        if self._requested.is_set():
            return
        self._requested_at = self._monotonic()
        self._requested.set()
        threading.Thread(target=self._run, name="worker-drain", daemon=True).start()

    def wait(self, timeout: float | None = None) -> int:
        """Wait for the drain to finish; return how many jobs were re-queued."""
        self._done.wait(timeout)
        return self._requeued

    def drain(self) -> int:
        """Stop consuming, wait for in-flight jobs, re-queue the ones left at the deadline."""
        started = self._requested_at if self._requested.is_set() else self._monotonic()
        deadline = started + max(self._grace - self._margin, 0.0)
        self._runtime.stop_consuming()
        while self._runtime.in_flight() and self._monotonic() < deadline:
            self._sleep(self._poll)
        if not self._runtime.in_flight():
            return 0
        requeued = self._runtime.requeue_in_flight()
        _LOG.warning("shutdown deadline reached; re-queued %d in-flight job(s)", requeued)
        return requeued

    def _run(self) -> None:
        try:
            self._requeued = self.drain()
        except Exception:
            _LOG.exception("worker drain failed")
        finally:
            self._done.set()
