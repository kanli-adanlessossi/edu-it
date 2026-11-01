"""The ``SandboxProvider`` port and its value types (hands-on-labs Req 1.1, 1.5, 1.9).

All sandbox technology sits behind this port; signatures follow
``docs/LABS-DESIGN.md`` (D4), including the additive amendments (``list_sandboxes``,
optional ``timeout_s`` on ``exec``, optional ``as_user`` on ``copy_in``, a defined
``ExecResult`` and a synchronous ``TerminalChannel``). Docker, Kubernetes and
the fake are infrastructure implementations; this module imports only the
standard library and domain models.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    from edu_it_domain.labs.models import SandboxSpec

__all__ = [
    "ExecResult",
    "HealthStatus",
    "ProviderUnavailable",
    "ProvisionError",
    "ProvisionTimeout",
    "SandboxError",
    "SandboxGone",
    "SandboxHandle",
    "SandboxProvider",
    "TerminalChannel",
    "WarmPool",
]


@dataclass(frozen=True, slots=True)
class SandboxHandle:
    """Opaque reference to one sandbox."""

    sandbox_id: str
    """Provider-specific id, opaque to callers."""

    session_stable_id: str
    """The owning Lab_Session."""

    provider: str
    """Provider name, e.g. ``docker``, ``fake`` or ``kubernetes``."""


@dataclass(frozen=True, slots=True)
class ExecResult:
    """Result of one ``exec``. Output is data for the grading harness only and
    is never forwarded to learners."""

    exit_code: int
    """Process exit code; 137 when killed by timeout."""

    stdout: bytes
    stderr: bytes
    timed_out: bool
    """True when ``timeout_s`` expired and the command was killed."""


@dataclass(frozen=True, slots=True)
class HealthStatus:
    """Provider-reported health of one sandbox."""

    healthy: bool
    detail: str = ""


class SandboxError(Exception):
    """Base class of sandbox provider failures."""


class ProvisionError(SandboxError):
    """Provisioning failed. ``partial_handle`` is set when the provider could
    not clean up everything it created, so the caller can destroy it."""

    def __init__(self, message: str = "", *, partial_handle: SandboxHandle | None = None) -> None:
        super().__init__(message)
        self.partial_handle = partial_handle


class ProvisionTimeout(ProvisionError):
    """Provisioning exceeded its time bound."""


class ProviderUnavailable(SandboxError):
    """The provider (e.g. the Docker daemon) is unreachable within its bound.

    Changes no session or sandbox state (Req 1.9).
    """


class SandboxGone(SandboxError):
    """The sandbox no longer exists, or a terminal channel is closed."""


@runtime_checkable
class TerminalChannel(Protocol):
    """Synchronous (blocking) TTY channel to a sandbox. Never persisted."""

    def read(self, max_bytes: int, timeout_s: float | None = None) -> bytes:
        """Return up to ``max_bytes``; empty bytes on timeout.

        Raises :class:`SandboxGone` when the channel is closed.
        """
        ...

    def write(self, data: bytes) -> None:
        """Send ``data`` to the TTY."""
        ...

    def resize(self, rows: int, cols: int) -> None:
        """Resize the TTY."""
        ...

    def close(self) -> None:
        """Close the channel; idempotent."""
        ...


@runtime_checkable
class SandboxProvider(Protocol):
    """Port for creating, driving and destroying per-session sandboxes."""

    def provision(self, spec: SandboxSpec) -> SandboxHandle:
        """Create the sandbox.

        Raises :class:`ProvisionError`, :class:`ProvisionTimeout` or
        :class:`ProviderUnavailable`.
        """
        ...

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
        """Run one command; kill it on ``timeout_s`` expiry and report ``timed_out``.

        Raises :class:`SandboxGone` on a destroyed sandbox.
        """
        ...

    def attach_terminal(self, handle: SandboxHandle) -> TerminalChannel:
        """Open a TTY channel. Raises :class:`SandboxGone` on a destroyed sandbox."""
        ...

    def copy_in(
        self,
        handle: SandboxHandle,
        source_ro: Path,
        dest: str,
        *,
        as_user: str | None = None,
    ) -> None:
        """Copy a read-only host source into the sandbox, owned by ``as_user`` if given."""
        ...

    def copy_out(self, handle: SandboxHandle, source: str, dest: Path) -> None:
        """Copy files out of the sandbox."""
        ...

    def destroy(self, handle: SandboxHandle) -> None:
        """Tear the sandbox down; idempotent."""
        ...

    def health(self, handle: SandboxHandle) -> HealthStatus:
        """Report the sandbox health."""
        ...

    def list_sandboxes(self) -> Sequence[SandboxHandle]:
        """Return handles of managed sandboxes only (used by the reaper)."""
        ...


@runtime_checkable
class WarmPool(Protocol):
    """Warm-pool extension point (Req 1.5); no implementation ships."""

    def acquire(self, spec: SandboxSpec) -> SandboxHandle | None:
        """Return a pre-provisioned sandbox for ``spec``, or ``None`` when none is available."""
        ...
