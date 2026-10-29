"""The ``LabCheck`` extension-point port (Req 1.4, 5.6).

Reaching :attr:`~edu_it_domain.learning.models.MasteryState.MASTERED` requires,
where a lab covers the concept, a passed lab check (Req 5.6, D3 §7.1). The
mastery service consults that lab-check path through this port — the ``LabCheck``
extension point of the learning design ("Labs lab-check path"): the gate is
satisfied when no lab covers the concept, and otherwise when the lab-covered
check has passed for the learner.

The port exposes the two questions the Mastered gate asks:

- :meth:`LabCheck.covers` — does any lab cover this concept at all? When it does
  not, the lab-check condition is vacuously satisfied (Req 1.4).
- :meth:`LabCheck.passed` — has the lab-covered check passed for this learner?
  Consulted only when a lab covers the concept.

Like the sibling ports this is a framework-free :class:`typing.Protocol`
(``@runtime_checkable``, standard library + domain imports only, no web/API
framework per Req 1.8). The application layer depends on the protocol; the
infrastructure layer supplies a ``Fake_LabCheck`` (configurable coverage and
pass result, task 1.4) so tests drive the Mastered gate without a runnable lab,
while the api and web surfaces expose no lab in this phase (Req 15.7, 19.7). As
an extension point any lab-check implementation satisfies the port by structural
typing.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from edu_it_domain.learning.models import StableId

__all__ = [
    "LabCheck",
]


@runtime_checkable
class LabCheck(Protocol):
    """Port for the Mastered-gate lab-check path (Req 1.4, 5.6)."""

    def covers(self, concept_id: StableId) -> bool:
        """Return whether any lab covers the concept ``concept_id``.

        When no lab covers the concept this returns ``False`` and the lab-check
        condition of the Mastered gate is vacuously satisfied, so mastery may
        reach :attr:`~edu_it_domain.learning.models.MasteryState.MASTERED`
        without any lab result (Req 1.4). When it returns ``True`` the gate then
        consults :meth:`passed`.
        """
        ...

    def passed(self, concept_id: StableId, user_id: StableId) -> bool:
        """Return whether the lab covering ``concept_id`` has passed for ``user_id``.

        Consulted only when :meth:`covers` is ``True``: the Mastered gate
        requires this to be ``True`` for the learner before the concept may reach
        :attr:`~edu_it_domain.learning.models.MasteryState.MASTERED` (Req 5.6).
        Both the concept and the learner are addressed by their ``stable_id``, so
        the domain never sees an internal key.
        """
        ...
