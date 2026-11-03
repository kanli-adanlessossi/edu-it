"""Replay transport: answers from recorded fixtures (ai-content Req 2.3, 2.4).

Owns no socket and imports no network module. Attempt ``n`` of a request
returns the ``n``-th recorded attempt for its key, so retries replay in order.
"""

from __future__ import annotations

from edu_it_domain.ai.fixtures import fixture_key
from edu_it_domain.ports.llm_provider import MissingFixtureError

from edu_it_infrastructure.llm.fixture_store import FixtureStore
from edu_it_infrastructure.llm.transport import ModelTransport, RawCompletion, TransportRequest

__all__ = ["ReplayTransport"]


class ReplayTransport(ModelTransport):
    """A :class:`ModelTransport` backed by a :class:`FixtureStore`."""

    def __init__(self, store: FixtureStore) -> None:
        self._store = store

    def complete(self, request: TransportRequest) -> RawCompletion:
        key = fixture_key(request.prompt, request.input)
        fixture = self._store.read(request.prompt, request.input)
        if fixture is None or request.attempt > len(fixture.attempts):
            raise MissingFixtureError(key)
        recorded = fixture.attempts[request.attempt - 1]
        return RawCompletion(
            text=recorded.text,
            model=recorded.model,
            input_tokens=recorded.input_tokens,
            output_tokens=recorded.output_tokens,
            cost_cents=0,
        )
