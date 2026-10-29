"""The configurable ``LabCheck`` fake (Req 1.4, 5.6).

:class:`Fake_LabCheck` implements the domain
:class:`~edu_it_domain.ports.lab_check.LabCheck` extension-point port entirely
in process, with no runnable lab. It exists so the mastery service can drive the
Mastered-gate lab-check path (Req 5.6) without a lab environment while the api
and web surfaces expose no lab in this phase (Req 15.7, 19.7).

Both questions the gate asks are configurable per concept:

- :meth:`covers` — whether any lab covers a concept at all. When no lab covers
  the concept the lab-check condition is vacuously satisfied, so mastery may
  reach :attr:`~edu_it_domain.learning.models.MasteryState.MASTERED` without any
  lab result (Req 1.4).
- :meth:`passed` — whether the lab-covered check has passed for a learner,
  consulted only when :meth:`covers` is ``True`` (Req 5.6).

The fake is substitutable for a future lab-check adapter without changing domain
code: it satisfies the same ``LabCheck`` protocol by structural typing (Req 5.2),
imports only the domain, and holds no framework dependency.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from edu_it_domain.learning.models import StableId
from edu_it_domain.ports.lab_check import LabCheck

__all__ = ["Fake_LabCheck"]


class Fake_LabCheck(LabCheck):
    """A :class:`LabCheck` with configurable coverage and pass results.

    Satisfies the :class:`~edu_it_domain.ports.lab_check.LabCheck` protocol, so
    the mastery service consults the Mastered-gate lab-check path exactly as it
    would a real lab (Req 5.2). Which concepts a lab covers, and which
    ``(concept, learner)`` checks have passed, are both set by the caller.
    """

    __test__ = False

    def __init__(
        self,
        *,
        covered: Iterable[StableId] | None = None,
        passed: Mapping[StableId, Iterable[StableId]] | None = None,
    ) -> None:
        """Create a lab check with configurable coverage and pass results.

        ``covered`` is the set of concept ``stable_id`` a lab covers; a concept
        not in it has no lab and so satisfies the gate vacuously (Req 1.4).
        ``passed`` maps a covered concept's ``stable_id`` to the learners (by
        ``stable_id``) who have passed its lab check (Req 5.6).
        """
        self._covered: set[StableId] = set(covered) if covered is not None else set()
        self._passed: dict[StableId, set[StableId]] = (
            {concept_id: set(users) for concept_id, users in passed.items()}
            if passed is not None
            else {}
        )

    def covers(self, concept_id: StableId) -> bool:
        """Return whether a lab covers ``concept_id`` (Req 1.4).

        ``False`` when no lab covers the concept, in which case the Mastered
        gate's lab-check condition is vacuously satisfied.
        """
        return concept_id in self._covered

    def passed(self, concept_id: StableId, user_id: StableId) -> bool:
        """Return whether the lab covering ``concept_id`` has passed for ``user_id``.

        Only meaningful when :meth:`covers` is ``True`` for the concept; the
        Mastered gate requires this to be ``True`` before the concept may reach
        :attr:`~edu_it_domain.learning.models.MasteryState.MASTERED` (Req 5.6).
        """
        return user_id in self._passed.get(concept_id, set())

    def set_covered(self, concept_id: StableId, *, covered: bool) -> None:
        """Set whether a lab covers ``concept_id`` (Req 1.4)."""
        if covered:
            self._covered.add(concept_id)
        else:
            self._covered.discard(concept_id)

    def set_passed(self, concept_id: StableId, user_id: StableId, *, passed: bool) -> None:
        """Set whether ``user_id`` has passed the lab check for ``concept_id`` (Req 5.6)."""
        if passed:
            self._passed.setdefault(concept_id, set()).add(user_id)
        else:
            self._passed.get(concept_id, set()).discard(user_id)
