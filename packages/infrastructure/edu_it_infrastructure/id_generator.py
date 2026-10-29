"""The production random-UUID ``IdGenerator`` adapter (Req 1.1).

:class:`Uuid_IdGenerator` implements the domain
:class:`~edu_it_domain.ports.id_generator.IdGenerator` port with random
(version 4) UUIDs, giving every enrollment, quiz attempt, and project submission
an opaque, unguessable ``stable_id`` that reveals no internal numeric key.
"""

from __future__ import annotations

import uuid

from edu_it_domain.ports.id_generator import IdGenerator

__all__ = ["Uuid_IdGenerator"]


class Uuid_IdGenerator(IdGenerator):
    """An :class:`IdGenerator` returning random UUID4 strings."""

    def new_id(self) -> str:
        """Return a new random UUID4 as a string."""
        return str(uuid.uuid4())
