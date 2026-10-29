"""The ``IdGenerator`` port: minting immutable public ``stable_id`` values (Req 1.1).

Use cases create enrollments, quiz attempts, and project submissions, each
addressed by an opaque ``stable_id`` (D3 section 1.2). Minting one is a source of
nondeterminism like the clock, so it sits behind a port: production wires a
random-UUID generator and tests wire a deterministic sequence.

Framework-free :class:`typing.Protocol` (Req 1.8).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

__all__ = ["IdGenerator"]


@runtime_checkable
class IdGenerator(Protocol):
    """Port for generating new, unique ``stable_id`` values."""

    def new_id(self) -> str:
        """Return a new unique, opaque ``stable_id``."""
        ...
