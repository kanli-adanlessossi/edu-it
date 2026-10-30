"""``Repository_LabCheck``: the real ``LabCheck`` adapter (hands-on-labs Req 19.3).

Answers the Mastered gate's two questions from the lab catalog and the learner's
recorded progress:

- :meth:`covers` is true when a non-archived lab lists the concept.
- :meth:`passed` is true when some covering lab has every required task completed
  by the learner, or the learner's mastery of the concept carries a zero-delta
  ``lab_passed`` marker event.

Archived labs never cover a concept (the catalog's ``labs_covering`` excludes
them). A lab with no required tasks cannot be passed by completion alone.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from edu_it_domain.labs.progression import LAB_PASSED_SOURCE

if TYPE_CHECKING:
    from edu_it_domain.labs.models import StableId
    from edu_it_domain.ports.lab_catalog import LabCatalog
    from edu_it_domain.ports.lab_sessions import LabCompletionReader
    from edu_it_domain.ports.mastery import MasteryRepository

__all__ = ["Repository_LabCheck"]


class Repository_LabCheck:
    """A :class:`~edu_it_domain.ports.lab_check.LabCheck` over lab repositories."""

    def __init__(
        self,
        labs: LabCatalog,
        completions: LabCompletionReader,
        mastery: MasteryRepository,
    ) -> None:
        self._labs = labs
        self._completions = completions
        self._mastery = mastery

    def covers(self, concept_id: StableId) -> bool:
        return bool(self._labs.labs_covering(concept_id))

    def passed(self, concept_id: StableId, user_id: StableId) -> bool:
        covering = self._labs.labs_covering(concept_id)
        if not covering:
            return False
        record = self._mastery.get(user_id, concept_id)
        if record is not None and any(e.source == LAB_PASSED_SOURCE for e in record.events):
            return True
        done = {c.task_id for c in self._completions.list_task_completions(user_id)}
        for lab in covering:
            required = {t.task_id for t in lab.tasks if t.required}
            if required and required <= done:
                return True
        return False
