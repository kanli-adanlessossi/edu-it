"""A fake in-memory ``LabCatalog`` (hands-on-labs Req 1.2)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from edu_it_domain.labs.models import StableId
    from edu_it_domain.ports.lab_catalog import LabDefinition

__all__ = ["Fake_LabCatalog"]


class Fake_LabCatalog:
    """Serves the :class:`LabDefinition` records it was given."""

    def __init__(self, labs: Iterable[LabDefinition] = ()) -> None:
        self._labs: dict[StableId, LabDefinition] = {}
        for lab in labs:
            self.add(lab)

    def add(self, lab: LabDefinition) -> None:
        self._labs[lab.lab_id] = lab

    def get(self, lab_id: StableId) -> LabDefinition | None:
        return self._labs.get(lab_id)

    def list_labs(self) -> Sequence[LabDefinition]:
        return tuple(lab for lab in self._labs.values() if not lab.archived)

    def labs_covering(self, concept_id: StableId) -> Sequence[LabDefinition]:
        return tuple(lab for lab in self.list_labs() if concept_id in lab.concept_ids)
