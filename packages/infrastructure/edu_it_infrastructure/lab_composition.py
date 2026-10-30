"""Real (PostgreSQL + Redis) adapters shared by the api and worker lab compositions.

:func:`build_lab_ports` assembles the lab repositories, the Redis task queue, the Redis
session store and the Redis idempotency store over ``DATABASE_URL`` / ``REDIS_URL``. It
always wires the read-only :class:`~edu_it_infrastructure.clock.System_Clock`: api and
worker are separate processes, so a per-process controllable ``Test_Clock`` would give
them different notions of "now" and never advance on its own (Req 6.3 keeps the
controllable clock out of any real composition).

Clients and engines are created lazily, so building the ports contacts no datastore.
"""

from __future__ import annotations

from dataclasses import dataclass

from edu_it_domain.ports.clock import Clock
from edu_it_domain.ports.id_generator import IdGenerator
from edu_it_domain.ports.lab_sessions import GradingRunRepository, LabSessionRepository
from edu_it_domain.ports.mastery import MasteryRepository
from edu_it_domain.ports.session_store import SessionStore
from edu_it_domain.ports.task_queue import TaskQueue

from edu_it_infrastructure.adapters.redis_support import redis_from_url
from edu_it_infrastructure.adapters.session_store import Redis_SessionStore
from edu_it_infrastructure.adapters.task_queue import Redis_TaskQueue
from edu_it_infrastructure.clock import System_Clock
from edu_it_infrastructure.composition import CompositionSettings
from edu_it_infrastructure.id_generator import Uuid_IdGenerator
from edu_it_infrastructure.persistence.lab_repositories import (
    Postgres_GradingRunRepository,
    Postgres_LabSessionRepository,
)
from edu_it_infrastructure.persistence.learning_repositories import Postgres_MasteryRepository

__all__ = ["LabPorts", "build_lab_ports"]


@dataclass(frozen=True, slots=True)
class LabPorts:
    """The real adapters the lab use cases are composed from."""

    clock: Clock
    ids: IdGenerator
    sessions: LabSessionRepository
    runs: GradingRunRepository
    mastery: MasteryRepository
    queue: TaskQueue
    session_store: SessionStore
    redis: object
    """The shared Redis client (typed ``object`` to keep ``redis`` out of this signature)."""


def build_lab_ports(settings: CompositionSettings | None = None) -> LabPorts:
    """Assemble the real lab adapters from the environment-driven settings."""
    settings = settings if settings is not None else CompositionSettings.from_env()
    clock = System_Clock()
    client = redis_from_url(settings.redis_url)
    return LabPorts(
        clock=clock,
        ids=Uuid_IdGenerator(),
        sessions=Postgres_LabSessionRepository(settings.database_url),
        runs=Postgres_GradingRunRepository(settings.database_url),
        mastery=Postgres_MasteryRepository(settings.database_url),
        queue=Redis_TaskQueue(client),
        session_store=Redis_SessionStore(client, clock),
        redis=client,
    )
